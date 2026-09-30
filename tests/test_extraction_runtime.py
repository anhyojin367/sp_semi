import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from sp_pdf_judger.extraction_runtime import (
    extraction_backend, extraction_fingerprint, runtime_manifest, validate_extraction_report,
)


def test_backend_is_explicit_not_inferred_from_installed_packages(monkeypatch):
    monkeypatch.delenv("SP_TABLE_BACKEND", raising=False)
    assert extraction_backend() == "hybrid"
    baseline = extraction_fingerprint()
    monkeypatch.setenv("SP_TABLE_BACKEND", "pdfplumber")
    assert extraction_fingerprint() != baseline
    assert runtime_manifest()["packages"]["pdfplumber"]
    monkeypatch.setenv("SP_TABLE_BACKEND", "typo")
    with pytest.raises(ValueError):
        extraction_backend()


def test_missing_hybrid_dependency_cannot_silently_change_the_extraction(monkeypatch):
    module = importlib.import_module("json추출")
    monkeypatch.setenv("SP_TABLE_BACKEND", "hybrid")
    monkeypatch.setitem(sys.modules, "camelot", None)
    with pytest.raises(RuntimeError, match="requirements"):
        module.PDFReader("not-opened.pdf").read()


def test_hybrid_batches_all_pages_including_last_short_batch(monkeypatch):
    module = importlib.import_module("json추출")
    monkeypatch.setenv("SP_TABLE_BACKEND", "hybrid")
    pages = [SimpleNamespace(rect=SimpleNamespace(height=800)) for _ in range(35)]
    calls = []
    class FakeDoc(list):
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(module.BasePDFReader, "read", lambda _: pages)
    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _: FakeDoc(pages)))
    monkeypatch.setitem(sys.modules, "camelot", SimpleNamespace(read_pdf=lambda _, **kw: calls.append(kw) or []))
    assert len(module.PDFReader("not-opened.pdf").read()) == 35
    assert [c["pages"] for c in calls] == ["1-16", "17-32", "33-35"]


def test_bad_page_is_blocking_not_a_successful_partial_review():
    with pytest.raises(RuntimeError, match="300쪽"):
        validate_extraction_report({"blocking_issues": [{"page": 300, "image_only": True}]})


def test_320_page_native_text_pdf_retains_last_page_and_permit_evidence(tmp_path, monkeypatch):
    from reportlab.pdfgen.canvas import Canvas
    from sp_pdf_judger.permit_pdf_store import PermitPdfStore
    module = importlib.import_module("json추출")
    path = tmp_path / "long_native.pdf"
    canvas = Canvas(str(path))
    for page in range(1, 321):
        canvas.drawString(50, 780, f"{page}. Manufacturing evidence")
        canvas.drawString(50, 750, f"LOT-{page:04d} native source sentinel {page}")
        if page in {160, 320}:
            # Real ruled cells in the middle and final page, not just a text counter.
            for x in (50, 210, 370, 530):
                canvas.line(x, 610, x, 700)
            for y in (610, 640, 670, 700):
                canvas.line(50, y, 530, y)
            for row, values in enumerate((("Material", "Lot", "Amount"),
                                          ("Buffer", f"BUF-{page}", "15 L"),
                                          ("Protein", f"MAT-{page}", "270 L"))):
                for column, value in enumerate(values):
                    canvas.drawString(58 + 160 * column, 680 - 30 * row, value)
        if page == 319:
            canvas.rect(50, 650, 150, 45)
            canvas.rect(300, 650, 150, 45)
            canvas.drawString(60, 670, "Mixing evidence")
            canvas.drawString(310, 670, "Filling evidence")
            canvas.line(200, 673, 300, 673)
            canvas.line(290, 678, 300, 673)
            canvas.line(290, 668, 300, 673)
        canvas.showPage()
    canvas.save()
    monkeypatch.setenv("SP_TABLE_BACKEND", "pdfplumber")
    output = tmp_path / "extracted"
    summary = module.Pipeline().run(path, output)
    report = json.loads((output / "extraction_report.json").read_text(encoding="utf-8"))
    assert summary["total_pages"] == 320
    assert report["processed_pages"] == list(range(1, 321))
    assert not report["blocking_issues"]
    assert all(p["word_count"] > 0 for p in report["pages"])
    source = json.loads((output / "01_raw_pages.json").read_text(encoding="utf-8"))
    assert "LOT-0320" in source[-1]["text"]
    for page in (160, 320):
        table_text = json.dumps(source[page - 1]["tables"])
        assert f"BUF-{page}" in table_text and f"MAT-{page}" in table_text
        assert "15 L" in table_text and "270 L" in table_text
    assert "Mixing evidence" in source[318]["native_text"]
    assert "Filling evidence" in source[318]["native_text"]
    records = json.loads((output / "05_records.json").read_text(encoding="utf-8"))
    assert any("LOT-0320" in (r.get("raw_text") or "") for r in records)
    assert any("BUF-320" in (r.get("raw_text") or "") for r in records)
    store = PermitPdfStore([path])
    assert any("LOT-0320" in c.text for c in store.chunks)
    assert max(c.page_end or c.page_number for c in store.chunks) == 320
