"""Worker 로 호출된 에이전트가 Task 를 따르고 worker_meta 를 돌려주는지 본다 (계획서 §4, §11-4 B)."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.agents import common, domain, market, maturity
from src.agents import stakeholder as sh
from src.schema import Assessment, WorkerMeta
from src.tools.web_search import FetchedPage, WebEvidenceStore
from tests.test_r3_web import HTML
from tests.test_r4_assessments import CHUNKS

TASK = {"task_id": "r1-maturity-ITME", "perspective": "maturity", "technologies": ["ITME"],
        "focus": "ITME 프로토타입 검증 범위", "queries": ["ITME prototype testbed"],
        "rationale": "", "round": 1, "web_budget": 0}

# Task 가 ITME 만 맡았을 때 LLM 이 받은 ITME 청크만 인용하는 응답 (세 노드의 파서에 모두 걸린다)
ITME_TEXT = ("## ① 시장 규모/성장성\n2. ITME TRL 평가\nITME 는 NVMe-oF 대비 1.80배 처리량을 보고한다 "
             "[bbbbbbbbbbbb].\n근거 공백: 없음")

NODES = [(maturity, "maturity"), (market, "market"), (domain, "domain_assessment")]


@pytest.fixture
def calls(monkeypatch):
    seen = {"search": [], "prompt": []}

    class Search:
        def invoke(self, payload):
            seen["search"].append(payload)
            return [c for c in CHUNKS if payload["technology"] in ("both", *c["applies_to"])]

    def text(instruction, domain_text, context):
        seen["prompt"].append(instruction)
        return ITME_TEXT

    for module, _ in NODES:
        monkeypatch.setattr(module, "bind_document_search", lambda *a, **k: Search())
        monkeypatch.setattr(module, "run_node", text)
    return seen


@pytest.mark.parametrize("module,key", NODES, ids=[k for _, k in NODES])
def test_task_drives_queries_technology_and_prompt(calls, module, key):
    out = getattr(module, key)({"run_id": "w-test", "run_config": {}, "task": TASK})

    a = Assessment.model_validate(out[key])
    meta = WorkerMeta.model_validate(out["worker_meta"])
    papers = [p for p in calls["search"] if p["query"] != "데이터센터 추론 동시성 메모리 비용 구조 TCO"]
    # Task 질의는 기본 질의에 더해진다(대체하지 않는다)
    defaults = getattr(module, "BASE_QUERIES", None) or module.QUERIES
    assert {p["query"] for p in papers} == {*defaults, "ITME prototype testbed"}
    assert all(p["technology"] == "ITME" for p in papers)
    assert "ITME 프로토타입 검증 범위" in calls["prompt"][0]
    assert "ITME prototype testbed" in meta.attempted_queries
    assert meta.retrieved >= 1
    # 대상 기술 Claim 만 만든다
    assert a.status == "completed"
    assert a.claims and {c.technology for c in a.claims} == {"ITME"}
    assert all("근거 공백" not in c.text for c in a.claims)


@pytest.mark.parametrize("module,key", NODES, ids=[k for _, k in NODES])
def test_off_target_gap_lines_are_dropped(calls, monkeypatch, module, key):
    """ITME Task 에서 LLM 이 TurboQuant 공백을 적어도 남기지 않고, 기술 미지정 공백은 ITME 로 둔다."""
    monkeypatch.setattr(module, "run_node", lambda *a: ITME_TEXT.replace(
        "근거 공백: 없음", "근거 공백: TurboQuant 커널 공개 | 운영 사례"))
    gaps = Assessment.model_validate(getattr(module, key)(
        {"run_id": "w-test", "run_config": {}, "task": TASK})[key]).gaps
    assert {g.technology for g in gaps} <= {"ITME"}
    assert any(g.item == "운영 사례" for g in gaps)
    assert not any("TurboQuant" in g.item for g in gaps)


@pytest.mark.parametrize("module,key", NODES, ids=[k for _, k in NODES])
def test_without_task_no_worker_meta(calls, module, key):
    out = getattr(module, key)({"run_id": "w-test", "run_config": {}})
    assert set(out) == {key, "trace"}


def test_retrieved_counts_only_chunks_at_or_above_tau(monkeypatch):
    monkeypatch.setattr(common, "settings", lambda: {"orchestrator": {"split": {"tau": 0.65}}})
    found = {c["chunk_id"]: c for c in CHUNKS}            # TurboQuant 0.7, ITME 0.6
    assert common.worker_meta(found, ["q", "q"], ["TurboQuant", "ITME"]) == {
        "attempted_queries": ["q"], "retrieved": 1, "web_searches_used": 0}
    # 대상 밖 기술 청크는 점수가 높아도 세지 않는다
    assert common.worker_meta(found, ["q"], ["ITME"])["retrieved"] == 0


def test_stakeholder_task_uses_own_folder_and_web_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "WebEvidenceStore", lambda *a, **k: WebEvidenceStore(
        *a, searcher=lambda q, n: [{"url": "https://example.org/a", "title": "t"}],
        transport=lambda url, **_: FetchedPage(url, 200, {"content-type": "text/html"}, HTML.encode()), **k))
    monkeypatch.setattr(sh, "_write_draft", lambda **k: {"claims": [], "gaps": []})
    task = {**TASK, "perspective": "stakeholder", "technologies": ["TurboQuant"], "web_budget": 3}

    tids = ("r0-sh-a", "r0-sh-b")
    with ThreadPoolExecutor(2) as pool:                    # Send 처럼 두 Task 를 동시에 실행
        outs = list(pool.map(lambda tid: sh.stakeholder(
            {"run_id": "w-test", "run_config": {"runs_dir": str(tmp_path)},
             "task": {**task, "task_id": tid}}), tids))

    for tid, out in zip(tids, outs):
        folder = tmp_path / "w-test" / "workers" / tid
        assert json.loads((folder / "stakeholder.json").read_text())["status"] == out["stakeholder"]["status"]
        assert (folder / "stakeholder-validation.json").is_file() and (folder / "stakeholder-trace.jsonl").is_file()
        meta = WorkerMeta.model_validate(out["worker_meta"])
        assert meta.web_searches_used == 3                 # 4주체 중 3회만 실행, 나머지는 예산 차단
        assert len(meta.attempted_queries) == 3            # 차단된 검색은 조사 이력에 넣지 않는다
        gaps = out["stakeholder"]["gaps"]
        assert all(g["technology"] == "TurboQuant" for g in gaps)
        # 검색하지 못한 주체는 evidence_gap 이 아니라 execution_gap
        assert [g["item"] for g in gaps if g.get("kind") == "execution_gap"] == ["investors"]
    assert not (tmp_path / "w-test" / "stakeholder.json").exists()


def test_stakeholder_zero_web_budget_is_execution_gap_not_crash(tmp_path):
    task = {**TASK, "task_id": "r0-sh-zero", "perspective": "stakeholder",
            "technologies": ["ITME"], "web_budget": 0}
    out = sh.stakeholder({"run_id": "w-zero", "run_config": {"runs_dir": str(tmp_path)}, "task": task})
    gaps = out["stakeholder"]["gaps"]
    assert len(gaps) == 4 and all(g["kind"] == "execution_gap" for g in gaps)
    meta = WorkerMeta.model_validate(out["worker_meta"])
    assert meta.web_searches_used == 0 and meta.attempted_queries == []


@pytest.mark.parametrize("module,key", NODES, ids=[k for _, k in NODES])
def test_fabricated_citation_is_invalid_evidence_not_evidence_gap(calls, monkeypatch, module, key):
    monkeypatch.setattr(module, "run_node", lambda *a: "ITME 는 검증됐다 [ffffffffffff].\n근거 공백: 없음")
    out = getattr(module, key)({"run_id": "w-test", "run_config": {}, "task": TASK})
    a = Assessment.model_validate(out[key])
    assert a.status == "failed" and a.claims == []
    assert [g.technology for g in a.gaps if g.kind == "invalid_evidence"] == ["ITME"]


def test_stakeholder_writer_error_propagates_to_worker(tmp_path):
    from tests.test_r3_stakeholder import make_store

    def writer(**k):
        raise TimeoutError("llm timeout")
    with pytest.raises(TimeoutError):
        sh.make_stakeholder(make_store(tmp_path), writer=writer)({"run_id": "test-run"})


def test_stakeholder_unverifiable_draft_gaps_are_invalid_evidence(tmp_path):
    from tests.test_r3_stakeholder import claim, make_store
    store = make_store(tmp_path)
    out = sh.make_stakeholder(store, writer=lambda **k: {"claims": [claim("invented")], "gaps": []})(
        {"run_id": "test-run"})
    assert out["stakeholder"]["status"] == "failed"
    assert {g["kind"] for g in out["stakeholder"]["gaps"]} == {"invalid_evidence"}


def test_stakeholder_off_target_claim_gets_diagnosed_not_keyerror(tmp_path):
    """TurboQuant 전용 Task 에서 LLM 이 ITME Claim 을 쓰면 이유가 붙은 수정 요청을 받는다."""
    from tests.test_r3_stakeholder import claim, make_store
    store = make_store(tmp_path)
    task = {**TASK, "perspective": "stakeholder", "technologies": ["TurboQuant"]}
    feedback, searched = [], []
    original = store.search
    store.search = lambda q, tech, k: searched.append(tech) or original(q, tech, k)

    def writer(**k):
        feedback.append(k["feedback"])
        eid = next(e for e, v in store.manifest["evidence"].items() if "Developer Kim" in v["quote"])
        if len(feedback) == 1:
            return {"claims": [claim(eid, technology="ITME")],
                    "gaps": [{"technology": "ITME", "item": "adopters", "reason": "r"}]}
        return {"claims": [claim(eid)], "gaps": [{"technology": "ITME", "item": "adopters", "reason": "r"}]}

    out = sh.make_stakeholder(store, writer=writer)({"run_id": "test-run", "task": task})
    assert "ITME 주장의 근거로 쓸 수 없다" in feedback[1]
    assert out["stakeholder"]["status"] != "failed"
    assert set(searched) == {"TurboQuant"}
    assert all(g["technology"] == "TurboQuant" for g in out["stakeholder"]["gaps"])


def test_stakeholder_failed_draft_keeps_budget_blocked_gap_as_execution_gap(tmp_path):
    from tests.test_r3_stakeholder import claim
    store = WebEvidenceStore("w-mix", root=str(tmp_path), max_searches=3,
                             searcher=lambda q, n: [{"url": "https://example.org/a", "title": "t"}],
                             transport=lambda url, **_: FetchedPage(url, 200, {"content-type": "text/html"}, HTML.encode()))
    task = {**TASK, "perspective": "stakeholder", "technologies": ["TurboQuant"]}
    out = sh.make_stakeholder(store, writer=lambda **k: {"claims": [claim("invented")], "gaps": []})(
        {"run_id": "w-mix", "task": task})
    kinds = {g["item"]: g["kind"] for g in out["stakeholder"]["gaps"]}
    assert kinds.pop("investors") == "execution_gap"
    assert set(kinds.values()) == {"invalid_evidence"}


def test_stakeholder_failed_draft_keeps_empty_search_as_evidence_gap(tmp_path):
    """초안 검증이 끝내 실패해도, 검색했으나 결과가 없던 칸은 인용 위조가 아니라 근거 없음이다."""
    from tests.test_r3_stakeholder import claim
    store = WebEvidenceStore("w-empty", root=str(tmp_path),
                             searcher=lambda q, n: [{"url": "https://example.org/a", "title": "t"}]
                             if "competing" in q else [],
                             transport=lambda url, **_: FetchedPage(url, 200, {"content-type": "text/html"}, HTML.encode()))
    task = {**TASK, "perspective": "stakeholder", "technologies": ["TurboQuant"]}
    out = sh.make_stakeholder(store, writer=lambda **k: {"claims": [claim("invented")], "gaps": []})(
        {"run_id": "w-empty", "task": task})
    kinds = {g["item"]: g["kind"] for g in out["stakeholder"]["gaps"]}
    assert kinds == {"competitors": "invalid_evidence", "adopters": "evidence_gap",
                     "developers": "evidence_gap", "investors": "evidence_gap"}


def test_domain_background_search_not_in_worker_meta(monkeypatch):
    """context 배경 검색은 두 기술 모두에 걸린 문서라 조사 이력·retrieved 에 넣지 않는다."""
    class Search:
        def __init__(self, hits): self.hits = hits
        def invoke(self, payload): return self.hits

    ctx = {**CHUNKS[1], "chunk_id": "cccccccccccc", "collection": "context", "applies_to": ["TurboQuant", "ITME"]}
    monkeypatch.setattr(domain, "bind_document_search", lambda *a: Search([ctx] if len(a) > 1 else []))
    monkeypatch.setattr(domain, "run_node", lambda *a: "근거 공백: 없음")
    meta = domain.domain_assessment({"run_id": "w-test", "run_config": {}, "task": TASK})["worker_meta"]
    assert meta["attempted_queries"] == [*domain.QUERIES, "ITME prototype testbed"] and meta["retrieved"] == 0


def test_stakeholder_appends_task_queries_to_group_queries(tmp_path):
    from tests.test_r3_stakeholder import make_store
    store = make_store(tmp_path)
    seen, original = [], store.search
    store.search = lambda q, tech, k: seen.append(q) or original(q, tech, k)
    task = {**TASK, "perspective": "stakeholder", "technologies": ["ITME"], "queries": ["CXL pooling"]}
    sh.make_stakeholder(store, writer=lambda **k: {"claims": [], "gaps": []})({"run_id": "test-run", "task": task})
    assert seen and all("CXL pooling" in q for q in seen)
    assert {q.split(" CXL pooling")[0] for q in seen} == set(sh.QUERIES.values())


@pytest.mark.parametrize("techs,expect", [(["ITME"], False), (["TurboQuant", "ITME"], True)])
def test_domain_hypothesis_only_for_two_tech_task(calls, monkeypatch, techs, expect):
    text = ITME_TEXT.replace("근거 공백: 없음", "### [결합 가설]\n둘을 결합하면 이득 [bbbbbbbbbbbb].\n근거 공백: 없음")
    monkeypatch.setattr(domain, "run_node", lambda *a: text)
    a = Assessment.model_validate(domain.domain_assessment(
        {"run_id": "w-test", "run_config": {}, "task": {**TASK, "technologies": techs}})["domain_assessment"])
    assert any(c.kind == "hypothesis" for c in a.claims) is expect
    assert all(c.technology != "both" for c in a.claims) is not expect
