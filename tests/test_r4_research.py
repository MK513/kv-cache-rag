"""기술 조사 노드 — 담당: R4 (검증: 인터페이스 계약 §① §③)

research()가 실제로 schema.Assessment 를 반환하는지, 인용 사슬 닫힘성과
status=failed 시 claims=[] 가드레일이 지켜지는지 검사한다. bind_document_search·
run_node 를 모두 가짜로 갈아끼워 LLM·색인을 타지 않는다.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agents import research as agent

TQ_ID = "aaaaaaaaaaaa"
ITME_ID = "bbbbbbbbbbbb"
ITME_CMP_ID = "cccccccccccc"
UNSEEN_ID = "ffffffffffff"


def chunk(cid, *, applies_to, scope="direct", collection="papers_core", source_id="paper-1", page=7, text="본문 내용"):
    return {"chunk_id": cid, "source_id": source_id, "source": source_id, "collection": collection,
            "applies_to": list(applies_to), "scope": scope, "page": page, "text": text}


def make_bind(chunks_by_tech):
    """technology 로 캐닛 청크를 돌려주는 가짜 bind_document_search. query 는 무시한다."""
    def bind(role, collection=None):
        def invoke(payload):
            return list(chunks_by_tech.get(payload["technology"], []))
        return SimpleNamespace(invoke=invoke)
    return bind


def make_queue_bind(queue_by_tech):
    """호출 순서별로 다른 결과를 주는 가짜 — 보완 검색(내부 1회 재시도) 검증용."""
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
        f"1. TurboQuant\nSW 계층에서 압축한다 [{TQ_ID}].\n\n"
        f"2. ITME\nHW 계층에서 가속한다 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.research(STATE)

    assert set(out) == {"research", "trace"}
    a = out["research"]
    assert a["status"] == "completed"
    assert a["gaps"] == []
    assert {c["technology"] for c in a["claims"]} == {"TurboQuant", "ITME"}
    for c in a["claims"]:
        assert c["evidence_ids"]

    # 인용 사슬 닫힘성: 검색은 됐지만 본문에 인용되지 않은 청크는 등록되지 않는다
    cited_evidence_ids = {e["evidence_id"] for e in a["evidence"]}
    assert cited_evidence_ids == {TQ_ID, ITME_ID}
    assert {s["source_id"] for s in a["sources"]} == {"paper-1"}

    trace = out["trace"][0]
    assert trace["node"] == "research" and trace["status"] == "completed"
    assert "timestamp" in trace and trace["attempt"] == 1


def test_partial_status_when_one_tech_only_has_comparison_scope(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"], scope="direct")
    itme_cmp = chunk(ITME_CMP_ID, applies_to=["ITME"], scope="comparison")
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme_cmp]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant\n압축한다 [{TQ_ID}].\n\n"
        f"2. ITME\n비교군 문헌뿐이다 [{ITME_CMP_ID}]. 직접 실증은 없다.\n\n"
        "근거 공백: ITME 1차 실증 근거 미확보"))

    out = agent.research(STATE)
    a = out["research"]

    assert a["status"] == "partial"
    assert len(a["claims"]) == 2
    # LLM 이 적은 근거 공백 1건 + 미확보 기술에 대한 명시적 Gap 1건
    assert len(a["gaps"]) == 2
    assert all(g["technology"] == "ITME" for g in a["gaps"])


def test_invalid_citation_forces_failed_and_empty_claims(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant\n압축한다 [{UNSEEN_ID}].\n\n"
        f"2. ITME\n가속한다 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.research(STATE)
    a = out["research"]

    assert a["status"] == "failed"
    assert a["claims"] == []


def test_no_citations_at_all_forces_failed(monkeypatch):
    monkeypatch.setattr(agent, "bind_document_search", make_bind({}))
    monkeypatch.setattr(agent, "run_node", stub_llm("근거를 전혀 찾지 못했다.\n근거 공백: 없음"))

    out = agent.research(STATE)
    a = out["research"]

    assert a["status"] == "failed"
    assert a["claims"] == []
    assert a["sources"] == []
    assert a["evidence"] == []


def test_supplemental_search_runs_once_when_a_technology_is_entirely_unretrieved(monkeypatch):
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    # ITME 는 기본 검색에서 전부 빈 결과, 보완 검색에서만 회수된다
    monkeypatch.setattr(agent, "bind_document_search", make_queue_bind({
        "TurboQuant": [[tq], [], []],
        "ITME": [[], [], [], [itme]],
    }))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"1. TurboQuant\n압축한다 [{TQ_ID}].\n\n"
        f"2. ITME\n가속한다 [{ITME_ID}].\n\n근거 공백: 없음"))

    out = agent.research(STATE)
    assert out["trace"][0]["supplemental_searches"] > 0
    assert out["research"]["status"] == "completed"


def test_fallback_claim_crashes_when_llm_skips_numbered_sections(monkeypatch):
    """알려진 결함: 본문이 '1. TurboQuant/2. ITME' 절 구분을 안 지키면
    폴백이 technology="both" 로 Claim 을 만드는데, schema.Technology 는
    TurboQuant/ITME 만 허용해 항상 pydantic.ValidationError 로 죽는다.
    R4 가 고쳐야 할 실결함이며, 회귀 확인용으로 여기 고정해 둔다.
    """
    tq = chunk(TQ_ID, applies_to=["TurboQuant"])
    itme = chunk(ITME_ID, applies_to=["ITME"])
    monkeypatch.setattr(agent, "bind_document_search",
        make_bind({"TurboQuant": [tq], "ITME": [itme]}))
    monkeypatch.setattr(agent, "run_node", stub_llm(
        f"TurboQuant 와 ITME 를 개괄한다 [{TQ_ID}][{ITME_ID}].\n근거 공백: 없음"))

    with pytest.raises(ValidationError):
        agent.research(STATE)
