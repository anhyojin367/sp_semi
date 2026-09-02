from pathlib import Path

import pytest

from sp_app import (
    InboxDocument,
    ReferenceDocument,
    _map_unknown_sky_covione_product,
    _version_gate,
)


@pytest.mark.parametrize(
    ("company", "product", "number"),
    [
        ("다른회사", "제품명 미확인", "SKY-M260001"),
        ("동국바이오사이언스", "다른제품", "SKY-M260001"),
        ("동국바이오사이언스", "제품명 미확인", "OTHER-260001"),
    ],
)
def test_unknown_product_mapping_does_not_change_other_cases(company, product, number):
    assert _map_unknown_sky_covione_product(company, product, number) == product


def test_unknown_dongkook_sky_m_product_maps_to_sky_covione():
    assert (
        _map_unknown_sky_covione_product(
            "동국바이오사이언스", "제품명 미확인", "SKY-M260001"
        )
        == "스카이코비원멀티주"
    )


def _document(*, company="동국바이오사이언스", product="스카이코비원멀티주", version="v1.2"):
    return InboxDocument(
        path=Path("submission.pdf"),
        company=company,
        product=product,
        title="제출 SP",
        product_number="SKY-M260001",
        version=version,
        received_date="2026.08.26",
        subject="",
    )


def _reference(*, company="동국바이오사이언스", product="스카이코비원멀티주", version="v8.0"):
    return ReferenceDocument(
        company=company,
        product=product,
        title="기준 SP",
        doc_id="REF-1",
        version=version,
        effective_date="2026.05.10",
        status="적용중",
        pages=1,
        checks=1,
        owner="백신검정과",
    )


def test_sky_covione_version_gate_passes_with_different_versions_and_preserves_submission_version():
    doc = _document(version="v1.2")
    ref = _reference(version="v8.0")

    result = _version_gate(doc, [ref])

    assert result["ok"] is True
    assert result["matched"] is ref
    assert doc.version == "v1.2"


def test_sky_covione_exception_does_not_cross_company_boundary():
    result = _version_gate(
        _document(company="SK바이오사이언스"),
        [_reference(company="SK바이오사이언스")],
    )
    assert result["ok"] is False


def test_other_products_keep_existing_version_comparison():
    doc = _document(product="다른제품", version="v1.2")
    ref = _reference(product="다른제품", version="v8.0")

    assert _version_gate(doc, [ref])["ok"] is False
    assert _version_gate(doc, [_reference(product="다른제품", version="v1.2")])["ok"] is True
