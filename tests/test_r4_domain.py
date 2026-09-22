"""도메인 적용 평가 노드 — 담당: R4 (검증: 인터페이스 계약 §① §③)

domain_assessment()가 실제로 schema.Assessment 를 반환하는지, 인용 사슬 닫힘성과
status=failed 시 claims=[] 가드레일이 지켜지는지 검사한다. papers_core/context
두 검색 도구와 run_node 를 모두 가짜로 갈아끼운다.
"""

from types import SimpleNamespace

import pytest

from src.schema import Assessment
from src.agents import domain as agent

TQ_ID = "aaaaaaaaaaaa"
ITME_ID = "bbbbbbbbbbbb"
UNSEEN_ID = "ffffffffffff"


def chunk(cid, *, applies_to, scope="direct", collection="papers_core", source_id="paper-1", page=7, text="본문 내용"):
    return {"chunk_id": cid, "source_id": source_id, "source": source_id, "collection": collection,
            "applies_to": list(applies_to), "scope": scope, "page": page, "text": text}


def make_bind(core_chunks_by_tech, context_chunks=()):
    """domain_assessment 가 쓰는 두 바인딩(core papers_core, context)을 함께 가짜로 만든다."""
    calls = []

    def bind(role, collection=None):
        target = "context" if collection == "context" else "core"

        def invoke(payload):
            calls.append((target, payload["technology"]))
            if target == "context":
                return list(context_chunks)
            return list(core_chunks_by_tech.get(payload["technology"], []))
        return SimpleNamespace(invoke=invoke)

    bind.calls = calls
    return bind


def stub_llm(text):
    return lambda instruction, domain, context: text


STATE = {"run_id": "r4-test", "run_config": {}}


def test_both_collections_are_queried(monkeypatch):
    bind = make_bind({"TurboQuant": [], "ITME": []}, context_chunks=[])
    monkeypatch.setattr(agent, "bind_document_search", bind)
    monkeypatch.setattr(agent, "run_node", stub_llm("근거 없음.\n근거 공백: 없음"))

    agent.domain_assessment(STATE)

    assert ("core", "TurboQuant") in bind.calls
    assert ("core", "ITME") in bind.calls
    assert ("context", "both") in bind.calls


def test_invalid_citations_only_force_failed_and_empty_claims(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant 단독 적용성\n배치를 키운다 [{UNSEEN_ID}].\n\n"
        "2. ITME 단독 적용성\n세션을 늘린다 [ffffffffffe0].\n\n근거 공백: 없음"))

    out = agent.domain_assessment(STATE)
    a = out["domain_assessment"]

    assert set(out) == {"domain_assessment", "trace"}
    assert a["status"] == "failed"
    assert a["claims"] == []
    assert a["sources"] == []
    assert a["evidence"] == []


def test_no_citations_forces_failed_cleanly(monkeypatch):
    monkeypatch.setattr(agent, "bind_document_search", make_bind({}))
    monkeypatch.setattr(agent, "run_node", stub_llm("근거를 찾지 못했다.\n근거 공백: 없음"))

    out = agent.domain_assessment(STATE)
    a = out["domain_assessment"]

    assert a["status"] == "failed"
    assert a["claims"] == []
    assert a["sources"] == []
    assert a["evidence"] == []


def test_valid_citation_produces_a_linked_assessment(monkeypatch):
    """유효 인용이 있으면 인용 사슬이 닫힌 Assessment 가 나온다.

    Source 의 allowed_uses 는 "domain" 이다 — State 필드명은 domain_assessment 지만
    schema.Role 의 역할 이름은 domain 이고 validate.py 도 그렇게 매핑한다.
    (이전에는 allowed_uses 에 "domain_assessment" 를 넣어 Source 생성에서 죽었다.)
    """
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant 단독 적용성\n배치를 키운다 [{TQ_ID}].\n\n"
        f"2. ITME 단독 적용성\n세션을 늘린다 [{ITME_ID}].\n\n"
        "### [결합 가설]\n결합하면 시너지가 있을 것으로 추정된다.\n\n근거 공백: 없음"))

    assessment = Assessment.model_validate(agent.domain_assessment(STATE)["domain_assessment"])

    assert {s.source_id for s in assessment.sources}
    assert all(s.allowed_uses == ["domain"] for s in assessment.sources)
    hypothesis = [c for c in assessment.claims if c.kind == "hypothesis"]
    assert hypothesis and hypothesis[0].evidence_ids and hypothesis[0].explanation
