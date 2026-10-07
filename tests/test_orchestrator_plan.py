"""동적 계획 — 담당 A (계획서 §4-1, 핵심 테스트 2·3)

- 사전 조사 결과(임계값 이상 청크 수)에 따라 Task 수가 개수 규칙대로 달라진다(4~7).
- Worker 수 = 계획의 Task 수.
- LLM 은 Task 내용만 채운다. 개수가 다르거나 실패하면 같은 개수의 기본 계획을 쓴다.
"""

from types import SimpleNamespace

import pytest

from src.graph import build_graph, invoke
from src.orchestrator import planner
from tests import mock_nodes

CONFIG = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"]}
SPLIT = {"max_retry": 2, "max_workers": 8, "max_steps": 12, "max_searches": 16,
         "split": {"tau": 0.5, "T": 3}}


def found_with(**chunks):
    """(관점_기술=청크 수) 로 사전 조사 결과를 만든다. 지정하지 않은 칸은 1."""
    found = {}
    for perspective in planner.PROBE_ROLE:
        for tech in ("TurboQuant", "ITME"):
            n = chunks.get(f"{perspective}_{tech}", 1)
            found[(perspective, tech)] = {"chunks": n, "groups": min(n, 2),
                                          "queries": [f"{tech} {perspective}"]}
    return found


@pytest.fixture(autouse=True)
def calibrated(monkeypatch):
    monkeypatch.setattr(planner, "_cfg", lambda: SPLIT)


# ── 개수 규칙 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chunks, expected", [
    ({}, 4),                                                          # 모두 T 미만 → 관점마다 1
    ({"maturity_TurboQuant": 5, "maturity_ITME": 4}, 5),              # maturity 만 기술별로
    ({f"{p}_{t}": 9 for p in ("maturity", "market", "domain_assessment")
      for t in ("TurboQuant", "ITME")}, 7),                           # 세 관점 모두 기술별로
])
def test_task_count_follows_the_probe(chunks, expected):
    specs, gaps = planner.task_specs(found_with(**chunks))
    assert len(specs) == expected and gaps == []
    assert ("stakeholder", ["TurboQuant", "ITME"]) in specs          # 웹은 항상 1개


def test_zero_evidence_tech_gets_a_planned_gap_instead_of_a_worker():
    specs, gaps = planner.task_specs(found_with(market_ITME=0, market_TurboQuant=7))
    assert ("market", ["TurboQuant"]) in specs
    assert [(p, t) for p, t, _ in gaps] == [("market", "ITME")]

    result = planner.planned_gap_result("market", "ITME", gaps[0][2], "도메인")
    gap = result["assessment"]["gaps"][0]
    assert gap["kind"] == "evidence_gap" and gap["attempted_queries"] == ["ITME market"]
    assert result["status"] == "partial"


def test_uncalibrated_probe_falls_back_to_one_task_per_perspective(monkeypatch):
    monkeypatch.setattr(planner, "_cfg", lambda: SPLIT | {"split": {"tau": None, "T": None}})
    assert planner.probe({"run_config": CONFIG}) is None
    specs, _ = planner.task_specs(None)
    assert len(specs) == 4


# ── 사전 조사 ─────────────────────────────────────────────────────────────────

def test_probe_counts_only_hits_above_tau_and_dedupes(monkeypatch):
    def search(query, collection, technology, top_k, perspective):
        return [{"chunk_id": "c1", "source_id": "turboquant-paper", "cosine_score": 0.8},
                {"chunk_id": "c1", "source_id": "turboquant-paper", "cosine_score": 0.8},
                {"chunk_id": "c2", "source_id": "turboquant-blog", "cosine_score": 0.6},
                {"chunk_id": "c3", "source_id": "other", "cosine_score": 0.2}]

    monkeypatch.setattr(planner, "settings", lambda: {
        "quality": {"source_groups": {"turboquant-paper": "google", "turboquant-blog": "google"}}})
    found = planner.probe({"run_config": CONFIG}, search=search)
    cell = found[("maturity", "TurboQuant")]
    # domain_assessment 는 papers_core·context 두 컬렉션을 보지만 같은 청크는 한 번만 센다
    assert found[("domain_assessment", "ITME")]["chunks"] == 2
    assert cell["chunks"] == 2 and cell["groups"] == 1               # 같은 묶음은 1개
    assert "데이터센터/클라우드" in cell["queries"][0]


