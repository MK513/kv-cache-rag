"""종합 노드가 Assessment 를 읽고 gaps 를 순차 병합하는지 본다 (설계서 §5·§6·§7).

LLM 은 fixture 로 대체한다. 확인하는 것은 계약이다 — 어떤 State 필드를 읽고 쓰는지.
"""

import pytest

from src.agents import synthesis as node
from src.schema import Synthesis

from src.agents.synthesis import SynthesisDraft


def assessment(role, *, status="partial"):
    return {
        "status": status,
        "claims": [{"claim_id": f"claim-{role}", "text": f"{role} 주장", "technology": "ITME",
                    "kind": "fact", "evidence_ids": ["e-1"]}],
        "sources": [], "evidence": [],
        "gaps": [{"role": role, "technology": "TurboQuant", "item": f"{role} 공백",
                  "reason": "근거 미확인"}],
    }


STATE = {
    "run_id": "r5-test",
    "research": assessment("research"),
    "maturity": assessment("maturity"),
    "market": assessment("market"),
    "stakeholder": assessment("stakeholder"),
    "domain_assessment": assessment("domain"),
    # collect_evidence 가 합류시킨 공백
    "gaps": [{"role": "maturity", "technology": "TurboQuant", "item": "maturity 공백",
              "reason": "근거 미확인"}],
}


class FakeLLM:
    """구조화 출력을 흉내내고 마지막 프롬프트를 붙잡아 둔다."""

    def __init__(self, result):
        self.result = result
        self.prompt = None

    def with_structured_output(self, schema):
        assert schema is SynthesisDraft, "claim_ids 를 받는 구조화 출력을 써야 한다"
        return self

    def invoke(self, prompt):
        self.prompt = prompt
        return self.result


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM(Synthesis(agreements=["공통 사실"], conflicts=[],
                             gaps=["결합 실측 자료 없음"],
                             combination_hypothesis="추론임을 명시한 결합 가설"))
    monkeypatch.setattr(node, "get_llm", lambda: fake)
    return fake


def test_writes_synthesis_and_merged_gaps(llm):
    update = node.synthesis(STATE)

    assert set(update) == {"synthesis", "gaps", "trace"}
    assert update["trace"][0]["node"] == "synthesis"


def test_gaps_merge_sequentially_without_dropping_earlier_ones(llm):
    """§7 — 합류 후 수집, 종합 후 순차 병합. 앞 단계 공백을 덮어쓰지 않는다."""
    gaps = node.synthesis(STATE)["gaps"]

    assert {gap["role"] for gap in gaps} == {"maturity", "synthesis"}
    assert any(gap["item"] == "maturity 공백" for gap in gaps)
    assert any(gap["role"] == "synthesis" and gap["item"] == "결합 실측 자료 없음"
               for gap in gaps)


def test_same_gap_is_not_added_twice(llm):
    """LLM 이 앞 단계와 같은 공백을 다시 말해도 한 번만 남는다."""
    llm.result = Synthesis(gaps=["maturity 공백"], combination_hypothesis="가설")
    gaps = node.synthesis(STATE)["gaps"]

    assert [gap["item"] for gap in gaps].count("maturity 공백") == 1


def test_reads_claims_not_the_removed_text_field(llm):
    """Assessment 에 text 필드는 없다. Claim 을 프롬프트에 넣어야 한다."""
    node.synthesis(STATE)

    assert "maturity 주장" in llm.prompt and "market 주장" in llm.prompt
    assert "research 주장" in llm.prompt      # 기술 조사도 종합 입력이다 (§6)


def test_rewrite_uses_the_validation_field(llm):
    """검증 오류는 validation.errors 에 있다. validation_errors 는 State 필드가 아니다."""
    state = STATE | {"validation": {"errors": [{"node": "market", "kind": "없는 인용 ID"}]}}
    update = node.synthesis(state)

    assert "재작성 지시" in llm.prompt
    assert update["trace"][0]["rewrite"] is True


