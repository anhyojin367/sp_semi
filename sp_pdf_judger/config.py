from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent

# Streamlit을 다른 working directory에서 실행해도 프로젝트 루트의 .env를 확실히 읽는다.
load_dotenv(BASE_DIR / ".env")
load_dotenv()

APP_TITLE = "SP 시험결과 자동판별 시스템"

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

OCR_LANG = "kor+eng"
OCR_TEXT_THRESHOLD = 80

UCUM_JSON_CANDIDATES = [
    BASE_DIR / "ucum_rag_docs.json",
    Path("/mnt/data/ucum_rag_docs.json"),
]

UCUM_JSONL_CANDIDATES = [
    BASE_DIR / "ucum_rag_image_units_ui_micro.jsonl",
    Path("/mnt/data/ucum_rag_image_units_ui_micro.jsonl"),
    BASE_DIR / "ucum_rag_image_units_enriched.jsonl",
    Path("/mnt/data/ucum_rag_image_units_enriched.jsonl"),
]

UCUM_XLSX_CANDIDATES = [
    BASE_DIR / "TableOfExampleUcumCodesForElectronicMessaging.xlsx",
    Path("/mnt/data/TableOfExampleUcumCodesForElectronicMessaging.xlsx"),
]

RAG_DATA_DIR = BASE_DIR / "rag_data"

LEGACY_EXTRACTOR_OUTDIR_NAME = "legacy_extract"

PASS_LABEL = "검수합격"
FAIL_LABEL = "검수불합격"
HOLD_LABEL = "검수보류"

STATUS_COLORS = {
    PASS_LABEL: "#22c55e",
    FAIL_LABEL: "#ef4444",
    HOLD_LABEL: "#f59e0b",
}

SECTION_NUMBER_RE = r"^(\d+(?:\.\d+)*)\.?\s+(.+)$"
