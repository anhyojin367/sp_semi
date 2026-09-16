import hashlib
import json
from pathlib import Path

from sp_pdf_judger.permit_catalog import resolve_permits


def _catalog(tmp_path: Path, *, sha256: str) -> Path:
    package = tmp_path / "package"
    (package / "permits" / "documents").mkdir(parents=True)
    (package / "permits" / "documents" / "fixture.pdf").write_bytes(b"permit")
    (package / "permits" / "catalog.json").write_text(
        json.dumps(
            {"permits": [{"policy_id": "fixture_policy", "companies": ["SK바이오사이언스", "에스케이바이오사이언스", "SK bioscience"], "products": ["스카이코비원멀티주", "스카이코비원", "SKYCovione"], "path": "permits/documents/fixture.pdf", "sha256": sha256, "authoritative": True, "ignore_section_numbers": True, "require_llm": True, "ocr_mode": "auto"}]},
            ensure_ascii=False,
        ), encoding="utf-8"
    )
    return package


def test_aliases_resolve_authoritative_catalog_file(tmp_path):
    package = _catalog(tmp_path, sha256="85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c")
    result = resolve_permits([], " sk-바이오사이언스 ", "스카이코비원 멀티주", package)
    assert result.policy.policy_id == "fixture_policy"
    assert result.policy.authoritative is True
    assert result.paths == (package / "permits" / "documents" / "fixture.pdf",)
    assert result.errors == ()
    assert result.fingerprint


def test_literal_fixture_hash_is_the_expected_sha256():
    assert hashlib.sha256(b"permit").hexdigest() == "85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c"


def test_company_alias_boundary_does_not_resolve_catalog(tmp_path):
    package = _catalog(tmp_path, sha256="85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c")
    result = resolve_permits([], "동국바이오사이언스", "스카이코비원멀티주", package)
    assert result.policy is None
    assert result.paths == ()


def test_product_alias_boundary_does_not_resolve_catalog(tmp_path):
    package = _catalog(tmp_path, sha256="85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c")
    result = resolve_permits([], "SK바이오사이언스", "다른제품", package)
    assert result.policy is None
    assert result.paths == ()


def test_hash_mismatch_excludes_catalog_file_and_reports_error(tmp_path):
    package = _catalog(tmp_path, sha256="0" * 64)
    result = resolve_permits([], "SK bioscience", "SKYCovione", package)
    assert result.policy is None
    assert result.paths == ()
    assert any("sha256" in error for error in result.errors)


def test_explicit_paths_are_deduplicated_before_catalog_path(tmp_path):
    package = _catalog(tmp_path, sha256="85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c")
    first = tmp_path / "explicit.pdf"
    first.write_bytes(b"explicit")
    result = resolve_permits([first, first], "SK바이오사이언스", "스카이코비원", package)
    assert result.paths == (first, package / "permits" / "documents" / "fixture.pdf")


def test_equivalent_explicit_path_spellings_deduplicate_to_one_resolved_path(tmp_path):
    first = tmp_path / "nested" / "explicit.pdf"
    first.parent.mkdir()
    first.write_bytes(b"explicit")
    equivalent = first.parent / ".." / "nested" / first.name

    result = resolve_permits([equivalent, first], None, None, tmp_path)

    assert result.paths == (first.resolve(),)


def test_explicit_catalog_document_still_produces_authoritative_policy(tmp_path):
    package = _catalog(tmp_path, sha256="85441a43c7cb9f242a5c20c6648026689c795ad4e8321e215bf68d48fc9a4b1c")
    catalog_file = package / "permits" / "documents" / "fixture.pdf"

    result = resolve_permits([catalog_file], "SK바이오사이언스", "스카이코비원", package)

    assert result.policy is not None
    assert result.policy.authoritative is True
    assert result.paths == (catalog_file.resolve(),)
