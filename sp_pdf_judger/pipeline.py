from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from dataclasses import fields
from datetime import date
from pathlib import Path

import fitz

from .config import FAIL_LABEL, HOLD_LABEL, PASS_LABEL
from .domain_details import DomainDetailProfile, DomainDetailStore
from .extractor import extract_records
from .hierarchy import build_document_tree
from .judgement import JudgeEngine
from .llm import ClovaJudgeClient
from .permit_pdf_store import PermitPdfStore
from .preview import render_first_page
from .rag import UcumRagStore
from .manufacturing_info_validator import (
    _norm_match,
    collect_manufacturing_info_occurrences,
    extract_summary_items_from_records,
)
from .schemas import Evaluation, ProcessingResult, Summary
from .sp_table_result_generalizer import expand_table_records_for_judgement
from .utils import clean_text, ensure_dir


@dataclass
class ManufacturingDateNode:
    page_number: int
    label: str
    date_text: str
    date_value: date
    x_center: float
    y_center: float
    group_name: str = ""


MEASUREMENT_OR_CRITERIA_KEYWORDS = [
    "이상", "이하", "초과", "미만",
    "CFU", "cfu",
    "mL", "ml",
    "mg", "g", "kg",
    "μg", "µg", "ug", "ng",
    "IU", "EU", "LD", "log",
    "%", "cells", "cell", "mOsm", "Osm", "nm",
    "≥", "≤", ">", "<", "~",
]


UNIT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])("
    r"EU/mL|IU/mL|CFU/mL|cells/mL|"
    r"CFU|cfu|mL|ml|L|mg|g|kg|μg|µg|ug|ng|"
    r"IU|EU|LD|log|cells|cell|mOsm|Osm|nm|%"
    r")(?![A-Za-z])"
)


def _is_valid_section_number(section_number: str | None) -> bool:
    section_number = clean_text(section_number)

    if not section_number:
        return False

    if not re.fullmatch(r"\d+(?:\.\d+)*", section_number):
        return False

    parts = section_number.split(".")

    if parts[0] == "0":
        return False

    if any(part == "0" for part in parts[1:]):
        return False

    return True


def _looks_like_criteria_text(text: str | None) -> bool:
    text = clean_text(text)

    if not text:
        return False

    if any(k in text for k in ["이상", "이하", "초과", "미만", "≥", "≤", ">", "<", "~"]):
        return True

    if re.search(r"\d+(?:\.\d+)?\s*[xX]\s*10", text):
        return True

    if re.search(r"\d", text) and UNIT_TOKEN_RE.search(text):
        return True

    return False


def _is_manufacturing_summary_section(
    section_number: str | None,
    section_title: str | None,
) -> bool:
    section_number = clean_text(section_number)
    section_title = clean_text(section_title)
    compact = re.sub(r"\s+", "", f"{section_number} {section_title}")

    return section_number == "1.2" or "제조요약도" in compact


def _find_manufacturing_summary_pages(pdf_path: Path) -> list[int]:
    page_numbers: list[int] = []

    with fitz.open(pdf_path) as doc:
        for idx, page in enumerate(doc, start=1):
            text = clean_text(page.get_text("text") or "")
            compact = re.sub(r"\s+", "", text)

            if "제조요약도" in compact:
                page_numbers.append(idx)

    return page_numbers


def _pdf_artifact_stem(pdf_path: Path) -> str:
    """
    Windows/PyMuPDF 저장 경로가 MAX_PATH에 걸리지 않도록
    제출 PDF 파일명 대신 짧고 안정적인 작업 파일명을 만든다.

    새로 들어온 Gmail 첨부파일은 파일명이 길어질 수 있는데,
    그 파일명을 그대로 PNG/추출 폴더명에 붙이면
    PyMuPDF pix.save()가 cannot open file 오류를 낼 수 있다.
    """
    try:
        stat = pdf_path.stat()
        source = f"{pdf_path.resolve()}::{stat.st_size}::{stat.st_mtime_ns}"
    except Exception:
        source = str(pdf_path)

    digest = hashlib.sha1(source.encode("utf-8", "ignore")).hexdigest()[:16]
    return f"sp_{digest}"


def _render_pdf_page(
    pdf_path: Path,
    page_number: int,
    output_dir: Path,
    suffix: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_suffix = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", suffix or "page").strip("._-") or "page"
    out_path = output_dir / f"{safe_suffix}_page_{page_number}.png"

    with fitz.open(pdf_path) as doc:
        page = doc[page_number - 1]
        matrix = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(out_path))

    return out_path


def _parse_date_value(value: str) -> date | None:
    value = clean_text(value)

    m = re.search(r"(20\d{2})[.\-/년\s]+(\d{1,2})(?:[.\-/월\s]+(\d{1,2}))?", value)

    if not m:
        return None

    year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3)) if m.group(3) else 1

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _group_words_to_lines(words: list[tuple]) -> list[dict]:
    if not words:
        return []

    words = sorted(words, key=lambda w: (round(float(w[1]), 1), float(w[0])))

    lines: list[list[tuple]] = []
    current: list[tuple] = []
    current_y: float | None = None

    for word in words:
        y0 = float(word[1])

        if current_y is None or abs(y0 - current_y) <= 4.0:
            current.append(word)
            current_y = y0 if current_y is None else min(current_y, y0)
        else:
            lines.append(current)
            current = [word]
            current_y = y0

    if current:
        lines.append(current)

    out: list[dict] = []

    for line_words in lines:
        line_words = sorted(line_words, key=lambda w: float(w[0]))

        text = clean_text(" ".join(clean_text(w[4]) for w in line_words if clean_text(w[4])))

        if not text:
            continue

        x0 = min(float(w[0]) for w in line_words)
        y0 = min(float(w[1]) for w in line_words)
        x1 = max(float(w[2]) for w in line_words)
        y1 = max(float(w[3]) for w in line_words)

        out.append(
            {
                "text": text,
                "words": line_words,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "x_center": (x0 + x1) / 2,
                "y_center": (y0 + y1) / 2,
            }
        )

    return out


def _text_near_x(line: dict, x_center: float, radius: float = 140.0) -> str:
    parts: list[str] = []

    for word in line.get("words", []):
        word_x = (float(word[0]) + float(word[2])) / 2

        if abs(word_x - x_center) <= radius:
            parts.append(clean_text(word[4]))

    return clean_text(" ".join(parts))


def _is_stage_label_candidate(text: str) -> bool:
    text = clean_text(text)
    compact = re.sub(r"\s+", "", text)

    if not compact:
        return False

    if re.search(r"20\d{2}", compact):
        return False

    blocked_exact = {
        "ComponentA",
        "ComponentB",
        "ComponentAComponentB",
        "제조번호",
        "제조년월일",
        "제조량",
        "제조수량",
    }

    if compact in blocked_exact:
        return False

    blocked_contains = [
        "페이지",
        "SummaryProtocol",
        "Production",
        "Quality",
        "control",
        "Japaneseencephalitis",
        "Vaccine",
        "Inactivated",
        "동국약품",
        "주식회사",
        "제조번호:",
        "제조번호：",
    ]

    if any(x in compact for x in blocked_contains):
        return False

    if len(compact) < 2:
        return False

    return True


