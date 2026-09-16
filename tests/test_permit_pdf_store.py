from pathlib import Path

import pytest

from sp_pdf_judger.permit_catalog import PermitPolicy
from sp_pdf_judger.permit_ocr import PermitPageText
from sp_pdf_judger.permit_pdf_store import PermitChunk, PermitPdfStore, normalize_permit_heading
from sp_pdf_judger.schemas import ExtractedRecord


SKY_POLICY = PermitPolicy(
    policy_id="sky",
    authoritative=True,
    ignore_section_numbers=True,
    require_llm=True,
    ocr_mode="auto",
)


def _component_b_record() -> ExtractedRecord:
    return ExtractedRecord(
        record_type="test",
        section_number="9.8.7",
        section_title="Component B 중간체 원액",
        section_path=["원액 시험", "Component B 중간체 원액"],
        test_name="2.1.99 확인시험",
        criteria="확인되어야 함",
        result="발색된 점 확인",
    )


def test_component_b_identity_ignores_dotted_numbers_and_keeps_page_continuation() -> None:
    store = PermitPdfStore.from_page_texts(
        [
            (
                1,
                "2.3. Component B 중간체 원액\n"
                "2.3.1. Component B 중간체원액에 대한 시험\n"
                "2.3.1.7. 확인시험\n"
                "Dot Blot 시험법에 따라 시험한다.",
            ),
            (
                2,
                "반응 후 발색된 점이 확인되어야 한다.\n"
                "2.3.1.8. 엔도톡신시험\n"
                "820 EU/mg 미만이어야 한다.",
            ),
        ],
        policy=SKY_POLICY,
        source_file="permit.pdf",
    )

    chunks = store.search(_component_b_record(), top_k=3)

    assert chunks[0].title == "확인시험"
    assert chunks[0].section_path_titles[-2:] == (
        "Component B 중간체원액에 대한 시험",
        "확인시험",
    )
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert "Dot Blot" in chunks[0].text
    assert "발색된 점이 확인되어야 한다" in chunks[0].text
    assert "엔도톡신" not in chunks[0].text


def test_generic_confirmation_test_requires_component_b_stage_match_not_input_order() -> None:
    store = PermitPdfStore.from_page_texts(
        [
            (
                1,
                "2.1. CHO 마스터 세포주\n"
                "2.1.1. CHO 마스터 세포주에 대한 시험\n"
                "2.1.1.7. 확인시험\n"
                "CHO 확인 기준\n"
                "2.3. Component B 중간체 원액\n"
                "2.3.1. Component B 중간체원액에 대한 시험\n"
                "2.3.1.7. 확인시험\n"
                "Component B 확인 기준",
            )
        ],
        policy=SKY_POLICY,
        source_file="permit.pdf",
    )

    chunks = store.search(_component_b_record(), top_k=2)

    assert "Component B" in chunks[0].section_path_titles[-2]
    assert all("CHO" not in chunk.section_path_titles[-2] for chunk in chunks)


def test_default_policy_keeps_page_number_and_section_number_matching() -> None:
    store = PermitPdfStore.from_page_texts(
        [(4, "2.1.7. 확인시험\n확인 기준")], source_file="permit.pdf"
    )
    record = ExtractedRecord(section_number="2.1.7", test_name="다른 시험")

    chunks = store.search(record)

    assert chunks[0].page_number == 4
    assert chunks[0].page_start == 4
    assert chunks[0].page_end == 4
    assert chunks[0].section_number == "2.1.7"


def test_normalize_permit_heading_removes_dotted_number() -> None:
    assert normalize_permit_heading("  2.3.1.7. 확인시험  ") == "확인시험"


def test_generic_test_with_no_stage_path_is_not_a_direct_match() -> None:
    store = PermitPdfStore.from_page_texts(
        [(1, "2.3.1.7. 확인시험\n공통 확인 기준")],
        policy=SKY_POLICY,
        source_file="permit.pdf",
    )

    assert store.search(_component_b_record()) == []


