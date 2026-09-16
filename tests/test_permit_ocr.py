from __future__ import annotations

import json
from pathlib import Path

import fitz

import sp_pdf_judger.permit_ocr as permit_ocr
from sp_pdf_judger.permit_ocr import (
    extract_permit_page_texts,
    looks_corrupt_permit_text,
    select_page_text,
)


def _blank_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()


def _corrupt_native_pages(_pdf_path: Path) -> list[str]:
    return ["2.1.2.1.8. ����������������� ��Ų"]


def test_replacement_character_heavy_native_text_uses_ocr() -> None:
    native = "2.1.2.1.8. ����������������� ��Ų"
    ocr = "2.1.2.1.8. 돼지유래바이러스부정시험 최소 21일간 배양"

    assert looks_corrupt_permit_text(native) is True
    assert select_page_text(native, ocr) == (ocr, "windows_ocr")


def test_valid_korean_native_text_does_not_use_ocr() -> None:
    native = "2.3.1.7. 확인시험 Component B 중간체원액에 대한 시험"

    assert looks_corrupt_permit_text(native) is False
    assert select_page_text(native, "잘못된 OCR") == (native, "native")


def test_unusable_ocr_returns_unreadable_page_and_public_error(
    tmp_path: Path, monkeypatch
) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    monkeypatch.setattr(
        permit_ocr,
        "_run_windows_ocr",
        lambda *_args: ({1: "garbled OCR output"}, []),
    )
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))

    pages, errors = extract_permit_page_texts(pdf_path)

    assert pages == [permit_ocr.PermitPageText(1, "", "unreadable")]
    assert any("page 1" in error and "unreadable" in error for error in errors)


def test_valid_pdf_sha_cache_skips_ocr_on_second_extraction(
    tmp_path: Path, monkeypatch
) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    calls: list[Path] = []
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))

    def windows_ocr(image_dir: Path):
        calls.append(image_dir)
        return ({1: "돼지유래바이러스부정시험 21일"}, [])

    monkeypatch.setattr(permit_ocr, "_run_windows_ocr", windows_ocr)

    first_pages, first_errors = extract_permit_page_texts(pdf_path)
    second_pages, second_errors = extract_permit_page_texts(pdf_path)

    assert first_errors == []
    assert second_errors == []
    assert first_pages == second_pages
    assert first_pages[0].source == "windows_ocr"
    assert len(calls) == 1


def test_malformed_cache_is_ignored_and_rebuilt(
    tmp_path: Path, monkeypatch
) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    cache_path = permit_ocr._cache_path(pdf_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("not json", encoding="utf-8")
    calls: list[Path] = []

    def windows_ocr(image_dir: Path):
        calls.append(image_dir)
        return ({1: "유정란접종시험 80% 혈구응집반응"}, [])

    monkeypatch.setattr(permit_ocr, "_run_windows_ocr", windows_ocr)

    pages, errors = extract_permit_page_texts(pdf_path)

    assert errors == []
    assert pages[0].source == "windows_ocr"
    assert len(calls) == 1
    rebuilt = json.loads(cache_path.read_text(encoding="utf-8"))
    assert rebuilt["pdf_sha256"]
    assert rebuilt["pages"] == [
        {"page_number": 1, "text": "유정란접종시험 80% 혈구응집반응", "source": "windows_ocr"}
    ]


def test_stale_cache_hash_is_ignored_and_rebuilt(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    cache_path = permit_ocr._cache_path(pdf_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pdf_sha256": "stale",
                "pages": [{"page_number": 1, "text": "old text", "source": "native"}],
            }
        ),
        encoding="utf-8",
    )
    calls: list[Path] = []

    def windows_ocr(image_dir: Path):
        calls.append(image_dir)
        return ({1: "돼지유래바이러스부정시험 21일"}, [])

    monkeypatch.setattr(permit_ocr, "_run_windows_ocr", windows_ocr)

    pages, errors = extract_permit_page_texts(pdf_path)

    assert errors == []
    assert pages == [
        permit_ocr.PermitPageText(1, "돼지유래바이러스부정시험 21일", "windows_ocr")
    ]
    assert len(calls) == 1
    assert json.loads(cache_path.read_text(encoding="utf-8"))["pdf_sha256"] != "stale"