def _is_group_header_candidate(text: str) -> bool:
    text = clean_text(text)
    compact = re.sub(r"\s+", "", text)

    if not compact:
        return False

    if re.search(r"20\d{2}", compact):
        return False

    blocked_contains = [
        "페이지",
        "SummaryProtocol",
        "Production",
        "Quality",
        "control",
        "Japaneseencephalitis",
        "Vaccine",
        "Inactivated",
        "동국약품",
        "주식회사",
        "제조번호",
        "제조요약도",
    ]

    if any(x in compact for x in blocked_contains):
        return False

    return len(compact) >= 2


def _find_stage_label_for_date(
    lines: list[dict],
    date_line_idx: int,
    date_x: float,
) -> str:
    date_line = lines[date_line_idx]
    date_y = float(date_line["y_center"])

    for idx in range(date_line_idx - 1, -1, -1):
        line = lines[idx]
        line_y = float(line["y_center"])

        if date_y - line_y > 90:
            break

        full_text = clean_text(line["text"])
        near_text = _text_near_x(line, date_x, radius=150)

        for candidate in [near_text, full_text]:
            candidate = clean_text(candidate)

            if _is_stage_label_candidate(candidate):
                return candidate

    return "공정명 미확인"


def _extract_group_headers(lines: list[dict], first_node_y: float) -> list[dict]:
    headers: list[dict] = []

    for line in lines:
        if line["y_center"] >= first_node_y:
            continue

        line_text = clean_text(line["text"])

        if not _is_group_header_candidate(line_text):
            continue

        headers.append(line)

    return headers


def _find_group_name_for_cluster(
    cluster: list[ManufacturingDateNode],
    headers: list[dict],
    common_y_threshold: float,
) -> str:
    if not cluster:
        return "흐름"

    cluster_x = sum(node.x_center for node in cluster) / len(cluster)
    cluster_top_y = min(node.y_center for node in cluster)

    if cluster_top_y >= common_y_threshold:
        return "공통 흐름"

    best_header = ""
    best_distance = 999999.0

    for line in headers:
        near = _text_near_x(line, cluster_x, radius=100)

        candidates = []

        if _is_group_header_candidate(near):
            candidates.append(near)

        full_text = clean_text(line["text"])
        if _is_group_header_candidate(full_text):
            candidates.append(full_text)

        for candidate in candidates:
            distance = abs(float(line["x_center"]) - cluster_x)

            if near and candidate == near:
                distance = 0

            if distance < best_distance:
                best_distance = distance
                best_header = candidate

    if best_header:
        return f"{best_header} 흐름"

    return "좌측 흐름" if cluster_x < 300 else "우측 흐름"


def _cluster_nodes_by_x(nodes: list[ManufacturingDateNode]) -> list[list[ManufacturingDateNode]]:
    if not nodes:
        return []

    sorted_nodes = sorted(nodes, key=lambda n: n.x_center)

    clusters: list[list[ManufacturingDateNode]] = []

    for node in sorted_nodes:
        if not clusters:
            clusters.append([node])
            continue

        last_cluster = clusters[-1]
        cluster_center = sum(n.x_center for n in last_cluster) / len(last_cluster)

        if abs(node.x_center - cluster_center) <= 85:
            last_cluster.append(node)
        else:
            clusters.append([node])

    for cluster in clusters:
        cluster.sort(key=lambda n: (n.y_center, n.x_center))

    return clusters


def _extract_manufacturing_date_nodes_for_page(
    page: fitz.Page,
    page_number: int,
) -> tuple[list[ManufacturingDateNode], list[dict]]:
    words = page.get_text("words") or []
    lines = _group_words_to_lines(words)

    candidate_words: list[tuple[int, tuple]] = []

    for line_idx, line in enumerate(lines):
        line_text = clean_text(line["text"])

        if "제조년월일" not in line_text:
            continue

        for word in line.get("words", []):
            word_text = clean_text(word[4])

            if re.fullmatch(r"20\d{2}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?", word_text):
                candidate_words.append((line_idx, word))

    if len(candidate_words) < 2:
        candidate_words = []

        for line_idx, line in enumerate(lines):
            line_text = clean_text(line["text"])

            if "페이지" in line_text or "제조번호 :" in line_text or "제조번호:" in line_text:
                continue

            if "제조량" in line_text or "제조수량" in line_text:
                continue

            for word in line.get("words", []):
                word_text = clean_text(word[4])

                if re.fullmatch(r"20\d{2}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?", word_text):
                    candidate_words.append((line_idx, word))

    nodes: list[ManufacturingDateNode] = []
    seen: set[tuple[int, int, str]] = set()

    for line_idx, word in candidate_words:
        word_text = clean_text(word[4])
        parsed_date = _parse_date_value(word_text)

        if parsed_date is None:
            continue

        x_center = (float(word[0]) + float(word[2])) / 2
        y_center = (float(word[1]) + float(word[3])) / 2

        key = (round(x_center), round(y_center), word_text)

        if key in seen:
            continue

        seen.add(key)

        label = _find_stage_label_for_date(lines, line_idx, x_center)

        nodes.append(
            ManufacturingDateNode(
                page_number=page_number,
                label=label,
                date_text=word_text,
                date_value=parsed_date,
                x_center=x_center,
                y_center=y_center,
            )
        )

    nodes.sort(key=lambda n: (n.y_center, n.x_center))

    return nodes, lines


def _assign_flow_groups(
    nodes: list[ManufacturingDateNode],
    lines: list[dict],
) -> list[list[ManufacturingDateNode]]:
    if not nodes:
        return []

    clusters = _cluster_nodes_by_x(nodes)

    first_y = min(node.y_center for node in nodes)
    last_y = max(node.y_center for node in nodes)

    common_y_threshold = first_y + (last_y - first_y) * 0.42

    headers = _extract_group_headers(lines, first_y)

    for cluster in clusters:
        group_name = _find_group_name_for_cluster(cluster, headers, common_y_threshold)

        for node in cluster:
            node.group_name = group_name

    upper_clusters = [c for c in clusters if c and "공통" not in c[0].group_name]
    common_clusters = [c for c in clusters if c and "공통" in c[0].group_name]

    upper_clusters.sort(key=lambda c: sum(n.x_center for n in c) / len(c))
    common_clusters.sort(key=lambda c: min(n.y_center for n in c))

    return upper_clusters + common_clusters


def _validate_group_sequence(group_name: str, nodes: list[ManufacturingDateNode]) -> list[str]:
    violations: list[str] = []

    ordered = sorted(nodes, key=lambda n: (n.y_center, n.x_center))

    for before, after in zip(ordered, ordered[1:]):
        if after.date_value < before.date_value:
            violations.append(
                f"{group_name}: {before.label}({before.date_text}) → {after.label}({after.date_text})"
            )

    return violations


