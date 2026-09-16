from __future__ import annotations

import pytest

from sp_pdf_judger.config import FAIL_LABEL, HOLD_LABEL, PASS_LABEL
from sp_pdf_judger.judgement import JudgeEngine
from sp_pdf_judger.llm import ClovaJudgeClient, JudgeResponse
from sp_pdf_judger.permit_catalog import PermitPolicy
from sp_pdf_judger.permit_pdf_store import PermitPdfStore
from sp_pdf_judger.schemas import ExtractedRecord


AUTHORITATIVE_POLICY = PermitPolicy("sky", True, True, True, "native")
NON_AUTHORITATIVE_POLICY = PermitPolicy("other", False, False, False, "native")

FULL_EGG_CHUNK = """3.2.1 유정란접종시험
요막강 내 접종
Allantoic eggs: 10~11일령 SPF 유정란 최소 10개에 시험검체를 접종한다.
36 ± 2 ℃에서 3일 배양하고 1차 생존율은 80% 이상이어야 한다.
계대 후 36 ± 2 ℃에서 3일 추가 배양하고 2차 생존율도 80% 이상이어야 한다.
기니피그, 닭, 사람 O형 적혈구에 대한 혈구응집반응이 없어야 한다.
난황낭 접종
Yolk-sac eggs: 6~7일령 SPF 유정란 최소 10개를 36 ± 2 ℃에서 9일 배양한다.
1차 생존율은 80% 이상이어야 하고 계대 후 9일 추가 배양하며 2차 생존율도 80% 이상이어야 한다.
"""


class FakePermitLLM:
    def __init__(self, response: JudgeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.enabled = True
        self.calls: list[dict] = []

    def explain(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class EmptyRagStore:
    def search(self, query: str, top_k: int = 9):
        return []


def _store(policy: PermitPolicy = AUTHORITATIVE_POLICY, text: str = FULL_EGG_CHUNK):
    return PermitPdfStore.from_page_texts([(1, text)], policy=policy, source_file="permit.pdf")


def _record(*, test_name: str = "유정란접종시험", criteria: str = "생존율 80% 이상", result: str = "생존율 90%"):
    return ExtractedRecord(
        record_type="test",
        section_number="9.9.9",
        section_title="바이러스 시험",
        test_name=test_name,
        criteria=criteria,
        result=result,
        method="SP 시험법",
        test_period="3일",
        remarks="원자료 비고",
        raw_text=f"{test_name}\n기준: {criteria}\n결과: {result}",
    )


def _engine(client, policy=AUTHORITATIVE_POLICY):
    return JudgeEngine(
        EmptyRagStore(),
        llm_client=client,
        permit_store=_store(policy),
        permit_policy=policy,
    )


def test_mapped_permit_fail_overrides_sp_pass_and_composes_reason():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="허가서 조건 위반으로 검수불합격입니다.",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
            failed_requirements=["혈구응집반응이 없어야"],
        )
    )

    evaluation = _engine(client).judge_record(
        _record(result="생존율 90%, 혈구응집반응 확인")
    )

    assert evaluation.final_status == FAIL_LABEL
    assert evaluation.source == "permit_pdf_llm_authoritative"
    assert "혈구응집반응이 없어야" in evaluation.reason
    assert "혈구응집반응 확인" in evaluation.reason


def test_authoritative_call_has_full_record_and_full_complex_permit_context():
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="모든 허가서 조건을 만족하여 검수합격입니다.",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="36 ± 2 ℃에서 3일 배양",
        )
    )

    _engine(client).judge_record(_record())

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["authoritative_permit"] is True
    assert "유정란접종시험" in call["record_context"]
    assert "SP 시험법" in call["record_context"]
    assert "원자료 비고" in call["record_context"]
    context = "\n".join(call["rag_contexts"])
    start = context.index("요막강 내 접종")
    split = context.index("난황낭 접종")
    allantoic = context[start:split]
    yolk_sac = context[split:]
    for required in ["10~11일령", "최소 10개", "36 ± 2 ℃", "3일", "1차 생존율은 80%", "계대", "2차 생존율도 80%", "기니피그", "닭", "사람 O형", "혈구응집반응이 없어야"]:
        assert required in allantoic
    for required in ["6~7일령", "최소 10개", "36 ± 2 ℃", "9일", "1차 생존율은 80%", "계대", "2차 생존율도 80%"]:
        assert required in yolk_sac
    for required in ["10~11일령", "10개", "36 ± 2 ℃", "3일", "9일", "계대", "80%", "혈구응집반응이 없어야"]:
        assert required in context
    for required in ["Allantoic eggs", "최소 10개", "기니피그", "닭", "사람 O형", "Yolk-sac eggs", "6~7일령"]:
        assert required in context


