from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz

try:
    import pytesseract
except Exception:  # pragma: no cover - availability differs by host
    pytesseract = None

from .utils import clean_text


CACHE_SCHEMA_VERSION = 1
_HANGUL_RE = re.compile(r"[가-힣]")
_MOJIBAKE_RE = re.compile(r"(?:[\uFFFD]{2,}|[ÃÂâ][^\s]{1,3})")


@dataclass(frozen=True)
class PermitPageText:
    page_number: int
    text: str
    source: str


def looks_corrupt_permit_text(text: str) -> bool:
    """Return whether PDF-native text is implausible for a Korean permit."""
    value = clean_text(text)
    if not value:
        return True

    replacement_ratio = value.count("\uFFFD") / len(value)
    if replacement_ratio >= 0.03 or _MOJIBAKE_RE.search(value):
        return True

    non_ascii_letters = [char for char in value if char.isalpha() and ord(char) > 127]
    if len(non_ascii_letters) >= 8:
        hangul_count = len(_HANGUL_RE.findall(value))
        if hangul_count / len(non_ascii_letters) < 0.12:
            return True
    return False


def _is_usable_korean_text(text: str) -> bool:
    value = clean_text(text)
    return bool(_HANGUL_RE.search(value)) and not looks_corrupt_permit_text(value)


def select_page_text(native: str, ocr: str) -> tuple[str, str]:
    if not looks_corrupt_permit_text(native):
        return clean_text(native), "native"
    if _is_usable_korean_text(ocr):
        return clean_text(ocr), "windows_ocr"
    return "", "unreadable"


def _pdf_sha256(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_root() -> Path:
    temp_dir = Path(os.environ.get("TEMP") or tempfile.gettempdir())
    return temp_dir / "sp_pdf_judger_permit_ocr"


def _cache_path(pdf_path: Path) -> Path:
    return _cache_root() / _pdf_sha256(pdf_path) / "page_texts.json"


def _native_page_texts(pdf_path: Path) -> list[str]:
    with fitz.open(pdf_path) as document:
        return [clean_text(page.get_text("text") or "") for page in document]


def _read_cache(cache_path: Path, pdf_sha256: str, page_count: int) -> list[PermitPageText] | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != CACHE_SCHEMA_VERSION
            or payload.get("pdf_sha256") != pdf_sha256
            or not isinstance(payload.get("pages"), list)
            or len(payload["pages"]) != page_count
        ):
            return None
        valid_sources = {"native", "windows_ocr", "tesseract", "unreadable"}
        pages: list[PermitPageText] = []
        for expected_page_number, item in enumerate(payload["pages"], start=1):
            if not isinstance(item, dict):
                return None
            page_number = item.get("page_number")
            text = item.get("text")
            source = item.get("source")
            if (
                not isinstance(page_number, int)
                or isinstance(page_number, bool)
                or page_number != expected_page_number
                or not isinstance(text, str)
                or source not in valid_sources
            ):
                return None
            if (source == "unreadable" and text) or (
                source != "unreadable" and not clean_text(text)
            ):
                return None
            pages.append(PermitPageText(page_number=page_number, text=text, source=source))
        return pages
    except (OSError, TypeError, ValueError, KeyError):
        return None


def _write_cache(cache_path: Path, pdf_sha256: str, pages: list[PermitPageText]) -> None:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "pdf_sha256": pdf_sha256,
        "pages": [
            {"page_number": page.page_number, "text": page.text, "source": page.source}
            for page in pages
        ],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(cache_path)


def _render_corrupt_pages(pdf_path: Path, page_numbers: list[int], image_dir: Path) -> None:
    image_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        for page_number in page_numbers:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(180 / 72, 180 / 72), alpha=False)
            pixmap.save(image_dir / f"page-{page_number}.png")


