"""TRL 평가 노드 — 담당: R4 (검증: 인터페이스 계약 §① §③)

maturity()가 실제로 schema.Assessment 를 반환하는지, 인용 사슬 닫힘성과
status=failed 시 claims=[] 가드레일이 지켜지는지 검사한다. bind_document_search·
run_node 를 모두 가짜로 갈아끼워 LLM·색인을 타지 않는다.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.schema import Assessment
from src.agents import maturity as agent

TQ_ID = "aaaaaaaaaaaa"
ITME_ID = "bbbbbbbbbbbb"
UNSEEN_ID = "ffffffffffff"


def chunk(cid, *, applies_to, scope="direct", collection="papers_core", source_id="paper-1", page=7, text="본문 내용"):
    return {"chunk_id": cid, "source_id": source_id, "source": source_id, "collection": collection,
            "applies_to": list(applies_to), "scope": scope, "page": page, "text": text}


def make_bind(chunks_by_tech):
    def bind(role, collection=None):
        def invoke(payload):
            return list(chunks_by_tech.get(payload["technology"], []))
        return SimpleNamespace(invoke=invoke)
    return bind


def make_queue_bind(queue_by_tech):
    calls = {"TurboQuant": 0, "ITME": 0}

    def bind(role, collection=None):
        def invoke(payload):
            tech = payload["technology"]
            idx = calls[tech]
            calls[tech] += 1
            queue = queue_by_tech.get(tech, [])
            return list(queue[idx]) if idx < len(queue) else []
        return SimpleNamespace(invoke=invoke)
    return bind


def stub_llm(text):
    return lambda instruction, domain, context: text


STATE = {"run_id": "r4-test", "run_config": {}}


def test_completed_status_and_citation_chain_closure(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    uncited = chunk("dddddddddddd", applies_to=["TurboQuant"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq, uncited], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant TRL 평가\nTRL 4 실험실 검증 [{TQ_ID}].\n\n"
        f"2. ITME TRL 평가\nTRL 3 시뮬레이션 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.maturity(STATE)

    assert set(out) == {"maturity", "trace"}
    a = out["maturity"]
    assert a["status"] == "completed"
    assert a["gaps"] == []
    assert {c["technology"] for c in a["claims"]} == {"TurboQuant", "ITME"}
    for c in a["claims"]:
        assert c["evidence_ids"]

    cited_evidence_ids = {e["evidence_id"] for e in a["evidence"]}
    assert cited_evidence_ids == {TQ_ID, ITME_ID}

    trace = out["trace"][0]
    assert trace["node"] == "maturity" and trace["status"] == "completed"


def test_uncovered_technology_becomes_a_gap_not_an_empty_claim(monkeypatch):
    """§6 — 근거가 없는 항목은 Claim 이 아니라 Gap 으로 기록한다.

    (이전에는 근거 0건인 절이 evidence_ids=[] 인 Claim 이 돼 schema.Claim 의
     min_length=1 을 위반하고 ValidationError 로 죽었다. 회귀 확인용으로 남긴다.)
    """
    tq = chunk(TQ_ID, applies_to=["TurboQuant"], scope="direct")
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": []}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant TRL 평가\nTRL 4 실험실 검증 [{TQ_ID}].\n\n"
        "2. ITME TRL 평가\n근거를 찾지 못했다.\n\n"
        "근거 공백: ITME 아키텍처 실증 근거 미확보"))

    assessment = Assessment.model_validate(agent.maturity(STATE)["maturity"])

    assert assessment.status == "partial"
    assert [c.technology for c in assessment.claims] == ["TurboQuant"]
    assert all(c.evidence_ids for c in assessment.claims)
    assert any(g.technology == "ITME" for g in assessment.gaps)

def test_invalid_citation_forces_failed_and_empty_claims(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant TRL 평가\nTRL 4 [{UNSEEN_ID}].\n\n"
        f"2. ITME TRL 평가\nTRL 3 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.maturity(STATE)
    a = out["maturity"]

    assert a["status"] == "failed"
    assert a["claims"] == []


def test_no_citations_at_all_forces_failed(monkeypatch):
    monkeypatch.setattr(agent, "bind_document_search", make_bind({}))
    monkeypatch.setattr(agent, "run_node", stub_llm("근거를 전혀 찾지 못했다.\n근거 공백: 없음"))

    out = agent.maturity(STATE)
    a = out["maturity"]

    assert a["status"] == "failed"
    assert a["claims"] == []
    assert a["sources"] == []
    assert a["evidence"] == []


def test_supplemental_search_runs_once_when_a_technology_is_entirely_unretrieved(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    # 기본 2회 질의 동안 ITME 는 빈 결과, 보완 검색(1회)에서만 회수된다
    monkeypatch.setattr(agent, "bind_document_search", make_queue_bind({
        "TurboQuant": [[tq], []],
        "ITME": [[], [], [itme]],
    }))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant TRL 평가\nTRL 4 [{TQ_ID}].\n\n"
        f"2. ITME TRL 평가\nTRL 3 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.maturity(STATE)
    assert out["trace"][0]["supplemental_searches"] == 1
    assert out["maturity"]["status"] == "completed"


def test_fallback_claim_covers_both_technologies(monkeypatch):
    """절 구분이 없으면 두 기술을 함께 다루는 Claim 하나로 낸다.

    schema.Technology 가 "both" 를 허용한다 — §3 검색 도구 계약이 이미 쓰는 값이다.
    (이전에는 TurboQuant/ITME 두 값뿐이라 폴백이 항상 ValidationError 로 죽었다.)
    """
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"TurboQuant 와 ITME 를 개괄한다 [{TQ_ID}][{ITME_ID}].\n근거 공백: 없음"))

    assessment = Assessment.model_validate(agent.maturity(STATE)["maturity"])

    assert [c.technology for c in assessment.claims] == ["both"]
    assert set(assessment.claims[0].evidence_ids) == {TQ_ID, ITME_ID}