def test_short_cho_master_cell_result_is_mapped_permit_fail():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="허가서 최소 기간 미달로 검수불합격입니다.",
            permit_match_status="matched",
            matched_permit_test="돼지유래바이러스부정시험",
            permit_basis="최소 21일",
            failed_requirements=["최소 21일"],
        )
    )
    record = _record(
        test_name="돼지유래바이러스부정시험",
        criteria="음성이어야 함",
        result="14일 배양 후 음성",
    )
    store = _store(text="""3.2.2 돼지유래바이러스부정시험
CHO 마스터 세포주를 최소 21일 배양해야 한다.
""")
    engine = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY)

    evaluation = engine.judge_record(record)

    assert evaluation.final_status == FAIL_LABEL
    assert "최소 21일" in evaluation.reason
    assert "14일" in evaluation.reason


def test_not_found_preserves_primary_verdict():
    client = FakePermitLLM(
        JudgeResponse(status=PASS_LABEL, reason="허가서 매칭 없음", permit_match_status="not_found")
    )

    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL
    assert evaluation.source == "rule_before"


def test_ambiguous_yields_hold():
    client = FakePermitLLM(
        JudgeResponse(status=PASS_LABEL, reason="매칭이 불명확하여 검수보류", permit_match_status="ambiguous")
    )

    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert evaluation.source == "permit_pdf_llm_authoritative"


def test_ambiguous_diagnostic_fields_do_not_leak():
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="api/429 secret reason",
            permit_match_status="ambiguous",
            permit_basis="api/429 secret basis",
            normalized_result="api/429 secret result",
        )
    )
    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert "api" not in (evaluation.reason or "").lower()
    assert "429" not in (evaluation.reason or "")
    assert "api" not in (evaluation.normalized_criteria or "").lower()
    assert "api" not in (evaluation.normalized_result or "").lower()


@pytest.mark.parametrize(
    "client",
    [
        None,
        FakePermitLLM(None),
        FakePermitLLM(error=RuntimeError("429 secret api error")),
    ],
)
def test_required_authoritative_llm_unavailable_or_invalid_is_safe_hold(client):
    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert "429" not in (evaluation.reason or "")
    assert "api" not in (evaluation.reason or "").lower()


def test_non_authoritative_policy_retains_hold_only_permit_behavior():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="비권위 허가서 결과",
            permit_match_status="matched",
        )
    )
    evaluation = _engine(client, NON_AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL
    assert client.calls == []


def test_clova_authoritative_prompt_requires_number_independent_every_condition_permit_match(monkeypatch):
    client = ClovaJudgeClient(api_key="fake")
    client.enabled = True
    client.client = object()
    captured = {}

    def fake_request_structured_response(**kwargs):
        captured.update(kwargs)
        return JudgeResponse(status=PASS_LABEL, reason="검수합격", permit_match_status="matched")

    monkeypatch.setattr("sp_pdf_judger.llm.request_structured_response", fake_request_structured_response)
    client.explain(
        test_name="유정란접종시험",
        criteria="기준",
        result="결과",
        rag_contexts=[FULL_EGG_CHUNK],
        authoritative_permit=True,
        record_context="full record context",
    )

    prompt = captured["prompt"]
    assert "matched" in prompt and "ambiguous" in prompt and "not_found" in prompt
    assert "section number" in prompt.lower() or "section-number" in prompt.lower() or "번호" in prompt
    assert "every" in prompt.lower() or "모든" in prompt
    assert "authoritative" in prompt.lower() or "우선" in prompt
    assert "full record context" in prompt


def test_result_only_primary_pass_is_overridden_by_authoritative_fail():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="검수불합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
            failed_requirements=["혈구응집반응이 없어야"],
        )
    )
    evaluation = _engine(client).judge_record(
        _record(criteria=None, result="적합, 혈구응집반응 확인")
    )

    assert evaluation.final_status == FAIL_LABEL
    assert evaluation.source == "permit_pdf_llm_authoritative"
    assert len(client.calls) == 1


