from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, List


@dataclass
class ExtractedRecord:
    record_type: str = "test"
    order_idx: int = 0

    section_number: Optional[str] = None
    section_title: Optional[str] = None

    test_name: Optional[str] = None
    content_label: Optional[str] = None
    content: Optional[str] = None

    criteria: Optional[str] = None
    result: Optional[str] = None
    method: Optional[str] = None
    test_date: Optional[str] = None
    test_period: Optional[str] = None
    remarks: Optional[str] = None

    page_start: int = 0
    page_end: int = 0
    source_types: List[str] = field(default_factory=list)
    raw_text: Optional[str] = None

    # json추출.py에서 생성되는 확장 필드
    diagram_data: Optional[dict[str, Any]] = None
    order_index: Optional[int] = None
    section_path: list[dict[str, str]] = field(default_factory=list)
    parent_test_group: Optional[str] = None
    subtest_order: Optional[str] = None
    result_table: Optional[dict[str, Any]] = None
    tables: list[dict[str, Any]] = field(default_factory=list)
    controls: list[dict[str, Any]] = field(default_factory=list)
    normalized_text: Optional[str] = None
    ocr_suspect: bool = False


@dataclass
class Evaluation:
    order_idx: int = 0
    record_type: str = "test"

    section_number: Optional[str] = None
    section_title: Optional[str] = None

    test_name: str = ""
    content_label: Optional[str] = None
    content: Optional[str] = None

    criteria: Optional[str] = None
    result: Optional[str] = None
    method: Optional[str] = None
    test_date: Optional[str] = None
    test_period: Optional[str] = None
    remarks: Optional[str] = None

    final_status: Optional[str] = None
    reason: Optional[str] = None

    page_start: int = 0
    page_end: int = 0

    normalized_criteria: Optional[str] = None
    normalized_result: Optional[str] = None
    comparator: Optional[str] = None

    confidence: str = "normal"
    source: str = "rule"
    raw_text: str = ""
    comparison_completed: bool = False
    lot_judgements: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TreeNode:
    key: str
    title: str
    level: int

    section_number: Optional[str] = None
    section_title: Optional[str] = None

    node_type: str = "section"
    status: Optional[str] = None

    page_start: Optional[int] = None
    page_end: Optional[int] = None

    info_lines: list[str] = field(default_factory=list)
    evaluation: Optional[Evaluation] = None
    source_record: Optional[ExtractedRecord] = None

    children: list["TreeNode"] = field(default_factory=list)
    order_idx: int = 0


@dataclass
class Summary:
    passed: int = 0
    failed: int = 0
    held: int = 0
    total: int = 0
    comparable_total: int = 0


@dataclass
class ProcessingResult:
    pdf_path: Path
    preview_image_path: Path
    extracted_records: list[ExtractedRecord]
    evaluations: list[Evaluation]
    tree: list[TreeNode]
    summary: Summary

    metadata: dict[str, Any] = field(default_factory=dict)

    manufacturing_summary_image_paths: list[Path] = field(default_factory=list)
    manufacturing_summary_status: Optional[str] = None
    manufacturing_summary_reason: Optional[str] = None
    manufacturing_summary_page_numbers: list[int] = field(default_factory=list)