# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import html
import json
import pickle
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from sp_pdf_judger.domain_details import resolve_domain_detail_profile
from sp_pdf_judger.pipeline import DocumentJudgePipeline
from sp_pdf_judger.stage_csv_exporter import (
    _is_albumin_document,
    _write_export_summary_csv,
    export_stage_csvs,
    make_stage_csv_zip,
)
from sp_pdf_judger.manufacturing_stage_ui import (
    build_stage_info_cards,
    render_manufacturing_summary_with_stage_cards,
    summarize_stage_info_cards,
)
from sp_pdf_judger.rule_regression import (
    append_rule_regression_log,
    write_output_index,
)
from sp_pdf_judger.schemas import Summary
from sp_pdf_judger.ui_html import (
    _render_section1_pdf_pages,
    render_result_html,
    render_summary_card,
)


JUDGE_COMPONENT_HEIGHT = 10000
FINAL_JUDGEMENT_VIEWPORT_HEIGHT = 860
JUDGEMENT_STATUS_DIR = Path(__file__).resolve().parent / ".sp_judgement_status"
JUDGEMENT_STATUS_INDEX = JUDGEMENT_STATUS_DIR / "status_index.json"
JUDGEMENT_CACHE_VERSION = "sp-app-direct-bridge-v53-20260729-domain-details"


# ============================================================
# 기본 유틸
# ============================================================

def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_sig(path: Path | None) -> str:
    if path is None or not path.exists():
        return "none"

    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def _artifact_key(
    pdf_path: Path,
    permit_paths: list[Path],
    original_csv_dir: Path | None,
    company: str = "",
    product: str = "",
    detail_fingerprint: str = "",
) -> str:
    permit_sig = "::".join(_file_sig(path) for path in permit_paths) if permit_paths else "no_permit"
    csv_sig = _file_sig(original_csv_dir) if original_csv_dir else "no_csv_dir"

    raw = (
        f"{JUDGEMENT_CACHE_VERSION}::"
        f"{_file_sig(pdf_path)}::"
        f"{permit_sig}::"
        f"{csv_sig}::"
        f"{company.strip()}::{product.strip()}::{detail_fingerprint}"
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _clean_stage_name(name: str) -> str:
    return str(name or "").strip()


def _final_component_height(result: Any) -> int:
    evaluations = list(getattr(result, "evaluations", []) or [])
    lot_rows = sum(len(getattr(ev, "lot_judgements", []) or []) for ev in evaluations)
    mfg_pages = len(getattr(result, "manufacturing_summary_image_paths", []) or [])
    height = 3600 + (len(evaluations) * 430) + (lot_rows * 74) + (mfg_pages * 1250)
    return max(JUDGE_COMPONENT_HEIGHT, min(height, 90000))


def _parent_scroll_bridge_script() -> str:
    return """
    <script>
    (function () {
      if (window.__spFinalScrollBridgeInstalled) {
        return;
      }
      window.__spFinalScrollBridgeInstalled = true;

      function canScrollInsideIframe(target, deltaY) {
        var el = target;
        while (el && el !== document.body && el !== document.documentElement) {
          var style = window.getComputedStyle(el);
          var overflowY = style.overflowY;
          var isScrollable = (overflowY === "auto" || overflowY === "scroll") && el.scrollHeight > el.clientHeight + 1;
          if (isScrollable) {
            if (deltaY < 0 && el.scrollTop > 0) {
              return true;
            }
            if (deltaY > 0 && el.scrollTop + el.clientHeight < el.scrollHeight - 1) {
              return true;
            }
          }
          el = el.parentElement;
        }
        return false;
      }

      function scrollParentBy(deltaX, deltaY) {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.scrollBy({ left: deltaX || 0, top: deltaY || 0, behavior: "auto" });
            return true;
          }
        } catch (err) {
          return false;
        }
        return false;
      }

      window.addEventListener("wheel", function (event) {
        if (canScrollInsideIframe(event.target, event.deltaY)) {
          return;
        }
        if (scrollParentBy(event.deltaX, event.deltaY)) {
          event.preventDefault();
        }
      }, { passive: false });

      window.addEventListener("keydown", function (event) {
        var tagName = (event.target && event.target.tagName || "").toLowerCase();
        if (tagName === "input" || tagName === "textarea" || tagName === "select" || event.target.isContentEditable) {
          return;
        }
        var delta = 0;
        if (event.key === "ArrowUp") delta = -80;
        else if (event.key === "ArrowDown") delta = 80;
        else if (event.key === "PageUp") delta = -Math.max(360, window.innerHeight * 0.75);
        else if (event.key === "PageDown" || event.key === " ") delta = Math.max(360, window.innerHeight * 0.75);
        else if (event.key === "Home") delta = -100000;
        else if (event.key === "End") delta = 100000;
        if (delta && scrollParentBy(0, delta)) {
          event.preventDefault();
        }
      });
    })();
    </script>
    """


def _to_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except Exception:
        return 0


def _signed(value: int) -> str:
    if value > 0:
        return f"+{value}"

    if value < 0:
        return str(value)

    return ""


def _pdf_status_key(path: Path) -> str:
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        resolved = str(path)
    return hashlib.sha256(resolved.encode("utf-8", "ignore")).hexdigest()


def _read_status_index() -> dict[str, Any]:
    if not JUDGEMENT_STATUS_INDEX.exists():
        return {"documents": {}}

    try:
        with JUDGEMENT_STATUS_INDEX.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"documents": {}}

    if not isinstance(data, dict):
        return {"documents": {}}

    documents = data.get("documents")
    if not isinstance(documents, dict):
        data["documents"] = {}

    return data


def _write_status_index(data: dict[str, Any]) -> None:
    _ensure_dir(JUDGEMENT_STATUS_DIR)
    with JUDGEMENT_STATUS_INDEX.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _remember_judgement_artifacts(artifacts: dict[str, Any]) -> None:
    pdf_path = Path(artifacts.get("pdf_path") or "")
    if not pdf_path.exists():
        return

    artifact_key = str(artifacts.get("artifact_key") or "")
    if not artifact_key:
        return

    summary_dir = _ensure_dir(JUDGEMENT_STATUS_DIR / artifact_key)
    summary_before = Path(artifacts.get("summary_before_path") or "")
    summary_after = Path(artifacts.get("summary_after_path") or "")

    persisted_before = summary_dir / "summary_before.csv"
    persisted_after = summary_dir / "summary_after.csv"

    try:
        if summary_before.exists():
            shutil.copy2(summary_before, persisted_before)
        if summary_after.exists():
            shutil.copy2(summary_after, persisted_after)
    except OSError:
        return

    try:
        stat = pdf_path.stat()
        pdf_sig = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        pdf_sig = {}

    data = _read_status_index()
    docs = data.setdefault("documents", {})
    # 단일 출처 원칙: summary_counts 는 이미 파일로 저장된 summary_after.csv 에서 읽는다.
    # 이렇게 하면 Gmail·시뮬레이션·판정 세 섹션이 모두 같은 파일을 참조한다.
    summary_counts = {}
    if persisted_after.exists():
        _rows = _read_summary_rows_by_position(persisted_after)
        _totals = _overall_from_rows(_rows)
        summary_counts = {
            "pass": _to_int(_totals.get("pass", 0)),
            "fail": _to_int(_totals.get("fail", 0)),
            "hold": _to_int(_totals.get("hold", 0)),
            "total": _to_int(_totals.get("total", 0)),
        }

    docs[_pdf_status_key(pdf_path)] = {
        "status": "completed",
        "cache_version": JUDGEMENT_CACHE_VERSION,
        "artifact_key": artifact_key,
        "pdf_path": str(pdf_path),
        "pdf_sig": pdf_sig,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary_counts": summary_counts,
        "summary_before_path": str(persisted_before),
        "summary_after_path": str(persisted_after),
        "permit_paths": [str(path) for path in artifacts.get("permit_paths", [])],
        "company": str(artifacts.get("company") or ""),
        "product": str(artifacts.get("product") or ""),
        "detail_fingerprint": str(artifacts.get("detail_fingerprint") or ""),
        "detail_sources": list(artifacts.get("detail_sources", []) or []),
    }
    _write_status_index(data)


