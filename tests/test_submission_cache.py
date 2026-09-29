from pathlib import Path
from types import SimpleNamespace

import pytest

import sp_app as app
import sp_judgement_bridge as bridge


def test_upload_selection_survives_relative_path_callback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / "sp.pdf"
    pdf.write_bytes(b"sample")
    doc = SimpleNamespace(path=pdf, company="회사", product="제품")
    state = {"sp_direct_judgement_artifacts::old": "old", "sim_signature::old": "old",
             "latest_judgement_artifact_key": "old", "unrelated": "preserve"}
    monkeypatch.setattr(app, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(app, "_clear_document_caches", lambda: None)
    seen = []
    monkeypatch.setattr(app, "_document_from_pdf", lambda path: seen.append(path) or doc)

    app._on_upload_saved(Path("sp.pdf"))

    assert app._ensure_selected_document([doc]) is doc
    assert state["selected_pdf_path"] == str(pdf)
    assert seen == [pdf]
    assert state["reference_company_filter"] == "회사"
    assert state["reference_product_filter"] == "제품"
    assert state["unrelated"] == "preserve"
    assert not any(key.startswith(("sp_direct_judgement_artifacts::", "sim_signature")) for key in state)
    assert "latest_judgement_artifact_key" not in state


def test_status_requires_current_rules_permits_and_details(monkeypatch, tmp_path):
    pdf = tmp_path / "sp.pdf"
    pdf.write_bytes(b"sample")
    doc = SimpleNamespace(path=pdf, company="회사", product="제품")
    monkeypatch.setattr(app, "policy_fingerprint", lambda: "rules-new")
    monkeypatch.setattr(app, "_related_permit_files", lambda p: [])
    monkeypatch.setattr(app, "resolve_submission_permits", lambda *a: SimpleNamespace(fingerprint="permit-new"))
    monkeypatch.setattr(app, "resolve_domain_detail_profile", lambda *a: SimpleNamespace(fingerprint="detail-new"))
    entry = {"cache_version": app.JUDGEMENT_STATUS_CACHE_VERSION, "rule_fingerprint": "rules-new",
             "permit_fingerprint": "permit-new", "detail_fingerprint": "detail-new"}
    index = {"documents": {app._pdf_status_key(pdf): entry}}
    assert app._status_record_for_doc(doc, index) is entry
    for field in ("rule_fingerprint", "permit_fingerprint", "detail_fingerprint"):
        original = entry[field]
        entry[field] = "old"
        assert app._status_record_for_doc(doc, index) is None
        entry[field] = original
    assert app._status_record_for_doc(doc, {"documents": {"different-pdf": entry}}) is None


def test_identical_names_in_separate_submissions_have_distinct_cache_keys(tmp_path):
    a, b = tmp_path / "one" / "sp.pdf", tmp_path / "two" / "sp.pdf"
    a.parent.mkdir(); b.parent.mkdir()
    a.write_bytes(b"same"); b.write_bytes(b"same")
    assert bridge._artifact_key(a, [], None) != bridge._artifact_key(b, [], None)


def test_pdf_product_field_takes_precedence_over_filename():
    assert app._extract_product("제 품 명\n다른제품주\n제조번호\n123", "스카이코비원.pdf") == "다른제품주"


@pytest.mark.parametrize("failure", ["exception", "missing_summary", "unknown_document"])
def test_simulation_never_substitutes_static_results_on_error(monkeypatch, tmp_path, failure):
    pdf = tmp_path / "sp.pdf"
    pdf.write_bytes(b"test")
    missing_permit = tmp_path / "missing-permit.pdf"
    doc = SimpleNamespace(path=pdf, permit_files=[missing_permit], company="회사", product="제품")
    errors, received = [], []
    monkeypatch.setattr(app, "st", SimpleNamespace(session_state={}, error=errors.append))
    monkeypatch.setattr(app, "_find_simulation_csv_dir", lambda _: tmp_path)
    monkeypatch.setattr(app, "_document_from_pdf", lambda _: None if failure == "unknown_document" else doc)
    monkeypatch.setattr(app, "_cached_simulation_artifacts_for_doc", lambda *a, **k: None)

    def generate(**kwargs):
        received.extend(kwargs["permit_paths"])
        if failure == "exception":
            raise ValueError("Invalid MD")
        return {"runtime_csv_dir": tmp_path}

    from concurrent.futures import Future
    def generate_job(**kwargs):
        future = Future()
        try:
            future.set_result(generate(**kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return SimpleNamespace(future=future)
    monkeypatch.setattr(app, "_load_judgement_bridge", lambda: {"get_judgement_job": generate_job})
    monkeypatch.setattr(app, "get_simulation_job", lambda *a, **k: pytest.fail("Static CSV must not be rendered"))
    assert app._render_inline_simulation("제품", pdf) == 0.0
    assert errors
    if failure != "unknown_document":
        assert received == [missing_permit], "Missing linked permits must not silently become no permit"


def test_final_page_stops_cleanly_when_judgement_fails(monkeypatch, tmp_path):
    errors = []
    slot = SimpleNamespace(markdown=lambda *a, **k: None, empty=lambda: None)
    monkeypatch.setattr(bridge, "st", SimpleNamespace(empty=lambda: slot, error=errors.append, button=lambda *a, **k: False))

    def fail(**kwargs):
        raise ValueError("Invalid MD")

    monkeypatch.setattr(bridge, "ensure_judgement_artifacts", fail)
    bridge.render_final_judgement_page(selected_doc=SimpleNamespace(path=tmp_path / "sp.pdf"), original_csv_dir=None)
    assert len(errors) == 1
    assert "Invalid MD" in errors[0]


@pytest.mark.parametrize("has_document", [True, False])
def test_final_back_navigation_never_rejudges_or_reads_pdf(monkeypatch, tmp_path, has_document):
    class RerunRequested(Exception):
        pass

    state = {"run_sim": True, "selected_pdf_path": "keep-selected", "gmail_logged_in": False}
    query = {"view": "judge_final", "pdf": "expired-cache.pdf"}
    def rerun():
        raise RerunRequested()
    def forbidden(*args, **kwargs):
        pytest.fail("Returning to inbox must not generate judgement or read the PDF")
    monkeypatch.setattr(bridge, "st", SimpleNamespace(session_state=state, query_params=query,
        button=lambda *a, **k: True, rerun=rerun, empty=forbidden, error=forbidden))
    monkeypatch.setattr(bridge, "ensure_judgement_artifacts", forbidden)
    document = SimpleNamespace(path=tmp_path / "not-opened.pdf") if has_document else None
    with pytest.raises(RerunRequested):
        bridge.render_final_judgement_page(selected_doc=document, original_csv_dir=None)
    assert not query
    assert state["run_sim"] is False
    assert state["selected_pdf_path"] == "keep-selected"


def test_overall_csv_includes_md_failure_and_unmapped_tests(monkeypatch, tmp_path):
    from sp_pdf_judger.schemas import Summary
    result = SimpleNamespace(metadata={"rule_fingerprint": "MD-v1"},
                             summary=Summary(passed=3, failed=1, held=1, total=5, comparable_total=5))
    monkeypatch.setattr(bridge, "_is_albumin_document", lambda _: False)
    monkeypatch.setattr(bridge, "build_stage_info_cards", lambda _: [
        SimpleNamespace(display_name="제조단계", passed=2, failed=0, held=0, total=2)])
    path = bridge._write_summary_csv_from_result(result, tmp_path / "summary.csv")
    rows = bridge._read_summary_rows_by_position(path)
    assert bridge._overall_from_rows(rows) == {"pass": 3, "fail": 1, "hold": 1, "total": 5}
    assert next(row for row in rows if row["name"] == "문서 공통·기타 검증")["fail"] == "1"


def test_inconsistent_summary_counts_stop_instead_of_showing_false_totals():
    result = SimpleNamespace(summary=SimpleNamespace(passed=1, failed=0, held=0, total=1))
    with pytest.raises(ValueError, match="초과"):
        bridge._include_document_summary_counts(result, [{"제조명": "중복", "검수합격": 2, "검수불합격": 0, "검수보류": 0, "총 계": 2}])