def _validate_merge_sequence(
    clusters: list[list[ManufacturingDateNode]],
) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    merge_check_lines: list[str] = []

    common_clusters = [
        cluster for cluster in clusters
        if cluster and "공통" in cluster[0].group_name
    ]

    if not common_clusters:
        return violations, merge_check_lines

    first_common_node = min(
        [node for cluster in common_clusters for node in cluster],
        key=lambda n: n.y_center,
    )

    for cluster in clusters:
        if not cluster:
            continue

        group_name = cluster[0].group_name or "흐름"

        if "공통" in group_name:
            continue

        last_upper_node = max(cluster, key=lambda n: n.y_center)

        is_valid = first_common_node.date_value >= last_upper_node.date_value
        status_text = "정상" if is_valid else "오류"

        merge_check_line = (
            f"{group_name} 마지막 공정 "
            f"{last_upper_node.label}({last_upper_node.date_text}) "
            f"→ 공통 흐름 첫 공정 "
            f"{first_common_node.label}({first_common_node.date_text}): {status_text}"
        )

        merge_check_lines.append(merge_check_line)

        if not is_valid:
            violations.append(
                f"{group_name}: "
                f"{last_upper_node.label}({last_upper_node.date_text}) "
                f"→ {first_common_node.label}({first_common_node.date_text})"
            )

    return violations, merge_check_lines


def _build_readable_manufacturing_summary(
    clusters: list[list[ManufacturingDateNode]],
) -> str:
    parts: list[str] = []

    for cluster in clusters:
        if not cluster:
            continue

        group_name = cluster[0].group_name or "흐름"
        ordered = sorted(cluster, key=lambda n: (n.y_center, n.x_center))

        flow = " → ".join(
            f"{node.label}({node.date_text})"
            for node in ordered
        )

        parts.append(f"{group_name}: {flow}")

    return " / ".join(parts)


def _node_to_dict(node: ManufacturingDateNode) -> dict:
    return {
        "page_number": node.page_number,
        "label": node.label,
        "date_text": node.date_text,
        "date_value": node.date_value.isoformat(),
        "x_center": node.x_center,
        "y_center": node.y_center,
        "group_name": node.group_name,
    }


def _judge_manufacturing_summary_page(
    pdf_path: Path,
    page_numbers: list[int],
) -> tuple[str | None, str | None, dict]:
    if not page_numbers:
        return (
            HOLD_LABEL,
            "제조요약도 페이지를 찾지 못해 사람의 확인이 필요합니다.",
            {
                "manufacturing_summary_found": False,
                "date_nodes": [],
                "violations": [],
                "readable_summary": "",
                "merge_checks": [],
            },
        )

    all_summaries: list[str] = []
    all_violations: list[str] = []
    all_merge_check_lines: list[str] = []
    all_nodes: list[ManufacturingDateNode] = []

    with fitz.open(pdf_path) as doc:
        for page_number in page_numbers:
            page = doc[page_number - 1]
            nodes, lines = _extract_manufacturing_date_nodes_for_page(page, page_number)

            if len(nodes) < 2:
                continue

            clusters = _assign_flow_groups(nodes, lines)

            for cluster in clusters:
                if not cluster:
                    continue

                group_name = cluster[0].group_name or "흐름"
                all_violations.extend(_validate_group_sequence(group_name, cluster))

            merge_violations, merge_check_lines = _validate_merge_sequence(clusters)
            all_violations.extend(merge_violations)
            all_merge_check_lines.extend(merge_check_lines)

            summary = _build_readable_manufacturing_summary(clusters)

            if summary:
                all_summaries.append(summary)

            all_nodes.extend(nodes)

    if len(all_nodes) < 2:
        return (
            HOLD_LABEL,
            "제조요약도에서 제조년월일 정보를 충분히 추출하지 못해 사람의 확인이 필요합니다.",
            {
                "manufacturing_summary_found": True,
                "date_nodes": [_node_to_dict(node) for node in all_nodes],
                "violations": [],
                "readable_summary": "",
                "merge_checks": [],
            },
        )

    readable_summary = " / ".join(all_summaries)

    flow_lines: list[str] = []
    for summary in all_summaries:
        for part in summary.split(" / "):
            part = clean_text(part)
            if part:
                flow_lines.append(part)

    if all_violations:
        reason_lines = [
            "제조요약도 날짜 선행관계 오류가 확인되었습니다.",
        ]

        if flow_lines:
            reason_lines.append("")
            reason_lines.append("공정 흐름")
            reason_lines.extend(f"- {line}" for line in flow_lines)

        if all_merge_check_lines:
            reason_lines.append("")
            reason_lines.append("합류 검증")
            reason_lines.extend(f"- {line}" for line in all_merge_check_lines)

        reason_lines.append("")
        reason_lines.append("오류 항목")
        reason_lines.extend(f"- {violation}" for violation in all_violations[:5])

        reason_lines.append("")
        reason_lines.append("뒤 공정 날짜가 앞 공정보다 빠른 항목이 있어 검수불합격입니다.")

        return (
            FAIL_LABEL,
            "\n".join(reason_lines),
            {
                "manufacturing_summary_found": True,
                "date_nodes": [_node_to_dict(node) for node in all_nodes],
                "violations": all_violations,
                "readable_summary": readable_summary,
                "merge_checks": all_merge_check_lines,
            },
        )

    reason_lines = [
        "제조요약도 날짜 선행관계 정상입니다.",
    ]

    if flow_lines:
        reason_lines.append("")
        reason_lines.append("공정 흐름")
        reason_lines.extend(f"- {line}" for line in flow_lines)

    if all_merge_check_lines:
        reason_lines.append("")
        reason_lines.append("합류 검증")
        reason_lines.extend(f"- {line}" for line in all_merge_check_lines)

    reason_lines.append("")
    reason_lines.append("각 흐름 내부 순서와 병렬 흐름의 공통 공정 합류 순서가 모두 적절하여 검수합격입니다.")

    return (
        PASS_LABEL,
        "\n".join(reason_lines),
        {
            "manufacturing_summary_found": True,
            "date_nodes": [_node_to_dict(node) for node in all_nodes],
            "violations": [],
            "readable_summary": readable_summary,
            "merge_checks": all_merge_check_lines,
        },
    )


def _make_structural_evaluation(
    *,
    order_idx: int,
    section_number: str,
    test_name: str,
    criteria: str,
    result: str,
    final_status: str,
    reason: str,
    raw_text: str = "",
    record_type: str = "structural_validation",
    section_title: str = "문서 기본요건 적합성 확인 로직",
    source: str = "structural_rule",
) -> Evaluation:
    return Evaluation(
        order_idx=order_idx,
        record_type=record_type,
        section_number=section_number,
        section_title=section_title,
        test_name=test_name,
        criteria=criteria,
        result=result,
        final_status=final_status,
        reason=reason,
        confidence="normal",
        source=source,
        raw_text=raw_text or result,
        comparison_completed=True,
    )


