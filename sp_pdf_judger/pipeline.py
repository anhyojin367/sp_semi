from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from dataclasses import fields
from datetime import date
from pathlib import Path

import fitz

from .config import FAIL_LABEL, HOLD_LABEL, PASS_LABEL
from .extractor import extract_records
from .hierarchy import build_document_tree
from .judgement import JudgeEngine
from .llm import GeminiJudgeClient
from .permit_pdf_store import PermitPdfStore
from .preview import render_first_page
from .rag import UcumRagStore
from .schemas import ProcessingResult, Summary
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
    def __init__(self, permit_pdf_paths: list[Path] | None = None) -> None:
        self.rag_store = UcumRagStore()
        self.llm_client = GeminiJudgeClient()
        self.permit_store = PermitPdfStore(permit_pdf_paths)
        self.judge_engine = JudgeEngine(
            self.rag_store,
            self.llm_client,
            permit_store=self.permit_store,
        )

    def run(
        self,
        pdf_path: Path,
        *,
        extracted_records: list[ExtractedRecord] | None = None,
        static_result: ProcessingResult | None = None,
    ) -> ProcessingResult:
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

        tree = build_document_tree(records, evaluations)

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
                "manufacturing_summary_page_numbers": manufacturing_page_numbers,
                "manufacturing_summary_meta": manufacturing_meta,
            },
            manufacturing_summary_image_paths=manufacturing_image_paths,
            manufacturing_summary_status=manufacturing_status,
            manufacturing_summary_reason=manufacturing_reason,
            manufacturing_summary_page_numbers=manufacturing_page_numbers,
        )