def test_determined_loq_table_pass_is_overridden_once_without_losing_rows():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="검수불합격",
            permit_match_status="matched",
            matched_permit_test="잔류 숙주세포 DNA 시험",
            permit_basis="유전자 중 1종 이상이 정량한계 미만이어야 함",
            failed_requirements=["유전자 중 1종 이상이 정량한계 미만이어야 함"],
        )
    )
    record = _record(
        test_name="잔류 숙주세포 DNA 시험",
        criteria="유전자 중 1종 이상이 정량한계 미만이어야 함",
        result=(
            "유전자 | 시험결과\n"
            "A 유전자 | 정량한계 이상\n"
            "B 유전자 | 정량한계 이상\n"
            "C 유전자 | 정량한계 미만"
        ),
    )
    store = _store(text="""3.2.2 잔류 숙주세포 DNA 시험
유전자 중 1종 이상이 정량한계 미만이어야 함.
""")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(record)

    assert evaluation.final_status == FAIL_LABEL
    assert len(evaluation.lot_judgements) == 3
    assert all(row["status"] == FAIL_LABEL for row in evaluation.lot_judgements)
    assert all(row["source"] == "permit_pdf_llm_authoritative" for row in evaluation.lot_judgements)
    assert [row["item_value"] for row in evaluation.lot_judgements] == ["A 유전자", "B 유전자", "C 유전자"]
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        JudgeResponse(status=PASS_LABEL, reason="검수합격", permit_match_status="matched", permit_basis="혈구응집반응이 없어야"),
        JudgeResponse(status=PASS_LABEL, reason="검수합격", permit_match_status="matched", matched_permit_test="다른 시험", permit_basis="혈구응집반응이 없어야"),
        JudgeResponse(status=PASS_LABEL, reason="검수합격", permit_match_status="matched", matched_permit_test="유정란접종시험", permit_basis="허가서 기준"),
        JudgeResponse(status=PASS_LABEL, reason="검수합격", permit_match_status="matched", matched_permit_test="3.2.1", permit_basis="혈구응집반응이 없어야"),
    ],
)
def test_matched_requires_grounded_test_name_and_concrete_basis(response):
    client = FakePermitLLM(response)
    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert evaluation.source == "permit_pdf_llm_authoritative"


@pytest.mark.parametrize("matched_test", ["시험", "확인시험", "성상", "무균시험", "일", "3.2.1"])
def test_generic_or_one_character_matched_test_is_not_grounded(matched_test):
    response = JudgeResponse(
        status=PASS_LABEL,
        reason="검수합격",
        permit_match_status="matched",
        matched_permit_test=matched_test,
        permit_basis="혈구응집반응이 없어야",
    )
    evaluation = _engine(FakePermitLLM(response)).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL


@pytest.mark.parametrize(
    ("matched_test", "basis"),
    [
        ("허가서", "혈구응집반응이 없어야"),
        ("permit", "혈구응집반응이 없어야"),
        ("pdf", "혈구응집반응이 없어야"),
        ("PDF 판정 후보 문단", "혈구응집반응이 없어야"),
        ("유정란접종시험", "이상"),
        ("유정란접종시험", "최소"),
        ("유정란접종시험", "기준"),
    ],
)
def test_wrapper_and_vague_permit_fragments_are_not_grounded(matched_test, basis):
    response = JudgeResponse(
        status=PASS_LABEL,
        reason="검수합격",
        permit_match_status="matched",
        matched_permit_test=matched_test,
        permit_basis=basis,
    )
    evaluation = _engine(FakePermitLLM(response)).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL


def test_stage_qualified_generic_test_can_be_grounded_from_section_path():
    store = _store(
        text="""3.2 바이러스 시험
3.2.1 확인시험
80% 이상이어야 함.
"""
    )
    response = JudgeResponse(
        status=PASS_LABEL,
        reason="검수합격",
        permit_match_status="matched",
        matched_permit_test="바이러스 > 확인시험",
        permit_basis="80% 이상",
    )
    record = _record(test_name="확인시험", result="80% 이상", criteria="80% 이상")
    evaluation = JudgeEngine(EmptyRagStore(), FakePermitLLM(response), store, AUTHORITATIVE_POLICY).judge_record(record)

    assert evaluation.final_status == PASS_LABEL


def test_invalid_status_is_hold_before_not_found_fallback():
    response = JudgeResponse(status="provider-error", reason="api/429 secret", permit_match_status="not_found")
    evaluation = _engine(FakePermitLLM(response)).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert "api" not in (evaluation.reason or "").lower()


def test_provider_diagnostic_in_structured_permit_fields_is_rejected_without_leak():
    response = JudgeResponse(
        status=FAIL_LABEL,
        reason="검수불합격",
        permit_match_status="matched",
        matched_permit_test="유정란접종시험",
        permit_basis="api/429 secret",
        failed_requirements=["api/429 secret"],
    )
    evaluation = _engine(FakePermitLLM(response)).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert "api" not in (evaluation.reason or "").lower()
    assert "429" not in (evaluation.reason or "")


