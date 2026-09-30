"""Explicit, auditable extraction environment (no optional-package auto switch)."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path

EXTRACTION_VERSION = "portable-tables-v1"
PACKAGES = ("pdfplumber", "pdfminer.six", "camelot-py", "opencv-python-headless",
            "pymupdf", "pypdf", "pypdfium2", "numpy", "pandas")


def extraction_backend() -> str:
    backend = os.environ.get("SP_TABLE_BACKEND", "hybrid").strip().lower()
    if backend not in {"hybrid", "pdfplumber"}:
        raise ValueError("SP_TABLE_BACKEND는 hybrid 또는 pdfplumber여야 합니다.")
    return backend


def runtime_manifest() -> dict:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"extraction_version": EXTRACTION_VERSION, "backend": extraction_backend(),
            "python": platform.python_version(), "platform": platform.system(), "packages": versions}


def extraction_fingerprint() -> str:
    data = runtime_manifest()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def read_extraction_report(directory: Path) -> dict:
    path = Path(directory) / "extraction_report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def validate_extraction_report(report: dict) -> None:
    if report.get("blocking_issues"):
        pages = ", ".join(str(row["page"]) for row in report["blocking_issues"][:20])
        raise RuntimeError(f"PDF 추출 확인 필요: {pages}쪽에 읽을 수 없는 텍스트/이미지 전용 내용이 있습니다. "
                           "누락된 문서를 충족으로 처리하지 않습니다. extraction_report.json을 확인하세요.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