def _load_extraction_result(extract_dir: Path) -> dict:
    path = extract_dir / "06_extraction_result.json"

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _build_page_continuity_evaluation(
    *,
    pdf_path: Path,
    extract_dir: Path,
    order_idx: int,
) -> tuple[Evaluation, dict]:
    criteria = (
        "SP 문서 각 페이지의 현재 페이지 번호와 전체 페이지 수가 확인되고, "
        "현재 페이지 번호가 1페이지부터 마지막 페이지까지 연속되어야 합니다."
    )
    actual_total_pages = 0

    try:
        with fitz.open(pdf_path) as doc:
            actual_total_pages = len(doc)
    except Exception:
        pass

    extraction_result = _load_extraction_result(extract_dir)
    page_headers = extraction_result.get("page_headers") or []

    if not isinstance(page_headers, list) or not page_headers:
        result = "페이지 번호 메타데이터 미확인"
        reason = (
            "추출 JSON에서 페이지 번호 메타데이터(page_headers)를 확인하지 못해 "
            "페이지 누락 여부를 자동 확정하지 못했습니다."
        )
        return (
            _make_structural_evaluation(
                order_idx=order_idx,
                section_number="A.2",
                test_name="페이지 번호 연속성 및 전체 페이지 수 확인",
                criteria=criteria,
                result=result,
                final_status=HOLD_LABEL,
                reason=reason,
            ),
            {
                "actual_total_pages": actual_total_pages,
                "page_headers_found": False,
                "missing_pdf_pages": [],
                "printed_sequence": [],
                "violations": [result],
            },
        )

    printable_headers: list[dict] = []
    missing_pdf_pages: list[int] = []

    for header in page_headers:
        if not isinstance(header, dict):
            continue

        pdf_page = header.get("pdf_page")
        printed_page = header.get("printed_page")
        printed_total = header.get("printed_total_pages")

        if printed_page is None or printed_total is None:
            if isinstance(pdf_page, int):
                missing_pdf_pages.append(pdf_page)
            continue

        try:
            printable_headers.append(
                {
                    "pdf_page": int(pdf_page),
                    "printed_page": int(printed_page),
                    "printed_total_pages": int(printed_total),
                }
            )
        except (TypeError, ValueError):
            if isinstance(pdf_page, int):
                missing_pdf_pages.append(pdf_page)

    if not printable_headers:
        result = "문서 내 인쇄 페이지 번호 미확인"
        reason = (
            "추출 JSON에는 page_headers가 있으나 인쇄된 현재/전체 페이지 번호가 "
            "확인되지 않아 페이지 누락 여부를 자동 확정하지 못했습니다."
        )
        return (
            _make_structural_evaluation(
                order_idx=order_idx,
                section_number="A.2",
                test_name="페이지 번호 연속성 및 전체 페이지 수 확인",
                criteria=criteria,
                result=result,
                final_status=HOLD_LABEL,
                reason=reason,
            ),
            {
                "actual_total_pages": actual_total_pages,
                "page_headers_found": True,
                "missing_pdf_pages": missing_pdf_pages,
                "printed_sequence": [],
                "violations": [result],
            },
        )

    printable_headers.sort(key=lambda item: item["pdf_page"])
    printed_sequence = [item["printed_page"] for item in printable_headers]
    printed_totals = [item["printed_total_pages"] for item in printable_headers]
    printed_total_candidates = sorted(set(printed_totals))
    expected_printed_total = printed_total_candidates[0] if len(printed_total_candidates) == 1 else 0
    expected_sequence = (
        list(range(1, expected_printed_total + 1))
        if expected_printed_total
        else []
    )
    printable_pdf_pages = {item["pdf_page"] for item in printable_headers}
    cover_page_pdf_pages = [
        page
        for page in missing_pdf_pages
        if page < min(printable_pdf_pages)
    ] if printable_pdf_pages else []
    non_cover_missing_pdf_pages = [
        page
        for page in missing_pdf_pages
        if page not in set(cover_page_pdf_pages)
    ]
    violations: list[str] = []

    if len(printed_total_candidates) > 1:
        totals = ", ".join(str(total) for total in printed_total_candidates)
        violations.append(
            f"문서 내 표시 전체 페이지 수가 서로 일치하지 않습니다: {totals}"
        )

    if expected_sequence and printed_sequence != expected_sequence:
        violations.append(
            "인쇄된 현재 페이지 번호가 1페이지부터 마지막 페이지까지 연속되지 않습니다."
        )

    if expected_printed_total and len(printable_headers) != expected_printed_total:
        violations.append(
            f"인쇄 페이지 번호가 확인된 본문 페이지 수({len(printable_headers)})와 표시 전체 페이지 수({expected_printed_total})가 일치하지 않습니다."
        )

    if non_cover_missing_pdf_pages:
        violations.append(
            "일부 PDF 페이지에서 인쇄된 현재/전체 페이지 번호를 확인하지 못했습니다: "
            + ", ".join(str(page) for page in sorted(set(non_cover_missing_pdf_pages))[:12])
        )

    if violations:
        result = "페이지 누락 확인"
        reason = "\n".join(
            [
                "페이지 번호 또는 전체 페이지 수 불일치가 확인되어 페이지 누락으로 판단했습니다.",
                *[f"- {violation}" for violation in violations],
            ]
        )
        status = FAIL_LABEL if "확인하지 못했습니다" not in " ".join(violations) else HOLD_LABEL
    else:
        result = (
            f"인쇄 페이지 {printed_sequence[0]}/{printed_totals[0]}부터 "
            f"{printed_sequence[-1]}/{printed_totals[-1]}까지 연속 확인"
        )
        if cover_page_pdf_pages:
            reason = (
                "표지로 판단되는 첫 PDF 페이지를 제외하고, 본문 인쇄 페이지 번호가 "
                f"1/{expected_printed_total}부터 {expected_printed_total}/{expected_printed_total}까지 "
                "연속 확인되어 충족으로 판단했습니다."
            )
        else:
            reason = "본문 인쇄 페이지 번호와 표시 전체 페이지 수가 연속 확인되어 충족으로 판단했습니다."
        status = PASS_LABEL

    return (
        _make_structural_evaluation(
            order_idx=order_idx,
            section_number="A.2",
            test_name="페이지 번호 연속성 및 전체 페이지 수 확인",
            criteria=criteria,
            result=result,
            final_status=status,
            reason=reason,
            raw_text="\n".join(
                f"PDF {item['pdf_page']}: {item['printed_page']}/{item['printed_total_pages']} 페이지"
                for item in printable_headers
            ),
        ),
        {
            "actual_total_pages": actual_total_pages,
            "page_headers_found": True,
            "missing_pdf_pages": missing_pdf_pages,
            "cover_page_pdf_pages": cover_page_pdf_pages,
            "non_cover_missing_pdf_pages": non_cover_missing_pdf_pages,
            "printed_sequence": printed_sequence,
            "printed_total_pages": printed_total_candidates,
            "violations": violations,
        },
    )


def _has_detail_manufacturing_occurrence(occurrence) -> bool:
    source_label = clean_text(getattr(occurrence, "source_label", ""))
    section_number = clean_text(getattr(occurrence, "section_number", ""))

    if "제조요약도" in source_label or "제조요약정보" in source_label:
        return False

    if section_number == "1.2":
        return False

    return True