def test_specific_component_b_stage_beats_broad_stage_in_either_input_order() -> None:
    broad = (
        1,
        "2.1. Component B\n"
        "2.1.7. 확인시험\n"
        "넓은 Component B 확인 기준",
    )
    specific = (
        2,
        "2.2. Component B 중간체 원액\n"
        "2.2.7. 확인 시험\n"
        "중간체 원액 확인 기준",
    )
    record = ExtractedRecord(
        section_title="ComponentB 중간체원액",
        section_path=["Component B", "중간체 원액"],
        test_name="9.8.7. 확인시험",
    )

    for pages in ([broad, specific], [specific, broad]):
        chunks = PermitPdfStore.from_page_texts(pages, policy=SKY_POLICY).search(record)

        assert "중간체 원액" in chunks[0].section_path_titles[-2]


def test_no_policy_keeps_headingless_later_page_as_its_own_page_chunk() -> None:
    store = PermitPdfStore.from_page_texts(
        [
            (1, "2.1.7. 확인시험\n첫 페이지 기준"),
            (2, "둘째 페이지의 독립 본문"),
        ],
        source_file="permit.pdf",
    )

    assert [(chunk.title, chunk.page_start, chunk.page_end) for chunk in store.chunks] == [
        ("확인시험", 1, 1),
        ("허가서 본문", 2, 2),
    ]
    assert "둘째 페이지" not in store.chunks[0].text


def test_format_context_uses_page_number_fallback_and_real_span() -> None:
    legacy_chunk = PermitChunk("permit.pdf", 3, "2.1.7", "확인시험", "기준")
    spanning_chunk = PermitChunk(
        "permit.pdf", 1, "2.1.8", "엔도톡신시험", "기준", page_start=1, page_end=2
    )

    context = PermitPdfStore().format_context([legacy_chunk, spanning_chunk])

    assert "- 페이지: 3" in context
    assert "- 페이지: 1-2" in context
    assert "None" not in context


def test_from_page_texts_rejects_unsupported_source_tuple() -> None:
    with pytest.raises(ValueError, match="page_number, text"):
        PermitPdfStore.from_page_texts([(1, "2.1.7. 확인시험", "native")])


def test_injected_extractor_receives_policy_mode_and_aggregates_errors(tmp_path: Path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls: list[tuple[str, str]] = []
    policy = PermitPolicy("sky", True, True, True, "native")

    def extractor(path: Path, ocr_mode: str) -> tuple[list[PermitPageText], list[str]]:
        calls.append((path.name, ocr_mode))
        if path == first:
            return [PermitPageText(1, "2.1.7. 확인시험\n기준", "native")], ["first warning"]
        raise RuntimeError("unavailable")

    store = PermitPdfStore([first, second], policy=policy, page_text_extractor=extractor)

    assert calls == [("first.pdf", "native"), ("second.pdf", "native")]
    assert store.enabled is True
    assert store.extraction_errors[0] == "first warning"
    assert "second.pdf" in store.extraction_errors[1]
    assert "unavailable" in store.extraction_errors[1]


def test_component_b_normalizes_mixed_language_spacing_and_number_variants() -> None:
    store = PermitPdfStore.from_page_texts(
        [
            (
                1,
                "2.3. Component B 중간체 원액\n"
                "2.3.1.7. 확인 시험\n"
                "Component B 확인 기준",
            )
        ],
        policy=SKY_POLICY,
    )
    record = ExtractedRecord(
        section_title="ComponentB 중간체원액",
        test_name="9.8.7. 확인시험",
    )

    chunks = store.search(record)

    assert chunks[0].title == "확인 시험"


@pytest.mark.parametrize("stage", ["시험", "기준", "test"])
def test_generic_test_does_not_use_generic_only_stage_overlap(stage: str) -> None:
    store = PermitPdfStore.from_page_texts(
        [(1, f"2.3. {stage}\n2.3.1.7. 확인시험\n공통 확인 기준")],
        policy=SKY_POLICY,
    )
    record = ExtractedRecord(section_title=stage, test_name="9.8.7. 확인시험")

    assert store.search(record) == []


@pytest.mark.parametrize(
    "page",
    [
        (None, "text"),
        (True, "text"),
        (0, "text"),
        (-1, "text"),
        ("1", "text"),
        (1, None),
        (1, 7),
    ],
)
def test_from_page_texts_rejects_invalid_two_tuple_metadata(page: tuple[object, object]) -> None:
    with pytest.raises(ValueError, match="positive non-bool int page number and str text"):
        PermitPdfStore.from_page_texts([page])
