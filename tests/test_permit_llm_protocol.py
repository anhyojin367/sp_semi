from sp_pdf_judger.permit_llm_protocol import (
    PermitCandidate, GroundedPermitResponse, ClauseDecision, parse_candidates, grounded_verdict,
)

CANDIDATE = PermitCandidate(1, "세포시험", "재료 > CHO 마스터 세포주", "세포농도는 2 이상이어야 한다. 생존율은 90% 이상이어야 한다.",
                          ("세포농도는 2 이상이어야 한다.", "생존율은 90% 이상이어야 한다."))


def response(decisions):
    return GroundedPermitResponse(permit_match_status="matched", candidate_id=1, reason="검증 결과",
        clauses=[ClauseDecision(clause_id=i, kind=k, status=s, reason="근거 확인") for i,k,s in decisions])


def test_quotes_are_copied_from_source_not_generated():
    result = grounded_verdict(response([(1,"acceptance","PASS"),(2,"acceptance","FAIL")]), [CANDIDATE])
    assert result["status"] == "검수불합격"
    assert result["permit_basis"] == CANDIDATE.text
    assert result["failed_requirements"] == [CANDIDATE.clauses[1]]
    assert result["permit_acceptance_basis"] == list(CANDIDATE.clauses)


def test_missing_duplicate_and_hidden_conditions_cannot_pass():
    for decisions in [[(1,"acceptance","PASS")],
                      [(1,"acceptance","PASS"),(1,"acceptance","PASS")],
                      [(1,"acceptance","PASS"),(2,"procedure","N/A")]]:
        assert grounded_verdict(response(decisions), [CANDIDATE])["status"] == "검수보류"


def test_missing_observation_remains_hold():
    result = grounded_verdict(response([(1,"acceptance","PASS"),(2,"acceptance","HOLD")]), [CANDIDATE])
    assert result["status"] == "검수보류"


def test_genuinely_method_only_permit_does_not_invent_a_result_threshold():
    candidate = PermitCandidate(1, "박테리오파지확인시험", "E.coli 마스터 세포주", "Plaque assay법으로 시험한다.", ("Plaque assay법으로 시험한다.",))
    result = grounded_verdict(response([(1, "procedure", "N/A")]), [candidate])
    assert result["permit_match_status"] == "method_only"
    assert result["status"] != "검수합격"


def test_footer_is_not_an_acceptance_clause_but_quote_is_preserved():
    body = "Plaque assay법으로 시험한다.\n11/60\n문서확인번호 : DEMO\n- 8 -"
    candidate = parse_candidates("[허가서 근거 1]\n- 섹션: 2.1 시험\n- 내용:\n" + body)[0]
    assert candidate.text == body
    assert candidate.clauses == ("Plaque assay법으로 시험한다.",)


def test_parse_only_explicit_permit_candidates():
    context = "[허가서 PDF 판정 후보 문단]\n[허가서 근거 1]\n- 파일: linked.pdf\n- 페이지: 3\n- 섹션: 2.1 세포시험\n- 경로: 재료 > 세포주\n- 내용:\n2.1 세포시험\n세포농도는 2 이상이어야 한다. 생존율은 90% 이상이어야 한다."
    parsed = parse_candidates(context)
    assert len(parsed) == 1 and len(parsed[0].clauses) == 2
    assert parsed[0].title == "세포시험"


def test_general_method_postprocessing_cannot_override_authoritative_verdict():
    from sp_pdf_judger.pipeline import _apply_general_test_method_rules
    from sp_pdf_judger.schemas import Evaluation
    evaluation = Evaluation(record_type="test", test_name="무균시험", criteria="균이 없어야 함",
        result="균이 확인되지 않음", test_period="2025.01.01 ~ 2025.02.01",
        final_status="검수불합격", source="permit_pdf_llm_authoritative", reason="별도 허가 기준 위반")
    _apply_general_test_method_rules([evaluation])
    assert evaluation.final_status == "검수불합격"
    assert evaluation.reason == "별도 허가 기준 위반"


def test_only_structural_grounding_error_gets_one_bounded_repair(monkeypatch):
    from types import SimpleNamespace
    from sp_pdf_judger.llm import ClovaJudgeClient
    calls = []
    valid = response([(1, "acceptance", "PASS"), (2, "acceptance", "HOLD")])
    monkeypatch.setattr("sp_pdf_judger.llm.request_structured_response", lambda **kw: calls.append(kw) or valid)
    client = SimpleNamespace(permit_audit=[], call_count=0, success_count=0, client=None, model="test", max_completion_tokens=1024)
    incomplete = response([(1, "acceptance", "PASS")])
    repaired = ClovaJudgeClient._ground_permit_result(client, incomplete, [CANDIDATE], "record", "prompt")
    assert repaired.status == "검수보류"
    assert len(calls) == 1 and client.permit_audit[-1]["repair"]
    assert "누락 ID=[2]" in calls[0]["prompt"]
    schema = calls[0]["response_model"].model_json_schema()["properties"]["clauses"]
    assert schema["minItems"] == schema["maxItems"] == 2
    ClovaJudgeClient._ground_permit_result(client, valid, [CANDIDATE], "record", "prompt")
    assert len(calls) == 1, "A legitimate missing observation must not be retried away"


def test_repair_diagnostic_identifies_procedure_status_not_missing_ids():
    from sp_pdf_judger.permit_llm_protocol import repair_contract
    candidate = PermitCandidate(1, "DNA", "Component A", "q-PCR법으로 시험한다. 15 미만이어야 한다.",
        ("q-PCR법으로 시험한다.", "15 미만이어야 한다."))
    invalid = response([(1, "procedure", "PASS"), (2, "acceptance", "PASS")])
    _, diagnostic = repair_contract(invalid, [candidate])
    assert "clause_id=1" in diagnostic and "procedure" in diagnostic and "N/A" in diagnostic
    held = grounded_verdict(invalid, [candidate])
    assert held["status"] == "검수보류"
    assert "procedure" in held["reason"]


def test_repair_diagnostic_keeps_acceptance_na_and_duplicate_errors_distinct():
    from sp_pdf_judger.permit_llm_protocol import repair_contract
    _, diagnostic = repair_contract(response([(1, "acceptance", "N/A"), (1, "acceptance", "PASS")]), [CANDIDATE])
    assert "중복 ID=[1]" in diagnostic and "누락 ID=[2]" in diagnostic
    assert "acceptance" in diagnostic and "N/A" in diagnostic


def test_repair_must_not_accept_same_invalid_classification_twice(monkeypatch):
    from types import SimpleNamespace
    from sp_pdf_judger.llm import ClovaJudgeClient
    invalid = response([(1, "procedure", "PASS"), (2, "acceptance", "PASS")])
    calls = []
    monkeypatch.setattr("sp_pdf_judger.llm.request_structured_response", lambda **kw: calls.append(kw) or invalid)
    client = SimpleNamespace(permit_audit=[], call_count=0, success_count=0, client=None, model="test", max_completion_tokens=1024)
    result = ClovaJudgeClient._ground_permit_result(client, invalid, [CANDIDATE], "record", "prompt")
    assert result.status == "검수보류" and len(calls) == 1
    assert "procedure" in result.reason