def _build_process_item_coverage_evaluation(
    *,
    records_source,
    order_idx: int,
) -> tuple[Evaluation, dict]:
    criteria = (
        "제조요약도에 기재된 각 제조단계 및 공정 항목이 상세제조기록에 "
        "누락 없이 기재되어야 합니다."
    )
    summary_items = extract_summary_items_from_records(records_source)

    if not summary_items:
        result = "제조요약도 공정 항목 미추출"
        reason = (
            "제조요약도에서 제조단계 또는 공정 항목을 추출하지 못해 "
            "상세제조기록 누락 여부를 자동 확정하지 못했습니다."
        )
        return (
            _make_structural_evaluation(
                order_idx=order_idx,
                section_number="A.3",
                test_name="제조요약도 공정 항목 상세제조기록 기재 확인",
                criteria=criteria,
                result=result,
                final_status=HOLD_LABEL,
                reason=reason,
            ),
            {"summary_stage_names": [], "missing_stage_names": [], "covered_stage_names": []},
        )

    occurrences = collect_manufacturing_info_occurrences(records_source, summary_items)
    missing_stage_names: list[str] = []
    covered_stage_names: list[str] = []

    for item in summary_items:
        stage_key = _norm_match(item.stage_name)
        stage_occurrences = list(occurrences.get(stage_key, []))

        if any(_has_detail_manufacturing_occurrence(occurrence) for occurrence in stage_occurrences):
            covered_stage_names.append(item.stage_name)
        else:
            missing_stage_names.append(item.stage_name)

    if missing_stage_names:
        result = "공정 항목 누락 확인: " + ", ".join(missing_stage_names[:8])
        reason = "\n".join(
            [
                "제조요약도에 존재하는 제조단계 또는 공정 항목이 상세제조기록에서 확인되지 않아 공정 항목 누락으로 판단했습니다.",
                *[f"- {name}" for name in missing_stage_names[:12]],
            ]
        )
        status = FAIL_LABEL
    else:
        result = f"제조요약도 공정 항목 {len(covered_stage_names)}건 상세제조기록 기재 확인"
        reason = "제조요약도의 각 제조단계 및 공정 항목이 상세제조기록에서 확인되어 충족으로 판단했습니다."
        status = PASS_LABEL

    return (
        _make_structural_evaluation(
            order_idx=order_idx,
            section_number="A.3",
            test_name="제조요약도 공정 항목 상세제조기록 기재 확인",
            criteria=criteria,
            result=result,
            final_status=status,
            reason=reason,
        ),
        {
            "summary_stage_names": [item.stage_name for item in summary_items],
            "missing_stage_names": missing_stage_names,
            "covered_stage_names": covered_stage_names,
        },
    )


def _build_structural_evaluations(
    *,
    pdf_path: Path,
    extract_dir: Path,
    records: list,
    start_order_idx: int,
) -> tuple[list[Evaluation], dict]:
    page_eval, page_meta = _build_page_continuity_evaluation(
        pdf_path=pdf_path,
        extract_dir=extract_dir,
        order_idx=start_order_idx,
    )
    records_source = {
        "pdf_path": str(pdf_path),
        "records": records,
        "metadata": {"extract_dir": str(extract_dir)},
    }
    process_eval, process_meta = _build_process_item_coverage_evaluation(
        records_source=records_source,
        order_idx=start_order_idx + 1,
    )

    return [page_eval, process_eval], {
        "page_continuity": page_meta,
        "process_item_coverage": process_meta,
    }


NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z가-힣])[-+]?\d+(?:\.\d+)?(?![A-Za-z가-힣])")

SKY_COVIONE_REQUIRED_TEST_UNITS = [
    ("세포성장 및 증식확인시험", "cells/mL"),
    ("박테리오파지확인시험", "PFU/mL"),
    ("생균수시험", "CFU/mL"),
    ("플라스미드 대장균수확인시험", "AU"),
    ("확인시험", "copies/cell"),
    ("단백질함량시험", "μg/mL"),
    ("엔도톡신시험", "EU/mL"),
    ("잔류 숙주세포 유래 단백질시험", "μg/mg of protein"),
    ("잔류 숙주세포 유래 DNA시험", "ng/mg of protein"),
    ("박테리오파지부정시험", "pg/μL"),
    ("항원함량시험", "μg/mL"),
    ("엔도톡신", "EU/mg of protein"),
    ("입자크기측정시험", "nm"),
    ("삼투압시험", "mOsm/kg"),
    ("주사제의 불용성미립자시험", "μm"),
    ("주사제 실용량시험", "mL"),
]

UNIT_CANONICAL_ALIASES = {
    "cells/ml": "cells/mL",
    "cell/ml": "cells/mL",
    "pfu/ml": "PFU/mL",
    "cfu/ml": "CFU/mL",
    "au": "AU",
    "copies/cell": "copies/cell",
    "copy/cell": "copies/cell",
    "ug/ml": "μg/mL",
    "μg/ml": "μg/mL",
    "µg/ml": "μg/mL",
    "eu/ml": "EU/mL",
    "ug/mgofprotein": "μg/mg of protein",
    "μg/mgofprotein": "μg/mg of protein",
    "µg/mgofprotein": "μg/mg of protein",
    "ng/mgofprotein": "ng/mg of protein",
    "pg/ul": "pg/μL",
    "pg/μl": "pg/μL",
    "pg/µl": "pg/μL",
    "eu/mgofprotein": "EU/mg of protein",
    "nm": "nm",
    "mosm/kg": "mOsm/kg",
    "um": "μm",
    "μm": "μm",
    "µm": "μm",
    "ml": "mL",
}

PRODUCT_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z가-힣])"
    r"(?:μg/mg\s*of\s*protein|µg/mg\s*of\s*protein|ug/mg\s*of\s*protein|"
    r"ng/mg\s*of\s*protein|EU/mg\s*of\s*protein|"
    r"cells/mL|cell/mL|cells/ml|cell/ml|PFU/mL|PFU/ml|CFU/mL|CFU/ml|"
    r"copies/cell|copy/cell|μg/mL|µg/mL|ug/mL|EU/mL|EU/ml|"
    r"pg/μL|pg/µL|pg/uL|pg/ul|mOsm/kg|AU|nm|μm|µm|um|mL|ml)"
    r"(?![A-Za-z가-힣])",
    re.I,
)


def _looks_like_date_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 12):start]
    after = text[end:min(len(text), end + 12)]
    context = before + text[start:end] + after

    if re.search(r"20\d{2}\s*[.\-/년]\s*\d{1,2}", context):
        return True

    if re.search(r"\d{1,2}\s*[.\-/월]\s*\d{1,2}", context):
        return True

    if re.search(r"\d+\s*/\s*\d+\s*페이지", context):
        return True

    if re.search(r"Ver\.?\s*\d", context, re.I):
        return True

    if re.search(r"(제조번호|문서\s*ID|로트번호|Lot|LOT)", context, re.I):
        return True

    return False


def _extract_decimal_tokens(text: str | None) -> list[dict]:
    text = clean_text(text)
    if not text:
        return []

    tokens: list[dict] = []

    for match in NUMBER_TOKEN_RE.finditer(text):
        value = match.group(0)

        if _looks_like_date_context(text, match.start(), match.end()):
            continue

        if "." in value:
            decimals = len(value.rsplit(".", 1)[1])
            numeric_key = value.rstrip("0").rstrip(".")
        else:
            decimals = 0
            numeric_key = value

        tokens.append(
            {
                "text": value,
                "decimals": decimals,
                "numeric_key": numeric_key,
                "start": match.start(),
                "end": match.end(),
            }
        )

    return tokens