@pytest.mark.parametrize("status", [PASS_LABEL, FAIL_LABEL])
def test_authoritative_pass_fail_reason_and_normalized_result_ignore_model_diagnostics(status):
    response = JudgeResponse(
        status=status,
        reason="api/429 secret provider detail",
        permit_match_status="matched",
        matched_permit_test="유정란접종시험",
        permit_basis="혈구응집반응이 없어야",
        failed_requirements=["혈구응집반응이 없어야"],
        normalized_result="api/429 secret normalized result",
    )
    evaluation = _engine(FakePermitLLM(response)).judge_record(
        _record(result="생존율 90%, 혈구응집반응 확인")
    )

    assert evaluation.final_status == status
    assert "api" not in (evaluation.reason or "").lower()
    assert "429" not in (evaluation.reason or "")
    assert "api" not in (evaluation.normalized_result or "").lower()
    assert "429" not in (evaluation.normalized_result or "")


def test_fail_reason_uses_actual_sp_result_not_failed_requirement():
    client = FakePermitLLM(
        JudgeResponse(
            status=FAIL_LABEL,
            reason="검수불합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
            failed_requirements=["혈구응집반응이 없어야"],
            normalized_result="혈구응집반응 확인",
        )
    )
    evaluation = _engine(client).judge_record(_record(result="생존율 90%, 혈구응집반응 확인"))

    assert evaluation.final_status == FAIL_LABEL
    assert "혈구응집반응 확인" in evaluation.reason


@pytest.mark.parametrize("enabled", [False, 0, None, ""])
def test_false_like_disabled_client_is_safe_hold(enabled):
    client = FakePermitLLM(
        JudgeResponse(status=FAIL_LABEL, reason="api/429 secret", permit_match_status="matched"),
    )
    client.enabled = enabled
    evaluation = _engine(client).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert "api" not in (evaluation.reason or "").lower()


def test_authoritative_context_keeps_long_chunk_and_all_retrieved_chunks():
    tail = " SENTINEL_AT_END "
    long_text = "3.2.1 유정란접종시험\n" + ("허가서 조건 보충 " * 500) + tail
    second = "3.2.2 유정란접종시험 추가조건\n두 번째 조건도 확인해야 함"
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="검수합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="허가서 조건 보충",
        )
    )
    store = _store(text=long_text + "\n" + second)
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL
    context = "\n".join(client.calls[0]["rag_contexts"])
    assert "SENTINEL_AT_END" in context
    assert "두 번째 조건도 확인해야 함" in context


def test_valid_store_without_search_candidate_preserves_primary_without_llm_call():
    client = FakePermitLLM(None)
    store = _store(text="3.2.1 전혀 다른 허가시험\n다른 기준")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL
    assert client.calls == []


def test_authoritative_store_with_extraction_error_is_safe_hold():
    client = FakePermitLLM(None)
    store = _store()
    store.chunks = []
    store.extraction_errors.append("unreadable permit")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert client.calls == []


def test_authoritative_store_with_partial_extraction_error_is_safe_hold():
    client = FakePermitLLM(None)
    store = _store()
    store.extraction_errors.append("unreadable page 2")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert client.calls == []


def test_fatal_page_unreadable_error_is_hold_even_with_enabled_chunks():
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="검수합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
        )
    )
    store = _store()
    store.extraction_errors.append("Permit OCR page 7 is unreadable")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == HOLD_LABEL
    assert client.calls == []


def test_non_content_cache_warning_allows_mapped_evaluation():
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="검수합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
        )
    )
    store = _store()
    store.extraction_errors.append("Could not write permit OCR cache")
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL


def test_criteria_only_record_enters_authoritative_hold_without_external_call():
    client = FakePermitLLM(None)
    evaluation = _engine(client).judge_record(_record(result=None))

    assert evaluation.final_status == HOLD_LABEL
    assert evaluation.source == "permit_pdf_llm_authoritative"
    assert client.calls == []


def test_authoritative_prompt_has_full_context_once_and_criteria_only_sp_text(monkeypatch):
    client = FakePermitLLM(
        JudgeResponse(
            status=PASS_LABEL,
            reason="검수합격",
            permit_match_status="matched",
            matched_permit_test="유정란접종시험",
            permit_basis="혈구응집반응이 없어야",
        )
    )
    unique_tail = "UNIQUE_PERMIT_SENTINEL_9f7c"
    store = _store(text=FULL_EGG_CHUNK + "\n" + unique_tail)
    evaluation = JudgeEngine(EmptyRagStore(), client, store, AUTHORITATIVE_POLICY).judge_record(_record())

    assert evaluation.final_status == PASS_LABEL
    call = client.calls[0]
    rendered = "\n".join(
        [
            call["criteria"],
            call["result"] or "",
            call["record_context"],
            *call["rag_contexts"],
            call["deterministic_reason"],
        ]
    )
    assert rendered.count(unique_tail) == 1
    assert "[허가서 전체 검색 근거]" not in call["criteria"]
