"""Orchestrator-Workers 뼈대 — 담당 A (계획서 §4·§5, 핵심 테스트 1·4·9)

- Worker 수는 계획(`plan`)의 길이로 정해진다(Dynamic Fan-out).
- 병렬 Worker 의 예외는 그래프를 멈추지 않는다(그래프 레벨 Fallback).
- 같은 관점 Worker 가 여럿이어도 Claim ID 가 충돌하지 않는다.
- 누적된 Worker 결과에서 지금 쓸 결과를 결정적으로 고른다.
"""

import pytest

from src.agents.worker import classify
from src.graph import build_graph, invoke
from src.orchestrator import planner
from src.orchestrator.selection import IntegrityError, select_active_worker_results
from tests import mock_nodes

CONFIG = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"]}


def initial(tmp_path, run_id="ow-test"):
    return {"run_id": run_id, "trace": [], "run_config": CONFIG | {"runs_dir": str(tmp_path)}}


def counting(fn, calls, name):
    def wrapped(state):
        calls.append((name, state["task"]["task_id"]))
        return fn(state)
    return wrapped


def task(perspective, techs, round_=0):
    return planner.default_task(perspective, techs, round_, "도메인")


def specs_of(*specs):
    """개수 규칙의 결과를 고정한다 — (관점, 기술 목록) 마다 Task 하나."""
    return lambda found: (list(specs), [])


def run_graph(tmp_path, workers, **overrides):
    nodes = mock_nodes.all_nodes()
    nodes.pop("workers")
    graph = build_graph(workers=workers, **nodes | overrides)
    return invoke(graph, initial(tmp_path))


# ── 핵심 1: 계획 3개 → Worker 3회 → collect_evidence 1회 → synthesis 1회 ──────────

def test_worker_count_follows_the_plan(tmp_path, monkeypatch):
    both = ["TurboQuant", "ITME"]
    monkeypatch.setattr(planner, "task_specs", specs_of(
        ("maturity", both), ("market", both), ("stakeholder", both)))
    calls = []
    workers = {name: counting(fn, calls, name) for name, fn in mock_nodes.workers().items()}

    final = run_graph(tmp_path, workers)

    assert sorted(name for name, _ in calls) == ["market", "maturity", "stakeholder"]
    assert len(final["worker_results"]) == 3
    nodes = [t["node"] for t in final["trace"]]
    assert nodes.count("worker") == 3
    assert nodes.count("collect_evidence") == 1
    assert nodes.count("synthesis") == 1
    assert [t["task_id"] for t in final["plan"]] == ["r0-maturity-both", "r0-market-both",
                                                     "r0-stakeholder-both"]
    assert "domain_assessment" not in final              # 계획에 없던 관점은 실행되지 않는다


def test_default_plan_has_one_task_per_perspective(tmp_path):
    plan = planner.default_plan({"run_config": CONFIG})
    assert [t["perspective"] for t in plan] == ["maturity", "market", "stakeholder",
                                                "domain_assessment"]
    assert all(t["technologies"] == ["TurboQuant", "ITME"] and t["round"] == 0 for t in plan)


# ── 핵심 4: 병렬 Worker 가 실제로 예외를 던져도 그래프는 이어진다 ─────────────────────

def test_worker_exceptions_become_failed_results(tmp_path):
    def timeout(state):
        raise TimeoutError("web search timed out")

    def broken(state):
        raise KeyError("unexpected field")

    workers = mock_nodes.workers() | {"stakeholder": timeout, "market": broken}
    final = run_graph(tmp_path, workers)

    failed = {r["task"]["perspective"]: r for r in final["worker_results"] if r["status"] == "failed"}
    assert set(failed) == {"stakeholder", "market"}               # 두 건 모두 보존
    assert failed["stakeholder"]["error_kind"] == "transient"
    assert failed["market"]["error_kind"] == "permanent"
    assert final["report"]                                        # 남은 결과로 끝까지 진행
    gaps = {(g["role"], g["technology"], g.get("kind")) for g in final["gaps"]}
    assert ("stakeholder", "ITME", "execution_gap") in gaps
    assert ("market", "TurboQuant", "execution_gap") in gaps
    # 모든 Worker 가 실패한 관점도 State 에 남긴다 — 보고서가 평가 보류 관점으로 표시한다
    assert final["stakeholder"]["status"] == "failed" and final["stakeholder"]["claims"] == []
    assert "r0-market-both" in final["last_error"]
    assert (tmp_path / "ow-test" / "workers" / "r0-market-both.json").exists()


@pytest.mark.parametrize("exc, kind", [
    (TimeoutError(), "transient"),
    (ConnectionError(), "transient"),
    (type("RateLimitError", (Exception,), {})(), "transient"),
    (type("HTTPError", (Exception,), {"status_code": 503})(), "transient"),
    (type("HTTPError", (Exception,), {"status_code": 400})(), "permanent"),
    (ValueError("bad schema"), "permanent"),
])
def test_classify(exc, kind):
    assert classify(exc) == kind