def _record_display_name(record) -> str:
    parts = [
        clean_text(getattr(record, "section_number", "")),
        clean_text(getattr(record, "section_title", "")),
        clean_text(getattr(record, "test_name", "")),
        clean_text(getattr(record, "content_label", "")),
    ]
    return " ".join(part for part in parts if part) or "문서 항목"


def _build_decimal_precision_pair_evaluation(
    *,
    records: list,
    order_idx: int,
) -> tuple[Evaluation, dict]:
    criteria = (
        "허가사항 또는 시험기준의 수치가 소수점 이하 자릿수를 포함하여 명시된 경우, "
        "시험결과값도 동일한 소수점 이하 자릿수로 기재되어야 합니다."
    )
    violations: list[dict] = []
    checked_count = 0

    for record in records:
        if clean_text(getattr(record, "record_type", "")) != "test":
            continue

        criteria_tokens = [
            token for token in _extract_decimal_tokens(getattr(record, "criteria", ""))
            if token["decimals"] > 0
        ]
        result_tokens = _extract_decimal_tokens(getattr(record, "result", ""))

        if not criteria_tokens or not result_tokens:
            continue

        expected_places = sorted({token["decimals"] for token in criteria_tokens})

        if len(expected_places) != 1:
            continue

        expected = expected_places[0]
        checked_count += 1

        for result_token in result_tokens:
            if result_token["decimals"] != expected:
                violations.append(
                    {
                        "item": _record_display_name(record),
                        "criteria_numbers": [token["text"] for token in criteria_tokens],
                        "result_number": result_token["text"],
                        "expected_decimals": expected,
                        "actual_decimals": result_token["decimals"],
                    }
                )

    if violations:
        result = f"소수점 자릿수 불일치 {len(violations)}건 확인"
        reason_lines = [
            "시험기준의 소수점 이하 자릿수와 시험결과값의 소수점 이하 자릿수가 일치하지 않아 불충족으로 판단했습니다.",
        ]
        for item in violations[:12]:
            reason_lines.append(
                f"- {item['item']}: 기준 수치 {', '.join(item['criteria_numbers'])} 기준 "
                f"소수 {item['expected_decimals']}자리이나 결과 {item['result_number']}는 "
                f"소수 {item['actual_decimals']}자리입니다."
            )
        status = FAIL_LABEL
    elif checked_count:
        result = f"기준-결과 소수점 자릿수 {checked_count}건 일치"
        reason_lines = [
            "소수점 이하 자릿수가 명시된 시험기준과 해당 시험결과값의 자릿수가 모두 일치하여 충족으로 판단했습니다."
        ]
        status = PASS_LABEL
    else:
        result = "비교 가능한 기준-결과 소수점 수치 미확인"
        reason_lines = [
            "시험기준과 시험결과 양쪽에서 자동 비교 가능한 소수점 수치 쌍을 확인하지 못해 보류로 판단했습니다."
        ]
        status = HOLD_LABEL

    return (
        _make_structural_evaluation(
            order_idx=order_idx,
            section_number="B.1",
            test_name="시험기준-시험결과 소수점 자릿수 일치 확인",
            criteria=criteria,
            result=result,
            final_status=status,
            reason="\n".join(reason_lines),
            record_type="numeric_precision_validation",
            section_title="계산 및 수치 정합성 확인 로직",
            source="numeric_precision_rule",
        ),
        {"checked_count": checked_count, "violations": violations},
    )


def _build_document_decimal_consistency_evaluation(
    *,
    records: list,
    order_idx: int,
) -> tuple[Evaluation, dict]:
    criteria = (
        "문서 내 동일 시험 또는 동일 항목에서 기준 수치가 소수점 이하 자릿수로 제시된 경우, "
        "그 하위 또는 동일 맥락의 수치 표기는 동일한 소수점 이하 자릿수로 통일되어야 합니다."
    )
    violations: list[dict] = []
    checked_contexts = 0

    for record in records:
        context_name = _record_display_name(record)
        combined = "\n".join(
            clean_text(value)
            for value in [
                getattr(record, "criteria", ""),
                getattr(record, "result", ""),
                getattr(record, "content", ""),
                getattr(record, "raw_text", ""),
            ]
            if clean_text(value)
        )
        tokens = _extract_decimal_tokens(combined)
        decimal_tokens = [token for token in tokens if token["decimals"] > 0]

        if not decimal_tokens:
            continue

        expected_places = sorted({token["decimals"] for token in decimal_tokens})

        if len(expected_places) != 1:
            continue

        expected = expected_places[0]
        comparable_tokens = [
            token
            for token in tokens
            if token["numeric_key"] in {item["numeric_key"] for item in decimal_tokens}
        ]

        if len(comparable_tokens) <= 1:
            continue

        checked_contexts += 1
        mismatches = [
            token
            for token in comparable_tokens
            if token["decimals"] != expected
        ]

        if mismatches:
            violations.append(
                {
                    "item": context_name,
                    "expected_decimals": expected,
                    "reference_numbers": [token["text"] for token in decimal_tokens[:5]],
                    "mismatch_numbers": [token["text"] for token in mismatches[:8]],
                }
            )

    if violations:
        result = f"문서 내 소수점 자릿수 통일성 불일치 {len(violations)}건 확인"
        reason_lines = [
            "동일 시험 또는 동일 항목 안에서 소수점 이하 자릿수가 통일되지 않은 수치가 확인되어 불충족으로 판단했습니다.",
        ]
        for item in violations[:12]:
            reason_lines.append(
                f"- {item['item']}: 기준 표기 {', '.join(item['reference_numbers'])} 기준 "
                f"소수 {item['expected_decimals']}자리이나 {', '.join(item['mismatch_numbers'])} 표기가 다릅니다."
            )
        status = FAIL_LABEL
    elif checked_contexts:
        result = f"문서 내 소수점 자릿수 통일성 {checked_contexts}개 항목 확인"
        reason_lines = [
            "동일 시험 또는 동일 항목 안의 수치 표기가 기준 소수점 이하 자릿수와 일치하여 충족으로 판단했습니다."
        ]
        status = PASS_LABEL
    else:
        result = "통일성 비교 가능한 소수점 수치 미확인"
        reason_lines = [
            "문서 내에서 기준 소수점 자릿수와 비교 가능한 반복 수치를 확인하지 못해 보류로 판단했습니다."
        ]
        status = HOLD_LABEL

    return (
        _make_structural_evaluation(
            order_idx=order_idx,
            section_number="B.2",
            test_name="문서 내 동일 수치 소수점 자릿수 통일성 확인",
            criteria=criteria,
            result=result,
            final_status=status,
            reason="\n".join(reason_lines),
            record_type="numeric_precision_validation",
            section_title="계산 및 수치 정합성 확인 로직",
            source="numeric_precision_rule",
        ),
        {"checked_contexts": checked_contexts, "violations": violations},
    )


def _canonical_product_unit(unit: str | None) -> str:
    unit = clean_text(unit).replace("µ", "μ")
    key = re.sub(r"\s+", "", unit).casefold()
    return UNIT_CANONICAL_ALIASES.get(key, unit)