# ── 핵심 2: Worker 수 = 계획의 Task 수 ─────────────────────────────────────────────

@pytest.mark.parametrize("chunks, expected", [
    ({}, 4),
    ({f"{p}_{t}": 9 for p in ("maturity", "market", "domain_assessment")
      for t in ("TurboQuant", "ITME")}, 7),
])
def test_send_count_equals_plan_length(tmp_path, monkeypatch, chunks, expected):
    monkeypatch.setattr(planner, "probe", lambda state: found_with(**chunks))
    graph = build_graph(**mock_nodes.all_nodes())
    final = invoke(graph, {"run_id": "plan-test", "trace": [],
                           "run_config": CONFIG | {"runs_dir": str(tmp_path)}})

    assert len(final["plan"]) == expected
    assert [t["node"] for t in final["trace"]].count("worker") == expected
    log = (tmp_path / "plan-test" / "decisions.jsonl").read_text(encoding="utf-8")
    assert f"Task {expected}개" in log and "사전 조사 반영" in log


def test_planned_gap_reaches_the_state_without_a_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "probe",
                        lambda state: found_with(market_ITME=0, market_TurboQuant=2))
    final = invoke(build_graph(**mock_nodes.all_nodes()),
                   {"run_id": "gap-test", "trace": [],
                    "run_config": CONFIG | {"runs_dir": str(tmp_path)}})
    assert "r0-market-TurboQuant" in [t["task_id"] for t in final["plan"]]
    gaps = [g for g in final["gaps"] if g.get("kind") == "evidence_gap" and g["role"] == "market"]
    assert any(g["attempted_queries"] == ["ITME market"] for g in gaps)


# ── 핵심 3: LLM 분해와 폴백 ─────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, n):
        self.n = n

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.prompt = prompt
        return SimpleNamespace(tasks=[
            SimpleNamespace(focus=f"하위 질문 {i}", queries=[f"질의 {i}"], rationale=f"이유 {i}")
            for i in range(self.n)])


def test_llm_fills_task_content_but_not_the_count(monkeypatch):
    specs = [("maturity", ["TurboQuant"]), ("maturity", ["ITME"]), ("market", ["TurboQuant", "ITME"])]
    fake = FakeLLM(3)
    monkeypatch.setattr(planner, "get_llm", lambda: fake)
    plan, source = planner.decompose({"run_config": CONFIG}, specs)

    assert source == "llm"
    assert [t["task_id"] for t in plan] == ["r0-maturity-TurboQuant", "r0-maturity-ITME",
                                            "r0-market-both"]
    assert plan[0]["focus"] == "하위 질문 0" and plan[2]["queries"] == ["질의 2"]
    assert "정확히 하나씩" in fake.prompt


def test_wrong_count_from_llm_falls_back_to_the_default_plan(monkeypatch):
    specs = [("maturity", ["TurboQuant", "ITME"]), ("market", ["TurboQuant", "ITME"])]
    monkeypatch.setattr(planner, "get_llm", lambda: FakeLLM(5))
    plan, source = planner.decompose({"run_config": CONFIG}, specs)
    assert source.startswith("default:") and len(plan) == 2
    assert plan[0]["focus"].endswith(planner.CRITERIA["maturity"])


def test_llm_error_falls_back_with_the_same_count():
    specs = [("maturity", ["TurboQuant"]), ("maturity", ["ITME"])]
    plan, source = planner.decompose({"run_config": CONFIG}, specs)   # conftest 가 LLM 을 막는다
    assert source == "default:RuntimeError" and len(plan) == 2