def _run_windows_ocr(image_dir: Path) -> tuple[dict[int, str], list[str]]:
    if os.name != "nt":
        return {}, ["Windows Media OCR is unavailable on this platform"]
    script = Path(__file__).resolve().parents[1] / "scripts" / "windows_ocr_images.ps1"
    if not script.exists():
        return {}, ["Windows OCR helper is missing"]
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                str(image_dir),
            ],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
            raise ValueError("OCR helper did not emit page JSON")
        pages = {
            int(item["page_number"]): str(item["text"])
            for item in payload["pages"]
            if isinstance(item, dict) and "page_number" in item and "text" in item
        }
        errors = [str(error) for error in payload.get("errors", [])]
        if result.returncode:
            errors.append(f"Windows OCR helper exited with code {result.returncode}")
        return pages, errors
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, [f"Windows OCR failed: {exc}"]


def _tesseract_is_available() -> bool:
    if pytesseract is None:
        return False
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def _run_tesseract_ocr(image_dir: Path) -> tuple[dict[int, str], list[str]]:
    if not _tesseract_is_available():
        return {}, ["Tesseract OCR is unavailable"]
    pages: dict[int, str] = {}
    errors: list[str] = []
    for image_path in sorted(image_dir.glob("page-*.png"), key=_page_number_from_image):
        page_number = _page_number_from_image(image_path)
        try:
            pages[page_number] = pytesseract.image_to_string(str(image_path), lang="kor")
        except Exception as exc:
            errors.append(f"Tesseract OCR failed for page {page_number}: {exc}")
    return pages, errors


def _page_number_from_image(image_path: Path) -> int:
    match = re.fullmatch(r"page-(\d+)\.png", image_path.name)
    return int(match.group(1)) if match else 0


def _unreadable_errors(pages: list[PermitPageText]) -> list[str]:
    return [f"Permit OCR page {page.page_number} is unreadable" for page in pages if page.source == "unreadable"]


def extract_permit_page_texts(
    pdf_path: Path, ocr_mode: str = "auto"
) -> tuple[list[PermitPageText], list[str]]:
    """Recover text page-by-page without interpreting the permit's contents."""
    pdf_path = Path(pdf_path)
    try:
        native_pages = _native_page_texts(pdf_path)
    except Exception as exc:
        return [], [f"Could not read permit PDF: {exc}"]

    pdf_sha256 = _pdf_sha256(pdf_path)
    cache_path = _cache_path(pdf_path)
    cached_pages = _read_cache(cache_path, pdf_sha256, len(native_pages))
    if cached_pages is not None:
        return cached_pages, _unreadable_errors(cached_pages)

    corrupt_numbers = [
        page_number
        for page_number, native in enumerate(native_pages, start=1)
        if looks_corrupt_permit_text(native)
    ]
    ocr_by_page: dict[int, str] = {}
    source_by_page: dict[int, str] = {}
    errors: list[str] = []
    if corrupt_numbers and ocr_mode not in {"none", "native", "off"}:
        image_dir = cache_path.parent
        try:
            _render_corrupt_pages(pdf_path, corrupt_numbers, image_dir)
        except Exception as exc:
            errors.append(f"Could not render permit OCR pages: {exc}")
        else:
            windows_errors: list[str] = []
            if ocr_mode != "tesseract":
                ocr_by_page, windows_errors = _run_windows_ocr(image_dir)
                errors.extend(windows_errors)
                source_by_page.update({page_number: "windows_ocr" for page_number in ocr_by_page})
            fallback_numbers = [
                number
                for number in corrupt_numbers
                if not _is_usable_korean_text(ocr_by_page.get(number, ""))
            ]
            if fallback_numbers and ocr_mode in {"auto", "tesseract"}:
                tesseract_pages, tesseract_errors = _run_tesseract_ocr(image_dir)
                errors.extend(tesseract_errors)
                for page_number in fallback_numbers:
                    if page_number in tesseract_pages:
                        ocr_by_page[page_number] = tesseract_pages[page_number]
                        source_by_page[page_number] = "tesseract"

    pages: list[PermitPageText] = []
    for page_number, native in enumerate(native_pages, start=1):
        selected, source = select_page_text(native, ocr_by_page.get(page_number, ""))
        if source == "windows_ocr":
            source = source_by_page.get(page_number, source)
        pages.append(PermitPageText(page_number, selected, source))

    errors.extend(_unreadable_errors(pages))
    try:
        _write_cache(cache_path, pdf_sha256, pages)
    except OSError as exc:
        errors.append(f"Could not write permit OCR cache: {exc}")
    return pages, errors