def _extract_product_units(text: str | None) -> list[str]:
    text = clean_text(text)
    if not text:
        return []

    units: list[str] = []
    seen: set[str] = set()

    for match in PRODUCT_UNIT_PATTERN.finditer(text.replace("µ", "μ")):
        unit = _canonical_product_unit(match.group(0))
        if unit and unit not in seen:
            units.append(unit)
            seen.add(unit)

    return units


def _sky_covione_unit_rule_applies(detail_profile: DomainDetailProfile, records: list) -> bool:
    detail_text = " ".join(
        clean_text(value)
        for value in [
            getattr(detail_profile, "requested_company", ""),
            getattr(detail_profile, "requested_product", ""),
            getattr(detail_profile, "matched_company", ""),
            getattr(detail_profile, "matched_product", ""),
        ]
    )
    detail_key = _norm_match(detail_text)

    if "스카이코비원" in detail_key or "skycovione" in detail_key:
        return True

    for record in records[:12]:
        text = " ".join(
            clean_text(getattr(record, attr, ""))
            for attr in ["section_title", "content", "raw_text", "test_name"]
        )
        text_key = _norm_match(text)
        if "스카이코비원" in text_key or "skycovione" in text_key:
            return True

    return False


def _expected_sky_covione_unit_for_test(test_name: str | None) -> str:
    test_key = _norm_match(test_name)
    if not test_key:
        return ""

    exact_matches = [
        unit
        for name, unit in SKY_COVIONE_REQUIRED_TEST_UNITS
        if _norm_match(name) == test_key
    ]
    if exact_matches:
        return exact_matches[0]

    partial_matches = [
        (name, unit)
        for name, unit in SKY_COVIONE_REQUIRED_TEST_UNITS
        if _norm_match(name) in test_key or test_key in _norm_match(name)
    ]
    if not partial_matches:
        return ""

    partial_matches.sort(key=lambda item: len(_norm_match(item[0])), reverse=True)
    return partial_matches[0][1]


def _build_sky_covione_required_unit_evaluation(
    *,
    detail_profile: DomainDetailProfile,
    records: list,
    order_idx: int,
) -> tuple[Evaluation | None, dict]:
    if not _sky_covione_unit_rule_applies(detail_profile, records):
        return None, {"applied": False, "reason": "스카이코비원멀티주 문서가 아니어서 제품별 단위 검증을 적용하지 않았습니다."}

    criteria = (
        "SK바이오사이언스 스카이코비원멀티주의 지정 시험항목은 제품별 시험별 단위표에 "
        "명시된 단위만 사용해야 합니다."
    )
    checked: list[dict] = []
    violations: list[dict] = []

    for record in records:
        if clean_text(getattr(record, "record_type", "")) != "test":
            continue

        test_name = clean_text(getattr(record, "test_name", ""))
        expected_unit = _expected_sky_covione_unit_for_test(test_name)

        if not expected_unit:
            continue

        field_units: list[tuple[str, str]] = []
        for field_name, attr in [
            ("시험기준", "criteria"),
            ("시험결과", "result"),
            ("원문", "raw_text"),
        ]:
            for unit in _extract_product_units(getattr(record, attr, "")):
                field_units.append((field_name, unit))

        actual_units = []
        seen_units: set[str] = set()
        for field_name, unit in field_units:
            key = f"{field_name}:{unit}"
            if key in seen_units:
                continue
            seen_units.add(key)
            actual_units.append({"field": field_name, "unit": unit})

        if not actual_units:
            continue

        checked.append(
            {
                "test_name": test_name,
                "expected_unit": expected_unit,
                "actual_units": actual_units,
            }
        )

        mismatches = [
            item for item in actual_units
            if _canonical_product_unit(item["unit"]) != _canonical_product_unit(expected_unit)
        ]
        if mismatches:
            violations.append(
                {
                    "test_name": test_name,
                    "expected_unit": expected_unit,
                    "mismatches": mismatches,
                }
            )

    if violations:
        result = f"단위불일치 {len(violations)}건 확인"
        reason_lines = [
            "SK바이오사이언스 스카이코비원멀티주 제품별 시험 단위표와 다른 단위가 확인되어 단위불일치로 불충족 판단했습니다.",
        ]
        for item in violations[:12]:
            mismatch_text = ", ".join(
                f"{mismatch['field']} {mismatch['unit']}"
                for mismatch in item["mismatches"]
            )
            reason_lines.append(
                f"- {item['test_name']}: 지정 단위 {item['expected_unit']}이나 {mismatch_text}로 기재되었습니다."
            )
        status = FAIL_LABEL
    elif checked:
        result = f"제품별 지정 단위 {len(checked)}건 일치"
        reason_lines = [
            "SK바이오사이언스 스카이코비원멀티주의 시험별 지정 단위와 문서 내 단위가 일치하여 충족으로 판단했습니다."
        ]
        status = PASS_LABEL
    else:
        result = "제품별 지정 단위 비교 대상 미확인"
        reason_lines = [
            "스카이코비원멀티주 문서로 판단되지만, 자동 비교 가능한 지정 시험항목 단위를 확인하지 못해 보류로 판단했습니다."
        ]
        status = HOLD_LABEL

    return (
        _make_structural_evaluation(
            order_idx=order_idx,
            section_number="B.3",
            test_name="스카이코비원멀티주 시험별 지정 단위 확인",
            criteria=criteria,
            result=result,
            final_status=status,
            reason="\n".join(reason_lines),
            record_type="numeric_precision_validation",
            section_title="계산 및 수치 정합성 확인 로직",
            source="product_unit_rule",
        ),
        {"applied": True, "checked": checked, "violations": violations},
    )


def _build_numeric_precision_evaluations(
    *,
    detail_profile: DomainDetailProfile,
    records: list,
    start_order_idx: int,
) -> tuple[list[Evaluation], dict]:
    pair_eval, pair_meta = _build_decimal_precision_pair_evaluation(
        records=records,
        order_idx=start_order_idx,
    )
    consistency_eval, consistency_meta = _build_document_decimal_consistency_evaluation(
        records=records,
        order_idx=start_order_idx + 1,
    )
    product_unit_eval, product_unit_meta = _build_sky_covione_required_unit_evaluation(
        detail_profile=detail_profile,
        records=records,
        order_idx=start_order_idx + 2,
    )

    evaluations = [pair_eval, consistency_eval]
    if product_unit_eval is not None:
        evaluations.append(product_unit_eval)

    return evaluations, {
        "criteria_result_decimal_precision": pair_meta,
        "document_decimal_consistency": consistency_meta,
        "sky_covione_required_units": product_unit_meta,
    }


def _count_evaluation_statuses(evaluations):
    """
    일반 시험은 evaluation 1개를 1건으로 세고,
    표 형태 시험은 lot_judgements 내부 행을 각각 1건으로 센다.
    """
    passed = 0
    failed = 0
    held = 0

    for ev in evaluations:
        lot_judgements = list(getattr(ev, "lot_judgements", []) or [])

        if lot_judgements:
            for item in lot_judgements:
                status = clean_text(item.get("status", ""))

                if status == PASS_LABEL:
                    passed += 1
                elif status == FAIL_LABEL:
                    failed += 1
                elif status == HOLD_LABEL:
                    held += 1

            continue

        if ev.final_status == PASS_LABEL:
            passed += 1
        elif ev.final_status == FAIL_LABEL:
            failed += 1
        elif ev.final_status == HOLD_LABEL:
            held += 1

    return passed, failed, held