# ============================================================
# sp_pdf_judger 결과 -> summary_before.csv / summary_after.csv
# ============================================================

def _write_summary_csv_from_result(result: Any, output_path: Path) -> Path:
    """
    sp_pdf_judger 판정 결과에서 제조요약도별 summary CSV를 생성한다.

    컬럼:
    제조명, 검수합격, 검수불합격, 검수보류, 총 계
    """
    if _is_albumin_document(result):
        try:
            export_dir = _ensure_dir(output_path.parent / f"{output_path.stem}_stage_summary_source")
            export_results = export_stage_csvs(result=result, output_dir=export_dir)
            source_summary = _write_export_summary_csv(
                export_results=export_results,
                csv_dir=export_dir,
            )
            shutil.copy2(source_summary, output_path)
            return output_path
        except Exception:
            pass

    cards = build_stage_info_cards(result)

    rows: list[dict[str, Any]] = []

    for card in cards:
        name = _clean_stage_name(card.display_name)

        if not name:
            continue

        rows.append(
            {
                "제조명": name,
                "검수합격": int(card.passed),
                "검수불합격": int(card.failed),
                "검수보류": int(card.held),
                "총 계": int(card.total),
            }
        )

    merged: dict[str, dict[str, Any]] = {}

    for row in rows:
        name = str(row["제조명"])

        if name not in merged:
            merged[name] = {
                "제조명": name,
                "검수합격": 0,
                "검수불합격": 0,
                "검수보류": 0,
                "총 계": 0,
            }

        merged[name]["검수합격"] += int(row["검수합격"])
        merged[name]["검수불합격"] += int(row["검수불합격"])
        merged[name]["검수보류"] += int(row["검수보류"])
        merged[name]["총 계"] += int(row["총 계"])

    final_rows = list(merged.values())

    total_pass = sum(int(row["검수합격"]) for row in final_rows)
    total_fail = sum(int(row["검수불합격"]) for row in final_rows)
    total_hold = sum(int(row["검수보류"]) for row in final_rows)
    total_count = sum(int(row["총 계"]) for row in final_rows)

    final_rows.append(
        {
            "제조명": "전체",
            "검수합격": total_pass,
            "검수불합격": total_fail,
            "검수보류": total_hold,
            "총 계": total_count,
        }
    )

    _ensure_dir(output_path.parent)

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["제조명", "검수합격", "검수불합격", "검수보류", "총 계"],
        )
        writer.writeheader()
        writer.writerows(final_rows)

    return output_path


