from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

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


def test_unusable_windows_text_falls_back_to_usable_tesseract(
    tmp_path: Path, monkeypatch
) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    monkeypatch.setattr(
        permit_ocr, "_run_windows_ocr", lambda *_args: ({1: "garbled OCR output"}, [])
    )
    tesseract_calls: list[Path] = []

    def tesseract_ocr(image_dir: Path):
        tesseract_calls.append(image_dir)
        return ({1: "\ub3fc\uc9c0\uc720\ub798\ubc14\uc774\ub7ec\uc2a4\ubd80\uc815\uc2dc\ud5d8 \ucd5c\uc18c 21\uc77c"}, [])

    monkeypatch.setattr(permit_ocr, "_run_tesseract_ocr", tesseract_ocr)

    pages, errors = extract_permit_page_texts(pdf_path)

    assert errors == []
    assert pages == [
        permit_ocr.PermitPageText(
            1, "돼지유래바이러스부정시험 최소 21일", "tesseract"
        )
    ]
    assert len(tesseract_calls) == 1


@pytest.mark.parametrize(
    "cached_page",
    [
        {"page_number": True, "text": "old text", "source": "native"},
        {"page_number": 1, "text": "old text", "source": "unexpected"},
        {"page_number": 1, "text": "", "source": "native"},
    ],
)
def test_semantically_invalid_cache_page_is_rebuilt(
    tmp_path: Path, monkeypatch, cached_page: dict[str, object]
) -> None:
    pdf_path = tmp_path / "permit.pdf"
    _blank_pdf(pdf_path)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setattr(permit_ocr, "_native_page_texts", _corrupt_native_pages)
    cache_path = permit_ocr._cache_path(pdf_path)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pdf_sha256": permit_ocr._pdf_sha256(pdf_path),
                "pages": [cached_page],
            }
        ),
        encoding="utf-8",
    )
    calls: list[Path] = []

    def windows_ocr(image_dir: Path):
        calls.append(image_dir)
        return ({1: "\uc720\uc815\ub780\uc811\uc885\uc2dc\ud5d8 80% \ud608\uad6c\uc751\uc9d1\ubc18\uc751"}, [])

    monkeypatch.setattr(permit_ocr, "_run_windows_ocr", windows_ocr)

    pages, errors = extract_permit_page_texts(pdf_path)

    assert errors == []
    assert pages == [
        permit_ocr.PermitPageText(1, "\uc720\uc815\ub780\uc811\uc885\uc2dc\ud5d8 80% \ud608\uad6c\uc751\uc9d1\ubc18\uc751", "windows_ocr")
    ]
    assert len(calls) == 1


def test_rendering_receives_only_corrupt_page_numbers(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "permit.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(pdf_path)
    document.close()
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setattr(
        permit_ocr,
        "_native_page_texts",
        lambda _path: ["English heading", "2.1.2.1.8. ����������������� ��Ų"],
    )
    rendered: list[list[int]] = []
    monkeypatch.setattr(
        permit_ocr,
        "_render_corrupt_pages",
        lambda _path, page_numbers, _image_dir: rendered.append(page_numbers),
    )
    monkeypatch.setattr(
        permit_ocr,
        "_run_windows_ocr",
        lambda _image_dir: ({2: "돼지유래바이러스부정시험 21일"}, []),
    )

    pages, errors = extract_permit_page_texts(pdf_path)

    assert errors == []
    assert [page.source for page in pages] == ["native", "windows_ocr"]
    assert rendered == [[2]]


SOURCE_PERMIT_PDF = Path(
    r"C:\Users\User\Documents\카카오톡 받은 파일\[더미허가문서]스카이코비원멀티주.pdf"
)


@pytest.mark.skipif(not SOURCE_PERMIT_PDF.exists(), reason="supplied permit PDF is absent")
def test_supplied_permit_pdf_recovers_required_pages_and_uses_cache(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TEMP", str(tmp_path / "isolated-temp"))

    first_pages, first_errors = extract_permit_page_texts(SOURCE_PERMIT_PDF)
    first_by_number = {page.page_number: page for page in first_pages}
    assert first_errors == []
    assert "돼지유래바이러스부정시험" in first_by_number[4].text
    assert "21일" in first_by_number[4].text
    assert all(
        token in first_by_number[5].text
        for token in ("유정란접종시험", "80%", "혈구응집반응")
    )

    def cache_miss_only(*_args, **_kwargs):
        raise AssertionError("second extraction must return before OCR or cache writing")

    monkeypatch.setattr(permit_ocr, "_run_windows_ocr", cache_miss_only)
    monkeypatch.setattr(permit_ocr, "_write_cache", cache_miss_only)
    second_pages, second_errors = extract_permit_page_texts(SOURCE_PERMIT_PDF)

    assert second_errors == []
    assert second_pages == first_pages


def test_windows_ocr_helper_orders_nonempty_korean_images_numerically(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("requires Windows Media OCR")
    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "malgun.ttf"
    if not powershell.exists():
        pytest.skip("requires Windows PowerShell 5.1")
    if not font_path.exists():
        pytest.skip("requires the Malgun Gothic Korean font")

    font = ImageFont.truetype(font_path, 44)
    for page_number in (10, 2):
        image = Image.new("RGB", (900, 260), "white")
        ImageDraw.Draw(image).text(
            (40, 90), f"\ud398\uc774\uc9c0 {page_number} \ud55c\uad6d\uc5b4 \uc2dc\ud5d8", font=font, fill="black"
        )
        image.save(tmp_path / f"page-{page_number}.png")

    helper = Path(__file__).parents[1] / "scripts" / "windows_ocr_images.ps1"
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(tmp_path),
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    if payload.get("errors") and any(
        "Windows Media OCR" in str(error) or "ko-KR" in str(error)
        for error in payload["errors"]
    ):
        pytest.skip("Windows Media OCR with ko-KR is unavailable")

    assert result.returncode == 0, result.stderr
    assert payload["errors"] == []
    assert [page["page_number"] for page in payload["pages"]] == [2, 10]
    for page in payload["pages"]:
        normalized = re.sub(r"\s+", "", page["text"])
        assert "페이지" in normalized
        assert "한국어" in normalized