class DocumentJudgePipeline:
    def __init__(
        self,
        permit_pdf_paths: list[Path] | None = None,
        *,
        company: str | None = None,
        product: str | None = None,
        detail_store: DomainDetailStore | None = None,
    ) -> None:
        self.company = clean_text(company)
        self.product = clean_text(product)
        self.detail_store = detail_store or DomainDetailStore()
        self.detail_profile = self.detail_store.resolve(self.company, self.product)
        self.rag_store = UcumRagStore()
        self.llm_client = ClovaJudgeClient(
            domain_detail_context=self.detail_profile.render_for_llm()
        )
        self.permit_store = PermitPdfStore(permit_pdf_paths)
        self.judge_engine = JudgeEngine(
            self.rag_store,
            self.llm_client,
            permit_store=self.permit_store,
        )

    def _activate_detail_profile(
        self,
        static_result: ProcessingResult | None,
    ) -> DomainDetailProfile:
        metadata = static_result.metadata if static_result is not None else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        company = (
            self.company
            or clean_text(metadata.get("document_company"))
            or clean_text(metadata.get("company"))
        )
        product = (
            self.product
            or clean_text(metadata.get("document_product"))
            or clean_text(metadata.get("product"))
        )
        self.detail_profile = self.detail_store.resolve(company, product)
        self.llm_client.set_domain_detail_context(
            self.detail_profile.render_for_llm()
        )
        return self.detail_profile

    def run(
        self,
        pdf_path: Path,
        *,
        extracted_records: list[ExtractedRecord] | None = None,
        static_result: ProcessingResult | None = None,
    ) -> ProcessingResult:
        detail_profile = self._activate_detail_profile(static_result)
        work_dir = ensure_dir(Path(tempfile.gettempdir()) / "sp_pdf_judger_preview")
        artifact_stem = _pdf_artifact_stem(pdf_path)

        if static_result is not None:
            preview_path = static_result.preview_image_path
        else:
            preview_path = work_dir / f"{artifact_stem}_page1.png"
            render_first_page(pdf_path, preview_path)

        extract_dir = ensure_dir(work_dir / f"{artifact_stem}_extract")

        if extracted_records is None:
            records = extract_records(pdf_path, extract_dir)
            record_dicts = [
                dict(vars(record))
                for record in records
            ]
            expanded_record_dicts = expand_table_records_for_judgement(
                record_dicts,
                has_permit=self.permit_store.enabled,
                keep_original_table_record=False,
            )
            if records:
                record_type = type(records[0])
                allowed_record_fields = {field.name for field in fields(record_type)}
                records = [
                    record_type(**{key: value for key, value in row.items() if key in allowed_record_fields})
                    for row in expanded_record_dicts
                ]
            else:
                records = []
        else:
            records = list(extracted_records)

        test_records = [
            r for r in records
            if getattr(r, "record_type", "") == "test"
        ]

        evaluations = [
            self.judge_engine.judge_record(r)
            for r in test_records
        ]

        if static_result is not None:
            manufacturing_page_numbers = list(static_result.manufacturing_summary_page_numbers)
            manufacturing_image_paths = list(static_result.manufacturing_summary_image_paths)
            manufacturing_status = static_result.manufacturing_summary_status
            manufacturing_reason = static_result.manufacturing_summary_reason
            manufacturing_meta = dict(static_result.metadata.get("manufacturing_summary", {}))
        else:
            manufacturing_page_numbers = _find_manufacturing_summary_pages(pdf_path)

            manufacturing_output_dir = ensure_dir(
                work_dir / f"{artifact_stem}_manufacturing_summary"
            )

            manufacturing_image_paths = [
                _render_pdf_page(
                    pdf_path=pdf_path,
                    page_number=page_number,
                    output_dir=manufacturing_output_dir,
                    suffix="manufacturing_summary",
                )
                for page_number in manufacturing_page_numbers
            ]

            manufacturing_status, manufacturing_reason, manufacturing_meta = _judge_manufacturing_summary_page(
                pdf_path=pdf_path,
                page_numbers=manufacturing_page_numbers,
            )

        structural_extract_dir = extract_dir
        if static_result is not None:
            static_extract_dir = getattr(static_result, "metadata", {}).get("extract_dir")
            if static_extract_dir:
                structural_extract_dir = Path(static_extract_dir)

        next_order_idx = max((getattr(record, "order_idx", 0) for record in records), default=0) + 1
        structural_evaluations, structural_meta = _build_structural_evaluations(
            pdf_path=pdf_path,
            extract_dir=structural_extract_dir,
            records=records,
            start_order_idx=next_order_idx,
        )
        evaluations.extend(structural_evaluations)
        numeric_evaluations, numeric_meta = _build_numeric_precision_evaluations(
            detail_profile=detail_profile,
            records=records,
            start_order_idx=next_order_idx + len(structural_evaluations),
        )
        evaluations.extend(numeric_evaluations)

        tree = build_document_tree(records, evaluations)

        passed, failed, held = _count_evaluation_statuses(evaluations)

        comparable_total = passed + failed + held

        summary = Summary(
            passed=passed,
            failed=failed,
            held=held,
            total=comparable_total,
            comparable_total=comparable_total,
        )

        return ProcessingResult(
            pdf_path=pdf_path,
            preview_image_path=preview_path,
            extracted_records=records,
            evaluations=evaluations,
            tree=tree,
            summary=summary,
            metadata={
                "llm_enabled": self.llm_client.enabled,
                "llm_model": self.llm_client.model,
                "llm_call_count": self.llm_client.call_count,
                "llm_success_count": self.llm_client.success_count,
                "llm_last_error": self.llm_client.last_error,
                "record_count": len(records),
                "manufacturing_summary_judgement": manufacturing_status,
                "extract_dir": str(extract_dir),
                "rag_doc_count": len(self.rag_store.docs),
                "rag_sources": self.rag_store.loaded_sources,
                "permit_pdf_count": len(self.permit_store.permit_pdf_paths),
                "permit_chunk_count": len(self.permit_store.chunks),
                "document_company": detail_profile.requested_company,
                "document_product": detail_profile.requested_product,
                "domain_detail_matched_company": detail_profile.matched_company,
                "domain_detail_matched_product": detail_profile.matched_product,
                "domain_detail_sources": detail_profile.sources,
                "domain_detail_fingerprint": detail_profile.fingerprint,
                "domain_detail_context": detail_profile.render_for_llm(),
                "manufacturing_summary_page_numbers": manufacturing_page_numbers,
                "manufacturing_summary_meta": manufacturing_meta,
                "structural_validation": structural_meta,
                "numeric_precision_validation": numeric_meta,
            },
            manufacturing_summary_image_paths=manufacturing_image_paths,
            manufacturing_summary_status=manufacturing_status,
            manufacturing_summary_reason=manufacturing_reason,
            manufacturing_summary_page_numbers=manufacturing_page_numbers,
        )