def _read_summary_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []

    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_summary_rows_by_position(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except Exception:
        return []

    normalized: list[dict[str, str]] = []
    for row in rows[1:]:
        if not row or not any(str(cell).strip() for cell in row):
            continue
        padded = row + [""] * 5
        normalized.append(
            {
                "name": str(padded[0]).strip(),
                "pass": str(padded[1]).strip(),
                "fail": str(padded[2]).strip(),
                "hold": str(padded[3]).strip(),
                "total": str(padded[4]).strip(),
            }
        )
    return normalized


# ============================================================
# 제조명 / 그래프 노드명 일반화 매칭
# ============================================================

def _normalize_stage_text(value: str) -> str:
    text = str(value or "")
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()

    text = text.replace("∙", ".").replace("ㆍ", ".").replace("·", ".")

    # 표기/OCR 흔들림 보정
    text = re.sub(r"e\s*\.?\s*colli", "ecoli", text)
    text = re.sub(r"e\s*\.?\s*coli", "ecoli", text)
    text = text.replace("chol", "cho")
    text = text.replace("컴포넌트", "component")

    # component A/B류 통합
    text = re.sub(r"component\s*([a-z0-9]+)", r"component\1", text)

    # 한글 공정명 공백 통합
    text = re.sub(r"마스터\s*세포주", "마스터세포주", text)
    text = re.sub(r"제조용\s*세포주", "제조용세포주", text)
    text = re.sub(r"중간체\s*원액", "중간체원액", text)
    text = re.sub(r"최종\s*원액", "최종원액", text)
    text = re.sub(r"완제\s*의약품", "완제의약품", text)
    text = re.sub(r"나노\s*파티클", "나노파티클", text)
    text = re.sub(r"나노\s*피티클", "나노파티클", text)

    # 비교용 compact key
    text = re.sub(r"[\s_\-./(){}\[\]:：,]+", "", text)

    return text


def _stage_tokens(value: str) -> set[str]:
    key = _normalize_stage_text(value)
    tokens: set[str] = set()

    terms = [
        "cho",
        "ecoli",
        "componenta",
        "componentb",
        "component",
        "마스터세포주",
        "제조용세포주",
        "세포주",
        "중간체원액",
        "중간체",
        "최종원액",
        "원액",
        "완제의약품",
        "완제",
        "의약품",
        "나노파티클",
        "나노",
    ]

    for term in terms:
        if term in key:
            tokens.add(term)

    m = re.search(r"component([a-z0-9]+)", key)
    if m:
        tokens.add("component")
        tokens.add(f"component{m.group(1)}")
        tokens.add(m.group(1))

    return tokens


def _is_blocked_graph_label(label: str) -> bool:
    key = _normalize_stage_text(label)

    if not key:
        return True

    blocked = {
        "start",
        "end",
        "ocr",
        "llm",
        "기준",
        "약전",
        "허가서",
        "시험기준판별",
        "분석선행관계",
        "허가서참고",
        "기준약전",
    }

    if key in blocked:
        return True

    return False


def _stage_match_score(left: str, right: str) -> float:
    if _is_blocked_graph_label(left) or _is_blocked_graph_label(right):
        return 0.0

    left_key = _normalize_stage_text(left)
    right_key = _normalize_stage_text(right)

    if not left_key or not right_key:
        return 0.0

    if left_key == right_key:
        return 1.0

    if len(left_key) >= 4 and len(right_key) >= 4:
        if left_key in right_key or right_key in left_key:
            return 0.95

    left_tokens = _stage_tokens(left)
    right_tokens = _stage_tokens(right)

    shared = left_tokens & right_tokens
    union = left_tokens | right_tokens

    token_score = len(shared) / max(len(union), 1) if union else 0.0
    char_score = SequenceMatcher(None, left_key, right_key).ratio()

    score = char_score * 0.45 + token_score * 0.55

    # Component A/B는 섞이면 안 됨
    left_component = ""
    right_component = ""

    lm = re.search(r"component([a-z0-9]+)", left_key)
    rm = re.search(r"component([a-z0-9]+)", right_key)

    if lm:
        left_component = lm.group(1)

    if rm:
        right_component = rm.group(1)

    if left_component and right_component:
        if left_component == right_component:
            score += 0.2
        else:
            score -= 0.45

    # 의미 토큰이 하나도 안 겹치면 과매칭 방지
    if not shared:
        score = min(score, 0.42)

    return max(0.0, min(1.0, score))


def _overall_from_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    positional_rows = [
        row for row in rows
        if {"name", "pass", "fail", "hold", "total"} <= set(row.keys())
    ]

    if positional_rows:
        total_markers = {"전체", "총계", "합계", "total", "overall"}
        total_row = next(
            (row for row in positional_rows if str(row.get("name", "")).strip().casefold() in total_markers),
            None,
        )
        if total_row is None:
            return {
                "pass": sum(_to_int(row.get("pass", 0)) for row in positional_rows),
                "fail": sum(_to_int(row.get("fail", 0)) for row in positional_rows),
                "hold": sum(_to_int(row.get("hold", 0)) for row in positional_rows),
                "total": sum(_to_int(row.get("total", 0)) for row in positional_rows),
            }
        return {
            "pass": _to_int(total_row.get("pass", 0)),
            "fail": _to_int(total_row.get("fail", 0)),
            "hold": _to_int(total_row.get("hold", 0)),
            "total": _to_int(total_row.get("total", 0)),
        }

    total_row = None
    for row in rows:
        values = [str(value).strip().casefold() for value in row.values()]
        if any(value in {"전체", "총계", "합계", "total", "overall"} for value in values):
            total_row = row
            break

    if total_row is None:
        return {
            "pass": sum(_to_int(_first_row_value(row, ("pass", "합격"))) for row in rows),
            "fail": sum(_to_int(_first_row_value(row, ("fail", "불합격"))) for row in rows),
            "hold": sum(_to_int(_first_row_value(row, ("hold", "보류"))) for row in rows),
            "total": sum(_to_int(_first_row_value(row, ("total", "전체", "총"))) for row in rows),
        }

    return {
        "pass": _to_int(_first_row_value(total_row, ("pass", "합격"))),
        "fail": _to_int(_first_row_value(total_row, ("fail", "불합격"))),
        "hold": _to_int(_first_row_value(total_row, ("hold", "보류"))),
        "total": _to_int(_first_row_value(total_row, ("total", "전체", "총"))),
    }


def _first_row_value(row: dict[str, Any], needles: tuple[str, ...]) -> Any:
    for key, value in row.items():
        norm_key = str(key).casefold()
        if any(needle in norm_key for needle in needles):
            return value
    return 0


def _summary_entries(summary_path: Path) -> list[dict[str, Any]]:
    rows = _read_summary_rows_by_position(summary_path)
    total_markers = {"전체", "총계", "합계", "total", "overall"}

    if not rows:
        rows = []
        for row in _read_summary_csv(summary_path):
            name = _first_row_value(row, ("name", "제조", "stage"))
            if not name:
                values = [str(value).strip() for value in row.values() if str(value).strip()]
                name = values[0] if values else ""
            rows.append(
                {
                    "name": str(name).strip(),
                    "pass": _first_row_value(row, ("pass", "합격")),
                    "fail": _first_row_value(row, ("fail", "불합격")),
                    "hold": _first_row_value(row, ("hold", "보류")),
                    "total": _first_row_value(row, ("total", "전체", "총")),
                }
            )

    entries: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name or name.casefold() in total_markers:
            continue
        entries.append(
            {
                "name": name,
                "counts": {
                    "pass": _to_int(row.get("pass", 0)),
                    "fail": _to_int(row.get("fail", 0)),
                    "hold": _to_int(row.get("hold", 0)),
                    "total": _to_int(row.get("total", 0)),
                },
            }
        )
    return entries


def _node_label_from_dict(obj: dict[str, Any]) -> str:
    labels = []

    for key in [
        "id",
        "key",
        "name",
        "label",
        "title",
        "display_name",
        "stage_name",
        "text",
        "node_name",
    ]:
        value = obj.get(key)

        if value:
            labels.append(str(value))

    return " ".join(labels)


def _best_counts_for_label(label: str, entries: list[dict[str, Any]]) -> dict[str, int] | None:
    best_score = 0.0
    best_counts = None

    for entry in entries:
        score = _stage_match_score(label, str(entry["name"]))

        if score > best_score:
            best_score = score
            best_counts = entry["counts"]

    if best_score < 0.55:
        return None

    return dict(best_counts)


def _apply_counts_to_node_dict(obj: dict[str, Any], counts: dict[str, int]) -> None:
    passed = int(counts.get("pass", 0))
    failed = int(counts.get("fail", 0))
    held = int(counts.get("hold", 0))
    total = int(counts.get("total", 0))

    pass_keys = [
        "pass",
        "passed",
        "ok",
        "success",
        "green",
        "pass_count",
        "passed_count",
        "ok_count",
        "green_count",
        "검수합격",
        "합격",
    ]

    fail_keys = [
        "fail",
        "failed",
        "ng",
        "red",
        "fail_count",
        "failed_count",
        "ng_count",
        "red_count",
        "검수불합격",
        "불합격",
    ]

    hold_keys = [
        "hold",
        "held",
        "pending",
        "warning",
        "yellow",
        "orange",
        "hold_count",
        "held_count",
        "pending_count",
        "warning_count",
        "yellow_count",
        "orange_count",
        "검수보류",
        "보류",
    ]

    total_keys = [
        "total",
        "count",
        "total_count",
        "white",
        "white_count",
        "전체",
        "총계",
        "총 계",
    ]

    for key in pass_keys:
        obj[key] = passed

    for key in fail_keys:
        obj[key] = failed

    for key in hold_keys:
        obj[key] = held

    for key in total_keys:
        obj[key] = total

    for nested_key in [
        "counts",
        "summary",
        "stats",
        "result",
        "judgement",
        "after",
        "before",
    ]:
        nested = obj.get(nested_key)

        if isinstance(nested, dict):
            for key in pass_keys:
                nested[key] = passed

            for key in fail_keys:
                nested[key] = failed

            for key in hold_keys:
                nested[key] = held

            for key in total_keys:
                nested[key] = total



def patch_graph_json_with_summary(graph_json: str, summary_path: Path) -> str:
    """
    최종 summary CSV 값을 graph_json 내부 노드 카운트에 반영한다.
    시뮬레이션에서는 판정 완료 후 생성된 summary_after.csv만 넘긴다.
    """
    entries = _summary_entries(summary_path)

    if not entries:
        return graph_json

    try:
        data = json.loads(graph_json)
    except Exception:
        return graph_json

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            label = _node_label_from_dict(obj)
            counts = _best_counts_for_label(label, entries)

            if counts is not None:
                _apply_counts_to_node_dict(obj, counts)

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)

    return json.dumps(data, ensure_ascii=False)


def attach_after_summary_targets(graph_json: str, summary_path: Path) -> str:
    """Attach summary_after counts as animation targets without changing before counts."""
    entries = _summary_entries(summary_path)
    if not entries:
        return graph_json

    try:
        data = json.loads(graph_json)
    except Exception:
        return graph_json

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            label = _node_label_from_dict(obj)
            counts = _best_counts_for_label(label, entries)
            if counts is not None:
                obj["after_pass"] = int(counts.get("pass", 0))
                obj["after_fail"] = int(counts.get("fail", 0))
                obj["after_hold"] = int(counts.get("hold", 0))
                obj["after_total"] = int(counts.get("total", 0))

            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return json.dumps(data, ensure_ascii=False)


# ============================================================
# 기존 시뮬레이션 CSV 폴더에 summary_before/after 연결
# ============================================================

