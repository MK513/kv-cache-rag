"""도메인 적용 평가 노드 — 담당: R4 (검증: 인터페이스 계약 §① §③)

domain_assessment()가 실제로 schema.Assessment 를 반환하는지, 인용 사슬 닫힘성과
status=failed 시 claims=[] 가드레일이 지켜지는지 검사한다. papers_core/context
두 검색 도구와 run_node 를 모두 가짜로 갈아끼운다.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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


def test_crashes_on_any_valid_citation_due_to_invalid_source_allowed_uses(monkeypatch):
    """알려진 결함(가장 심각): 유효 인용이 하나라도 있으면 6-1 단계에서
    Source(allowed_uses=["domain_assessment", "domain"]) 를 만드는데, 공용
    schema.Role 에는 "domain_assessment" 가 없다(§7 R1 주석: State 필드명은
    domain_assessment 지만 역할 이름은 domain). 그래서 인용 사슬을 검사하기도
    전에 Source 생성에서 항상 pydantic.ValidationError 로 죽는다 — 이 버그 아래에
    깔린 두 번째 결함(실증/결합 가설 Claim 이 항상 technology="both" 를 써서
    schema.Technology 위반)은 이게 고쳐져야 비로소 드러난다. R4 가 고쳐야 할
    실결함이며, 회귀 확인용으로 여기 고정해 둔다.
    """
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant 단독 적용성\n배치를 키운다 [{TQ_ID}].\n\n"
        f"2. ITME 단독 적용성\n세션을 늘린다 [{ITME_ID}].\n\n"
        "### [결합 가설]\n결합하면 시너지가 있을 것으로 추정된다.\n\n근거 공백: 없음"))

    with pytest.raises(ValidationError):
        agent.domain_assessment(STATE)