def test_no_conflict_is_allowed(llm):
    """§5 — 상충하는 의견을 억지로 만들지 않는다. conflicts 가 비어도 통과한다."""
    assert node.synthesis(STATE)["synthesis"]["conflicts"] == []


def test_restated_gap_is_not_added_again(llm):
    """§6 — 종합은 공백을 *정리* 한다. 말만 바꿔 다시 싣지 않는다."""
    llm.result = Synthesis(gaps=["maturity 공백에 대한 추가 확인 필요"],
                           combination_hypothesis="가설")
    items = [gap["item"] for gap in node.synthesis(STATE)["gaps"]]

    assert items == ["maturity 공백"]


def test_genuinely_new_gap_is_kept(llm):
    llm.result = Synthesis(gaps=["총소유비용 정량치"], combination_hypothesis="가설")
    gaps = node.synthesis(STATE)["gaps"]

    assert [g["item"] for g in gaps] == ["maturity 공백", "총소유비용 정량치"]


def test_items_keep_claim_ids_and_drop_unknown_ones(llm):
    """L2 — 종합 항목은 근거 Claim ID 를 남긴다. 입력에 없는 ID 는 버린다."""
    llm.result = SynthesisDraft(
        agreements=[{"text": "공통 사실", "claim_ids": ["claim-maturity", "claim-없음"]}],
        conflicts=[{"perspective": "TRL", "why": "실증 범위", "claim_ids": ["claim-market"]}],
        combination_hypothesis="가설")
    update = node.synthesis(STATE)
    out = update["synthesis"]

    assert out["agreements"] == [{"text": "공통 사실", "claim_ids": ["claim-maturity"]}]
    assert out["conflicts"][0]["claim_ids"] == ["claim-market"]
    assert update["trace"][0]["dropped_refs"] == 1
    assert "(claim-maturity)" in llm.prompt          # 모델이 참조할 수 있게 ID 를 보여 준다


def test_version_increments_and_records_round(llm):
    first = node.synthesis(STATE | {"retry_count": 1})["synthesis"]
    second = node.synthesis(STATE | {"synthesis": first})["synthesis"]

    assert (first["synthesis_version"], first["round"]) == (1, 1)
    assert second["synthesis_version"] == 2


def test_invalid_claims_are_not_offered_again(llm):
    """계획서 §5·§9 — 품질 평가·사람 검토의 무효 Claim 은 재종합에서 되살아나지 않는다."""
    state = STATE | {"claim_flags": {"claim-market": {"status": "invalid", "by": "human_review"}}}
    llm.result = SynthesisDraft(agreements=[{"text": "x", "claim_ids": ["claim-market"]}],
                                combination_hypothesis="가설")
    out = node.synthesis(state)["synthesis"]

    assert "market 주장" not in llm.prompt
    assert out["agreements"][0]["claim_ids"] == []


def test_quality_feedback_becomes_a_rewrite_instruction(llm):
    """품질 평가가 종합 문제로 되돌려 보내면 그 사유를 재작성 지시로 넣는다."""
    state = STATE | {"quality_eval": {"next": "synthesis", "verdicts": {
        "neutrality": {"status": "fail", "reasons": ["우열·추천 표현: 더 우수"]},
        "coverage": {"status": "fail", "reasons": ["무시돼야 하는 근거 문제"]}}}}
    node.synthesis(state)

    assert "neutrality: 우열·추천 표현: 더 우수" in llm.prompt
    assert "무시돼야 하는" not in llm.prompt


def test_legacy_string_output_is_grounded_with_empty_refs(monkeypatch):
    """옛 Synthesis(문자열 목록)를 돌려주는 대역도 GroundedSynthesis 로 맞춘다."""
    fake = FakeLLM(Synthesis(agreements=["공통"], combination_hypothesis="가설"))
    fake.with_structured_output = lambda schema: fake
    monkeypatch.setattr(node, "get_llm", lambda: fake)

    assert node.synthesis(STATE)["synthesis"]["agreements"] == [{"text": "공통", "claim_ids": []}]