def _prepare_runtime_simulation_csv_dir(
    original_csv_dir: Path,
    root_dir: Path,
    summary_before_path: Path,
    summary_after_path: Path,
) -> Path:
    """
    기존 sp_app.py 시뮬레이션 구조를 유지하기 위해
    원래 그래프 CSV 폴더를 복사하고 그 주변에 새 summary_before/after를 배치한다.
    """
    runtime_parent = _ensure_dir(root_dir / "runtime_simulation_source")
    runtime_csv_dir = runtime_parent / original_csv_dir.name

    if runtime_csv_dir.exists():
        shutil.rmtree(runtime_csv_dir)

    shutil.copytree(original_csv_dir, runtime_csv_dir)

    # csv_dir.parent 기준 대응
    shutil.copy2(summary_before_path, runtime_parent / "summary_before.csv")
    shutil.copy2(summary_after_path, runtime_parent / "summary_after.csv")
    shutil.copy2(summary_after_path, runtime_parent / "summary.csv")

    # csv_dir 내부 기준 대응
    shutil.copy2(summary_before_path, runtime_csv_dir / "summary_before.csv")
    shutil.copy2(summary_after_path, runtime_csv_dir / "summary_after.csv")
    shutil.copy2(summary_after_path, runtime_csv_dir / "summary.csv")

    return runtime_csv_dir


# ============================================================
# 판정 아티팩트 생성
# ============================================================

def _attach_rule_regression_log(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Persist a non-UI regression trace without changing judgement output."""
    result = artifacts.get("after_result")
    if result is None:
        return artifacts

    project_root = Path(__file__).resolve().parent
    try:
        logged = append_rule_regression_log(
            result,
            project_root=project_root,
            permit_paths=artifacts.get("permit_paths", []),
            summary_counts_path=artifacts.get("summary_after_path"),
        )
        artifacts["rule_regression_workbook_path"] = logged.workbook_path
        artifacts["rule_regression_run_id"] = logged.run_id
        artifacts["rule_regression_appended"] = logged.appended
        artifacts.pop("rule_regression_error", None)
        write_output_index(logged.workbook_path.parent)
    except Exception as exc:
        # Regression logging is observability only. It must never block judgement.
        artifacts["rule_regression_error"] = f"{type(exc).__name__}: {exc}"
    return artifacts


def ensure_judgement_artifacts(
    *,
    pdf_path: Path,
    permit_paths: list[Path],
    original_csv_dir: Path | None,
    company: str = "",
    product: str = "",
) -> dict[str, Any]:
    """
    실제 sp_pdf_judger를 실행해서 before/after 판정 결과와 summary CSV를 만든다.

    before_result:
      permit_pdf_paths=[] 로 실행
      -> summary_before.csv

    after_result:
      permit_pdf_paths=permit_paths 로 실행
      -> summary_after.csv
    """
    pdf_path = Path(pdf_path)
    permit_paths = [Path(path) for path in permit_paths if Path(path).exists()]
    original_csv_dir = Path(original_csv_dir) if original_csv_dir else None
    company = str(company or "").strip()
    product = str(product or "").strip()
    detail_profile = resolve_domain_detail_profile(company, product)

    key = _artifact_key(
        pdf_path,
        permit_paths,
        original_csv_dir,
        company,
        product,
        detail_profile.fingerprint,
    )
    session_key = f"sp_direct_judgement_artifacts::{key}"

    if session_key in st.session_state:
        _attach_rule_regression_log(st.session_state[session_key])
        _remember_judgement_artifacts(st.session_state[session_key])
        return st.session_state[session_key]

    root_dir = _ensure_dir(
        Path(tempfile.gettempdir())
        / "sp_app_direct_judgement"
        / key
    )

    before_stage_dir = _ensure_dir(root_dir / "before_stage_csv")
    after_stage_dir = _ensure_dir(root_dir / "after_stage_csv")

    summary_before_path = root_dir / "summary_before.csv"
    summary_after_path = root_dir / "summary_after.csv"
    result_cache_path = root_dir / "judgement_artifacts.pkl"

    if result_cache_path.exists():
        try:
            artifacts = pickle.loads(result_cache_path.read_bytes())
            if (
                    isinstance(artifacts, dict)
                    and artifacts.get("after_result") is not None
                    and artifacts.get("cache_version") == JUDGEMENT_CACHE_VERSION
                ):
                artifacts["root_dir"] = root_dir
                artifacts["summary_before_path"] = summary_before_path
                artifacts["summary_after_path"] = summary_after_path
                artifacts["before_stage_dir"] = before_stage_dir
                artifacts["after_stage_dir"] = after_stage_dir
                artifacts["pdf_path"] = pdf_path
                artifacts["permit_paths"] = permit_paths
                if original_csv_dir and original_csv_dir.exists():
                    artifacts["runtime_csv_dir"] = _prepare_runtime_simulation_csv_dir(
                        original_csv_dir=original_csv_dir,
                        root_dir=root_dir,
                        summary_before_path=summary_before_path,
                        summary_after_path=summary_after_path,
                    )
                st.session_state[session_key] = artifacts
                st.session_state["latest_judgement_artifact_key"] = key
                st.session_state["latest_judgement_pdf_path"] = str(pdf_path)
                _attach_rule_regression_log(artifacts)
                _remember_judgement_artifacts(artifacts)
                return artifacts
        except Exception:
            pass

    spinner_text = "문서 기준과 참고 근거를 대조해 판정 데이터를 생성하고 있습니다..."

    with st.spinner(spinner_text):
        before_pipeline = DocumentJudgePipeline(
            permit_pdf_paths=[],
            company=company,
            product=product,
        )
        before_result = before_pipeline.run(pdf_path)

        if permit_paths:
            after_pipeline = DocumentJudgePipeline(
                permit_pdf_paths=permit_paths,
                company=company,
                product=product,
            )
            after_result = after_pipeline.run(
                pdf_path,
                extracted_records=before_result.extracted_records,
                static_result=before_result,
            )
        else:
            after_result = before_result

        _write_summary_csv_from_result(before_result, summary_before_path)
        _write_summary_csv_from_result(after_result, summary_after_path)

        try:
            make_stage_csv_zip(
                result=before_result,
                output_dir=before_stage_dir,
                zip_name=f"{pdf_path.stem}_before_제조요약도별_CSV.zip",
            )
        except Exception:
            pass

        try:
            make_stage_csv_zip(
                result=after_result,
                output_dir=after_stage_dir,
                zip_name=f"{pdf_path.stem}_after_제조요약도별_CSV.zip",
            )
        except Exception:
            pass

    runtime_csv_dir = None

    if original_csv_dir and original_csv_dir.exists():
        runtime_csv_dir = _prepare_runtime_simulation_csv_dir(
            original_csv_dir=original_csv_dir,
            root_dir=root_dir,
            summary_before_path=summary_before_path,
            summary_after_path=summary_after_path,
        )

    artifacts = {
        "artifact_key": key,
        "cache_version": JUDGEMENT_CACHE_VERSION,
        "root_dir": root_dir,
        "before_result": before_result,
        "after_result": after_result,
        "summary_before_path": summary_before_path,
        "summary_after_path": summary_after_path,
        "before_stage_dir": before_stage_dir,
        "after_stage_dir": after_stage_dir,
        "runtime_csv_dir": runtime_csv_dir,
        "pdf_path": pdf_path,
        "permit_paths": permit_paths,
        "company": company,
        "product": product,
        "detail_fingerprint": detail_profile.fingerprint,
        "detail_sources": detail_profile.sources,
    }

    _attach_rule_regression_log(artifacts)
    st.session_state[session_key] = artifacts
    st.session_state["latest_judgement_artifact_key"] = key
    st.session_state["latest_judgement_pdf_path"] = str(pdf_path)
    _remember_judgement_artifacts(artifacts)

    try:
        result_cache_path.write_bytes(pickle.dumps(artifacts))
    except Exception:
        pass

    return artifacts


# ============================================================
# before/after 집계 패널
# ============================================================

def _summary_style() -> str:
    return """
    <style>
    .phase-wrap {
        margin: 8px 0 16px 0;
        padding: 14px 16px;
        border: 1px solid #3a4442;
        border-radius: 18px;
        background: rgba(20, 28, 27, 0.82);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        color: #e5e7eb;
    }

    .phase-card {
        display: inline-block;
        vertical-align: top;
        width: calc(50% - 32px);
        min-width: 260px;
        padding: 14px 16px;
        border-radius: 14px;
        background: rgba(12, 18, 18, 0.76);
        border: 1px solid #3a4442;
    }

    .phase-card.before {
        border-left: 5px solid #f59e0b;
    }

    .phase-card.after {
        border-left: 5px solid #22c55e;
    }

    .phase-arrow {
        display: inline-flex;
        width: 48px;
        height: 112px;
        align-items: center;
        justify-content: center;
        font-size: 24px;
        font-weight: 900;
        color: #9ca3af;
    }

    .phase-kicker {
        font-size: 11px;
        font-weight: 900;
        color: #9ca3af;
        margin-bottom: 4px;
    }

    .phase-title {
        font-size: 17px;
        font-weight: 900;
        color: #f9fafb;
        margin-bottom: 10px;
    }

    .phase-grid {
        display: grid;
        grid-template-columns: 1fr 76px;
        row-gap: 6px;
        font-size: 13px;
    }

    .phase-grid span {
        color: #cbd5e1;
        font-weight: 800;
    }

    .phase-grid b {
        text-align: right;
        color: #ffffff;
    }

    .phase-grid em {
        font-style: normal;
        font-size: 11px;
        color: #60a5fa;
        margin-left: 4px;
    }

    .phase-note {
        margin-top: 12px;
        font-size: 13px;
        color: #cbd5e1;
        font-weight: 700;
    }

    .summary-details {
        margin: 8px 0;
        border: 1px solid #3a4442;
        border-radius: 12px;
        padding: 9px 12px;
        background: rgba(12, 18, 18, 0.76);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        color: #e5e7eb;
    }

    .summary-details summary {
        cursor: pointer;
        font-weight: 900;
        color: #f9fafb;
    }

    .summary-table-box {
        overflow-x: auto;
        margin-top: 10px;
    }

    .summary-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
    }

    .summary-table th,
    .summary-table td {
        border: 1px solid #3a4442;
        padding: 7px 8px;
        text-align: left;
    }

    .summary-table th {
        background: rgba(31, 41, 55, 0.72);
        font-weight: 900;
    }
    </style>
    """


# ============================================================
# 시뮬레이션 HTML JS 주입
# ============================================================

def _render_summary_table(title: str, rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""

    normalized = []
    for row in rows:
        if {"name", "pass", "fail", "hold", "total"} <= set(row.keys()):
            normalized.append(row)
        else:
            values = [str(value).strip() for value in row.values()]
            padded = values + [""] * 5
            normalized.append(
                {
                    "name": padded[0],
                    "pass": padded[1],
                    "fail": padded[2],
                    "hold": padded[3],
                    "total": padded[4],
                }
            )

    body = []
    for row in normalized:
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('name', '')))}</td>"
            f"<td>{html.escape(str(row.get('pass', '0')))}</td>"
            f"<td>{html.escape(str(row.get('fail', '0')))}</td>"
            f"<td>{html.escape(str(row.get('hold', '0')))}</td>"
            f"<td>{html.escape(str(row.get('total', '0')))}</td>"
            "</tr>"
        )

    return f"""
    <details class="summary-details">
      <summary>{html.escape(title)}</summary>
      <div class="summary-table-box">
        <table class="summary-table">
          <thead>
            <tr>
              <th>제조명</th>
              <th>충족</th>
              <th>불충족</th>
              <th>보류</th>
              <th>전체</th>
            </tr>
          </thead>
          <tbody>{''.join(body)}</tbody>
        </table>
      </div>
    </details>
    """


def render_simulation_summary_panels(artifacts: dict[str, Any]) -> None:
    after_rows = _read_summary_rows_by_position(Path(artifacts["summary_after_path"]))

    st.markdown(
        _summary_style()
        + _render_summary_table("최종 판정 상세 집계", after_rows),
        unsafe_allow_html=True,
    )


def inject_after_summary_transition(html_text: str, after_graph_json: str) -> str:
    """
    시뮬레이션 수치는 최종 summary 기준으로 유지한다.
    남아 있는 이벤트 hook은 기존 iframe 이벤트 호환용이다.
    """
    safe_after_json_literal = json.dumps(after_graph_json, ensure_ascii=False)

    injection = r"""