# ── 핵심 9: 같은 관점 Worker 둘이 병렬로 돌아도 ID 가 충돌하지 않는다 ──────────────────

def test_same_perspective_workers_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "task_specs", specs_of(
        ("market", ["TurboQuant"]), ("market", ["ITME"])))
    final = run_graph(tmp_path, mock_nodes.workers())

    ids = [c["claim_id"] for c in final["market"]["claims"]]
    assert ids == ["r0-market-ITME:claim-market-itme", "r0-market-TurboQuant:claim-market-turboquant"]
    assert len(set(ids)) == len(ids)


# ── 결과 선택 규칙 ─────────────────────────────────────────────────────────────────

def worker_result(perspective, techs, round_, claims=None, status="completed", error_kind=""):
    t = task(perspective, techs, round_)
    evidence = [{"evidence_id": "e1", "source_id": "s1", "run_id": "r", "collection": "web",
                 "quote": "q", "location": "paragraph:1", "allowed_uses": ["market"]}]
    sources = [{"source_id": "s1", "run_id": "r", "collection": "web", "allowed_uses": ["market"]}]
    if status == "failed":
        return {"task": t, "round": round_, "status": "failed", "assessment": None,
                "error_kind": error_kind or "transient", "error": "x", "meta": {
                    "attempted_queries": [], "retrieved": 0, "web_searches_used": 0},
                "result_path": ""}
    claims = claims if claims is not None else [
        {"claim_id": f"{t['task_id']}:c-{tech}", "text": "t", "technology": tech, "kind": "fact",
         "evidence_ids": ["e1"]} for tech in techs]
    return {"task": t, "round": round_, "status": status,
            "assessment": {"claims": claims, "evidence": evidence if claims else [],
                           "sources": sources if claims else [], "gaps": [], "status": status},
            "error_kind": "", "error": "",
            "meta": {"attempted_queries": t["queries"], "retrieved": 1, "web_searches_used": 0},
            "result_path": ""}


def test_latest_round_replaces_the_cell_only():
    """칸 단위 교체 — 재계획한 칸만 새 결과로, 나머지 칸은 이전 결과 그대로."""
    first = worker_result("market", ["TurboQuant", "ITME"], 0)
    again = worker_result("market", ["ITME"], 1)
    picked = select_active_worker_results([first, again])["assessments"]["market"]
    assert [c["claim_id"] for c in picked["claims"]] == [
        "r0-market-both:c-TurboQuant", "r1-market-ITME:c-ITME"]


def test_arrival_order_does_not_change_the_result():
    a = worker_result("market", ["TurboQuant", "ITME"], 0)
    b = worker_result("maturity", ["TurboQuant", "ITME"], 0)
    assert select_active_worker_results([a, b]) == select_active_worker_results([b, a])


def test_duplicate_results_count_once_and_conflicts_raise():
    a = worker_result("market", ["TurboQuant", "ITME"], 0)
    assert select_active_worker_results([a, a]) == select_active_worker_results([a])
    changed = dict(a, error="different")
    with pytest.raises(IntegrityError):
        select_active_worker_results([a, changed])


def test_gap_kinds():
    failed = worker_result("stakeholder", ["TurboQuant", "ITME"], 0, status="failed")
    empty = worker_result("market", ["TurboQuant", "ITME"], 0, claims=[], status="partial")
    flagged = worker_result("maturity", ["TurboQuant", "ITME"], 0)
    flags = {c["claim_id"]: {"status": "invalid"} for c in flagged["assessment"]["claims"]}

    selected = select_active_worker_results([failed, empty, flagged], flags)
    kinds = {(g["role"], g["technology"], g["kind"]) for g in selected["cell_gaps"]}
    assert ("stakeholder", "ITME", "execution_gap") in kinds
    assert ("market", "ITME", "evidence_gap") in kinds
    assert ("maturity", "ITME", "invalid_evidence") in kinds
    market_gap = next(g for g in selected["cell_gaps"] if g["role"] == "market")
    assert market_gap["attempted_queries"]               # 조사 이력은 Worker meta 에서
    assert [r["task"]["task_id"] for r in selected["failed"]] == ["r0-stakeholder-both"]


def test_both_claims_cover_both_cells():
    """두 기술을 함께 다룬 Claim 은 두 칸 모두의 근거다(시장성 에이전트는 both 로만 낸다)."""
    both = [{"claim_id": "r0-market-both:c", "text": "t", "technology": "both", "kind": "fact",
             "evidence_ids": ["e1"]}]
    result = worker_result("market", ["TurboQuant", "ITME"], 0, claims=both)
    selected = select_active_worker_results([result])
    assert selected["cell_gaps"] == []
    assert selected["assessments"]["market"]["status"] == "completed"
