from sp_pdf_judger.permit_catalog import PermitPolicy
from sp_pdf_judger.permit_pdf_store import PermitPdfStore, normalize_permit_heading
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