// ===== 최종 판정 후 summary_after.csv 수치 전환 =====
try {
  const __SP_AFTER_GRAPH_JSON_TEXT__ = __AFTER_GRAPH_JSON_TEXT__;
  const __SP_AFTER_GRAPH__ = JSON.parse(__SP_AFTER_GRAPH_JSON_TEXT__);

  function __spNormStageText(value) {
    let text = String(value || '').toLowerCase();

    text = text
      .replace(/e\s*\.?\s*colli/g, 'ecoli')
      .replace(/e\s*\.?\s*coli/g, 'ecoli')
      .replace(/chol/g, 'cho')
      .replace(/컴포넌트/g, 'component')
      .replace(/component\s*([a-z0-9]+)/g, 'component$1')
      .replace(/마스터\s*세포주/g, '마스터세포주')
      .replace(/제조용\s*세포주/g, '제조용세포주')
      .replace(/중간체\s*원액/g, '중간체원액')
      .replace(/최종\s*원액/g, '최종원액')
      .replace(/완제\s*의약품/g, '완제의약품')
      .replace(/나노\s*파티클/g, '나노파티클')
      .replace(/나노\s*피티클/g, '나노파티클')
      .replace(/[\s_\-./(){}\[\]:：,]+/g, '');

    return text;
  }

  function __spLabelFromObj(obj) {
    if (!obj || typeof obj !== 'object') return '';

    const keys = [
      'id',
      'key',
      'name',
      'label',
      'title',
      'display_name',
      'stage_name',
      'text',
      'node_name'
    ];

    const parts = [];

    for (const key of keys) {
      if (obj[key] !== undefined && obj[key] !== null) {
        parts.push(String(obj[key]));
      }
    }

    return parts.join(' ');
  }

  function __spNum(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? n : 0;
  }

  function __spCountsFromObj(obj) {
    if (!obj || typeof obj !== 'object') return null;

    const passValue =
      obj.pass ?? obj.passed ?? obj.ok ?? obj.green ??
      obj.pass_count ?? obj.passed_count ?? obj.ok_count ?? obj.green_count ??
      obj['검수합격'] ?? obj['합격'];

    const failValue =
      obj.fail ?? obj.failed ?? obj.ng ?? obj.red ??
      obj.fail_count ?? obj.failed_count ?? obj.ng_count ?? obj.red_count ??
      obj['검수불합격'] ?? obj['불합격'];

    const holdValue =
      obj.hold ?? obj.held ?? obj.pending ?? obj.warning ?? obj.yellow ?? obj.orange ??
      obj.hold_count ?? obj.held_count ?? obj.pending_count ?? obj.warning_count ??
      obj.yellow_count ?? obj.orange_count ??
      obj['검수보류'] ?? obj['보류'];

    const totalValue =
      obj.total ?? obj.count ?? obj.total_count ?? obj.white ?? obj.white_count ??
      obj['전체'] ?? obj['총계'] ?? obj['총 계'];

    const p = __spNum(passValue);
    const f = __spNum(failValue);
    const h = __spNum(holdValue);
    const t = __spNum(totalValue || (p + f + h));

    if (p === 0 && f === 0 && h === 0 && t === 0) return null;

    return {
      pass: p,
      fail: f,
      hold: h,
      total: t
    };
  }

  function __spCollectAfterCounts(root) {
    const out = {};

    function walk(obj) {
      if (Array.isArray(obj)) {
        for (const item of obj) walk(item);
        return;
      }

      if (!obj || typeof obj !== 'object') return;

      const label = __spLabelFromObj(obj);
      const key = __spNormStageText(label);
      const counts = __spCountsFromObj(obj);

      if (key && counts) {
        out[key] = counts;
      }

      for (const value of Object.values(obj)) {
        walk(value);
      }
    }

    walk(root);
    return out;
  }

  const __SP_AFTER_COUNTS__ = __spCollectAfterCounts(__SP_AFTER_GRAPH__);

  function __spBestCountsForNode(nodeKey, nodeLabel) {
    const key = __spNormStageText(nodeKey + ' ' + nodeLabel);

    if (!key) return null;

    if (__SP_AFTER_COUNTS__[key]) {
      return __SP_AFTER_COUNTS__[key];
    }

    let bestCounts = null;
    let bestScore = 0;

    for (const [candidateKey, counts] of Object.entries(__SP_AFTER_COUNTS__)) {
      if (!candidateKey) continue;

      let score = 0;

      if (key === candidateKey) {
        score = 1;
      } else if (key.includes(candidateKey) || candidateKey.includes(key)) {
        score = 0.92;
      } else {
        const common = [...candidateKey].filter(ch => key.includes(ch)).length;
        const denom = Math.max(candidateKey.length, key.length, 1);
        score = common / denom;
      }

      if (score > bestScore) {
        bestScore = score;
        bestCounts = counts;
      }
    }

    if (bestScore < 0.55) return null;

    return bestCounts;
  }

  function __spSvg(tag, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);

    for (const [key, value] of Object.entries(attrs || {})) {
      el.setAttribute(key, value);
    }

    return el;
  }

  function __spRemoveOldOverlay() {
    const olds = document.querySelectorAll('[data-sp-after-overlay="1"]');
    olds.forEach(el => el.remove());
  }

  function __spDrawAfterOverlayForNode(nodeKey, node, counts) {
    if (!node || !counts) return;

    const x = Number(node.x || 0);
    const y = Number(node.y || 0);
    const w = Number(node.w || 120);
    const h = Number(node.h || 42);

    if (!x || !y) return;

    const g = __spSvg('g', {
      'data-sp-after-overlay': '1'
    });

    const baseY = y + h / 2 + 23;
    const baseX = x - 47;

    const bg = __spSvg('rect', {
      x: x - 62,
      y: baseY - 18,
      width: 124,
      height: 45,
      rx: 8,
      fill: 'rgba(31, 39, 39, 0.98)'
    });
    g.appendChild(bg);

    const items = [
      { label: 'pass', value: counts.pass, color: '#20c997', dx: 0 },
      { label: 'hold', value: counts.hold, color: '#f59e0b', dx: 35 },
      { label: 'fail', value: counts.fail, color: '#ef4444', dx: 70 }
    ];

    for (const item of items) {
      const cx = baseX + item.dx;

      g.appendChild(__spSvg('circle', {
        cx: cx,
        cy: baseY,
        r: 5,
        fill: item.color
      }));

      const text = __spSvg('text', {
        x: cx + 9,
        y: baseY + 4,
        fill: '#f8fafc',
        'font-size': '12',
        'font-weight': '800'
      });
      text.textContent = String(item.value);
      g.appendChild(text);
    }

    const totalDotX = baseX + 24;
    const totalY = baseY + 24;

    g.appendChild(__spSvg('circle', {
      cx: totalDotX,
      cy: totalY,
      r: 5,
      fill: '#ffffff'
    }));

    const totalText = __spSvg('text', {
      x: totalDotX + 9,
      y: totalY + 4,
      fill: '#f8fafc',
      'font-size': '12',
      'font-weight': '900'
    });
    totalText.textContent = String(counts.total);
    g.appendChild(totalText);

    const totalLabel = __spSvg('text', {
      x: totalDotX + 35,
      y: totalY + 4,
      fill: '#94a3b8',
      'font-size': '10',
      'font-weight': '700'
    });
    totalLabel.textContent = '전체';
    g.appendChild(totalLabel);

    try {
      pipeL.appendChild(g);
    } catch (e) {
      const svg = document.querySelector('svg');
      if (svg) svg.appendChild(g);
    }
  }

  let __spAfterApplied = false;

  function __spApplyAfterCounts() {
    if (__spAfterApplied) return;
    __spAfterApplied = true;

    __spRemoveOldOverlay();

    if (typeof PNODE !== 'object' || !PNODE) return;

    for (const [nodeKey, node] of Object.entries(PNODE)) {
      const nodeLabel = [
        nodeKey,
        node && node.name,
        node && node.label,
        node && node.title,
        node && node.text
      ].filter(Boolean).join(' ');

      const counts = __spBestCountsForNode(nodeKey, nodeLabel);

      if (!counts) continue;

      node.pass = counts.pass;
      node.fail = counts.fail;
      node.hold = counts.hold;
      node.total = counts.total;
      node.passed = counts.pass;
      node.failed = counts.fail;
      node.held = counts.hold;

      __spDrawAfterOverlayForNode(nodeKey, node, counts);
    }

    try {
      if (typeof st !== 'undefined') {
        st.textContent = '최종 판정 완료: 최종 집계 기준으로 수치가 갱신되었습니다.';
      }
    } catch (e) {}
  }

  window.__SP_APPLY_AFTER_COUNTS__ = __spApplyAfterCounts;

  document.addEventListener('sp:permit-after-applied', function() {
    __spApplyAfterCounts();
  });

  document.addEventListener('sp:permit-after-finalized', function() {
    __spApplyAfterCounts();
  });

} catch (e) {}

