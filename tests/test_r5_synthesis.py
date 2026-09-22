"""종합 노드가 Assessment 를 읽고 gaps 를 순차 병합하는지 본다 (설계서 §5·§6·§7).

LLM 은 fixture 로 대체한다. 확인하는 것은 계약이다 — 어떤 State 필드를 읽고 쓰는지.
"""

import pytest

from src.agents import synthesis as node
from src.schema import Synthesis


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
        assert schema is Synthesis, "공용 schema 를 써야 한다"
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