"""

    injection = injection.replace("__AFTER_GRAPH_JSON_TEXT__", safe_after_json_literal)

    markers = [
        "})();\n</script>",
        "})();\r\n</script>",
        "})();</script>",
    ]

    for marker in markers:
        if marker in html_text:
            return html_text.replace(marker, injection + marker)

    return html_text + f"<script>{injection}</script>"


def inject_end_navigation(html_text: str) -> str:
    """
    Deprecated compatibility hook.

    최종 판정 이동 버튼은 Streamlit 본문 하단에만 노출한다. iframe 내부에
    이동 링크를 다시 주입하면 버튼이 중복으로 보이고 parent navigation도
    브라우저 환경에 따라 막힐 수 있어서 여기서는 HTML을 그대로 반환한다.
    """
    return html_text


# ============================================================
# 최종 판정 페이지 렌더링
# ============================================================

def _clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


def render_final_judgement_page(
    *,
    selected_doc: Any,
    original_csv_dir: Path | None,
) -> None:
    if selected_doc is None:
        st.error("최종 판정에 사용할 SP 문서를 찾지 못했습니다.")
        return

    pdf_path = Path(selected_doc.path)

    permit_paths = [
        Path(path)
        for path in getattr(selected_doc, "permit_files", ()) or []
        if Path(path).exists()
    ]

    progress_slot = st.empty()
    progress_slot.markdown(
        """
        <div style="margin:1rem 0;padding:1rem 1.15rem;border:1px solid #bfdbfe;border-radius:14px;
                    background:#eff6ff;color:#174e9b;font-weight:850;">
            최종 판정 데이터를 생성하고 있습니다. 문서 기준과 참고 근거를 대조하는 중이라 문서 크기에 따라 시간이 걸릴 수 있습니다.
        </div>
        """,
        unsafe_allow_html=True,
    )

    artifacts = ensure_judgement_artifacts(
        pdf_path=pdf_path,
        permit_paths=permit_paths,
        original_csv_dir=Path(original_csv_dir) if original_csv_dir else None,
        company=str(getattr(selected_doc, "company", "") or ""),
        product=str(getattr(selected_doc, "product", "") or ""),
    )
    progress_slot.empty()

    result = artifacts["after_result"]

    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            display: none !important;
        }

        [data-testid="collapsedControl"] {
            display: none !important;
        }

        .block-container {
            padding-top: 1.05rem;
            padding-left: 2.2rem;
            padding-right: 2.2rem;
            max-width: 100% !important;
        }
        .final-report-layout { display:grid; grid-template-columns:minmax(560px,1.25fr) minmax(420px,.75fr); gap:26px; align-items:start; margin:.3rem 0 1.2rem; }
        .final-page-frame { padding:18px; border:1px solid #d8e4f1; border-radius:22px; background:#f8fafc; box-shadow:0 12px 30px rgba(15,23,42,.08); }
        .final-page-frame-title { margin:0 0 14px; font-size:26px; font-weight:950; color:#111827; }
        .final-page-frame img { display:block; width:100%; max-width:980px; margin:0 auto; border-radius:14px; border:1px solid #e5e7eb; background:#fff; box-shadow:0 10px 28px rgba(15,23,42,.08); }
        .final-side-card { position:sticky; top:16px; padding:22px; border:1px solid #d8e4f1; border-radius:22px; background:#ffffff; box-shadow:0 12px 30px rgba(15,23,42,.08); }
        .final-kicker { font-size:.9rem; font-weight:950; letter-spacing:.08em; color:#2563eb; }
        .final-doc-title { margin:.35rem 0 .35rem; color:#111827; font-size:32px; line-height:1.25; font-weight:950; }
        .final-doc-sub { margin:0 0 18px; color:#536173; font-size:18px; line-height:1.55; overflow-wrap:anywhere; }
        .final-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.65rem; margin:0 0 18px; }
        .final-metric { min-width:0; padding:.8rem .9rem; border:1px solid #dbeafe; border-radius:13px; background:#f8fbff; }
        .final-metric span { display:block; font-size:15px; color:#64748b; font-weight:850; }
        .final-metric b { display:block; margin-top:.25rem; font-size:18px; color:#172554; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .final-metric.pass b { color:#15803d; } .final-metric.fail b { color:#b91c1c; } .final-metric.hold b { color:#b45309; }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.final-preview-marker), div[data-testid="stVerticalBlockBorderWrapper"]:has(.final-summary-marker) { border-color:#d8e4f1 !important; border-radius:16px !important; background:#fff !important; box-shadow:0 10px 26px rgba(15,23,42,.07) !important; }
        .final-panel-title { margin:0 0 .8rem; font-size:1.02rem; font-weight:900; color:#172554; }
        .final-permit-status { margin-top:.85rem; padding:.7rem .8rem; border-radius:9px; font-size:.82rem; font-weight:750; line-height:1.4; }
        .final-permit-status.applied { color:#14734b; background:#effdf6; border:1px solid #a7efd1; }
        .final-permit-status.missing { color:#9a5b08; background:#fff8e8; border:1px solid #f7d79c; }
        @media (max-width: 1180px) { .final-report-layout { grid-template-columns:1fr; } .final-side-card { position:relative; top:auto; } }
        </style>
        """,
        unsafe_allow_html=True,
    )



    if st.button("← 첫 화면으로 돌아가기"):
        gmail_state = {
            "gmail_logged_in": st.session_state.get("gmail_logged_in", False),
            "gmail_connected_address": st.session_state.get("gmail_connected_address", ""),
            "gmail_confirm_version": st.session_state.get("gmail_confirm_version", ""),
            "gmail_address": st.session_state.get("gmail_address", ""),
            "gmail_app_password": st.session_state.get("gmail_app_password", ""),
            "gmail_subject_keyword": st.session_state.get("gmail_subject_keyword", ""),
            "gmail_since_date": st.session_state.get("gmail_since_date", None),
            "gmail_confirmed_address": st.session_state.get("gmail_confirmed_address", ""),
            "gmail_confirmed_app_password": st.session_state.get("gmail_confirmed_app_password", ""),
            "gmail_confirmed_subject_keyword": st.session_state.get("gmail_confirmed_subject_keyword", ""),
            "gmail_confirmed_since_date": st.session_state.get("gmail_confirmed_since_date", None),
            "gmail_confirmed_search": st.session_state.get("gmail_confirmed_search", ""),
        }
        confirmed_address = str(gmail_state.get("gmail_confirmed_address") or gmail_state.get("gmail_connected_address") or gmail_state.get("gmail_address") or "").strip()
        confirmed_password = str(gmail_state.get("gmail_confirmed_app_password") or gmail_state.get("gmail_app_password") or "").strip()
        st.session_state["run_sim"] = False
        st.session_state["last_gmail_sync_at"] = datetime.now().timestamp()
        st.session_state["last_gmail_sync_error"] = ""
        st.session_state["last_gmail_background_error"] = ""
        st.session_state["gmail_settings_open"] = False
        for key, value in gmail_state.items():
            if value not in (None, ""):
                st.session_state[key] = value
        if confirmed_address and confirmed_password:
            st.session_state["gmail_logged_in"] = True
            st.session_state["gmail_connected_address"] = confirmed_address
            st.session_state["gmail_address"] = confirmed_address
            st.session_state["gmail_app_password"] = confirmed_password
            st.session_state["gmail_confirmed_address"] = confirmed_address
            st.session_state["gmail_confirmed_app_password"] = confirmed_password
            st.session_state["gmail_confirm_version"] = "manual-gmail-confirm-20260602-v2"
        _clear_query_params()
        st.rerun()

    doc_company = str(getattr(selected_doc, "company", "") or "제출 문서")
    doc_product = str(getattr(selected_doc, "product", "") or "제품 정보 없음")
    doc_version = str(getattr(selected_doc, "version", "버전 정보 없음") or "버전 정보 없음")
    received_date = str(getattr(selected_doc, "received_date", "접수일 정보 없음") or "접수일 정보 없음")
    product_number = str(getattr(selected_doc, "product_number", "식별번호 없음") or "식별번호 없음")
    permit_state = f"허가서 {len(permit_paths)}건 연계" if permit_paths else "허가서 미연계"

    # 판정 결과 총합을 CSV 단일 출처에서 읽어 Gmail·시뮬레이션 섹션과 일치시킨다.
    _summary_after_path = Path(artifacts.get("summary_after_path") or "")
    if _summary_after_path.exists():
        _csv_rows = _read_summary_rows_by_position(_summary_after_path)
        _t = _overall_from_rows(_csv_rows)
        display_summary: Summary = Summary(
            passed=_t["pass"],
            failed=_t["fail"],
            held=_t["hold"],
            total=_t["total"],
            comparable_total=_t["total"],
        )
    else:
        display_summary = summarize_stage_info_cards(result)
    preview_html = """
      <div class="final-page-sheet">
        <div class="final-page-missing">
          미리보기 이미지를 생성하지 못했습니다.
        </div>
      </div>
    """
    preview_image_path = getattr(result, "preview_image_path", None)

    if preview_image_path:
        import base64

        preview_b64 = base64.b64encode(Path(preview_image_path).read_bytes()).decode("utf-8")
        preview_html = f"""
          <div class="final-page-sheet">
            <img src="data:image/png;base64,{preview_b64}" />
          </div>
        """

    permit_status_html = (
        f'<div class="final-permit-status applied">허가서 PDF 적용됨 · {html.escape(", ".join(path.name for path in permit_paths))}</div>'
        if permit_paths
        else '<div class="final-permit-status missing">허가서 PDF 미적용</div>'
    )

    # 제조요약도 기준 CSV 다운로드 버튼과 내부 판정 호출 통계는 최종 판정 화면에 표시하지 않는다.

    combined_parts: list[str] = []
    combined_parts.append(
        f"""
        <style>
          html {{ scroll-behavior:smooth; }}
          body {{ margin:0; background:#ffffff; color:#111827; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif; }}
          .final-page-stage {{
            --final-side-width: 520px;
            --final-side-gap: 28px;
            width:100%;
            max-width:1860px;
            margin:0 auto 32px;
            padding:0 calc(var(--final-side-width) + var(--final-side-gap) + 14px) 0 12px;
            box-sizing:border-box;
          }}
          .final-page-canvas {{
            position:relative;
            width:min(980px,100%);
            margin:0 auto;
            overflow:visible;
          }}
          .final-page-sheet {{
            background:#ffffff;
            border:1px solid #d8e4f1;
            border-radius:18px;
            box-shadow:0 18px 46px rgba(15,23,42,.12);
            padding:18px;
          }}
          .final-page-sheet img {{
            display:block;
            width:100%;
            height:auto;
            border-radius:12px;
            border:1px solid #e5e7eb;
            background:#fff;
          }}
          .final-page-missing {{
            padding:22px;
            border:1px dashed #cbd5e1;
            border-radius:14px;
            color:#64748b;
            font-size:18px;
            background:#fff;
          }}
          .final-page-summary-card {{
            position:absolute;
            left:calc(100% + var(--final-side-gap));
            top:42px;
            width:var(--final-side-width);
            box-sizing:border-box;
            padding:34px 38px 36px;
            border:1px solid #d8e4f1;
            border-left:11px solid #2563eb;
            border-radius:28px;
            background:rgba(255,255,255,.98);
            box-shadow:0 24px 54px rgba(15,23,42,.20);
          }}
          .final-card-kicker {{
            color:#2563eb;
            font-size:17px;
            font-weight:950;
            letter-spacing:.12em;
            text-transform:uppercase;
          }}
          .final-card-title {{
            margin:14px 0 9px;
            color:#111827;
            font-size:46px;
            line-height:1.25;
            font-weight:950;
          }}
          .final-card-sub {{
            margin:0 0 25px;
            color:#64748b;
            font-size:23px;
            line-height:1.45;
            font-weight:850;
            overflow-wrap:anywhere;
          }}
          .final-card-counts {{
            display:grid;
            grid-template-columns:repeat(3,minmax(0,1fr));
            gap:14px;
            margin:18px 0 16px;
          }}
          .final-card-pill {{
            display:flex;
            justify-content:center;
            align-items:center;
            border-radius:999px;
            padding:14px 0;
            font-size:22px;
            font-weight:950;
            white-space:nowrap;
          }}
          .final-card-pill.pass {{ color:#15803d; background:#dcfce7; }}
          .final-card-pill.fail {{ color:#b91c1c; background:#fee2e2; }}
          .final-card-pill.hold {{ color:#b45309; background:#fef3c7; }}
          .final-card-total {{
            margin-top:16px;
            border-radius:999px;
            background:#e5e7eb;
            color:#111827;
            text-align:center;
            padding:16px 0;
            font-size:29px;
            font-weight:950;
          }}
          .final-card-meta {{
            display:grid;
            gap:15px;
            margin-top:26px;
            padding-top:25px;
            border-top:1px solid #e5e7eb;
          }}
          .final-card-meta div {{
            display:flex;
            justify-content:space-between;
            gap:18px;
            font-size:22px;
            line-height:1.4;
          }}
          .final-card-meta span {{ color:#64748b; font-weight:850; }}
          .final-card-meta b {{ color:#172554; font-weight:950; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
          .final-permit-status {{ margin-top:26px; padding:20px 22px; border-radius:18px; font-size:20px; font-weight:900; line-height:1.55; }}
          .final-permit-status.applied {{ color:#14734b; background:#effdf6; border:1px solid #a7efd1; }}
          .final-permit-status.missing {{ color:#9a5b08; background:#fff8e8; border:1px solid #f7d79c; }}
          @media (max-width:1180px) {{
            .final-page-stage {{ padding:0 8px; }}
            .final-page-summary-card {{ position:relative; left:auto; top:auto; width:100%; margin:14px 0 0; }}
          }}
        </style>
        <section class="final-page-stage">
          <div class="final-page-canvas">
            {preview_html}
            <aside class="final-page-summary-card">
              <div class="final-card-kicker">AI DOCUMENT REVIEW</div>
              <div class="final-card-title">전체 요약</div>
              <p class="final-card-sub">{html.escape(doc_company)} · {html.escape(doc_product)}</p>
              <div class="final-card-counts">
                <span class="final-card-pill pass">충족 {display_summary.passed}</span>
                <span class="final-card-pill fail">불충족 {display_summary.failed}</span>
                <span class="final-card-pill hold">보류 {display_summary.held}</span>
              </div>
              <div class="final-card-total">총 {display_summary.total}개</div>
              <div class="final-card-meta">
                <div><span>제출 버전</span><b>{html.escape(doc_version)}</b></div>
                <div><span>접수일</span><b>{html.escape(received_date)}</b></div>
                <div><span>허가서</span><b>{html.escape(permit_state)}</b></div>
                <div><span>제품/제조번호</span><b>{html.escape(product_number)}</b></div>
              </div>
              {permit_status_html}
            </aside>
          </div>
        </section>
        """
    )

    general_info_html = _render_section1_pdf_pages(result)
    if general_info_html:
        combined_parts.append(general_info_html)

    if getattr(result, "manufacturing_summary_image_paths", None):
        combined_parts.append(
            """
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                        font-size:22px;font-weight:900;color:#111827;margin:8px 0 14px 0;">
                제조요약도
            </div>
            """
        )
        combined_parts.append(
            render_manufacturing_summary_with_stage_cards(
                result,
                summary_counts_path=artifacts.get("summary_after_path"),
            )
        )

    combined_parts.append(
        """
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:26px 0 18px 0;" />
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                    font-size:22px;font-weight:900;color:#111827;margin:8px 0 14px 0;">
            개별 시험 판정 결과
        </div>
        """
    )

    combined_parts.append(render_result_html(result))

    components.html(
        "\n".join(combined_parts),
        height=FINAL_JUDGEMENT_VIEWPORT_HEIGHT,
        scrolling=True,
    )
