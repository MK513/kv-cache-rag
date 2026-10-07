"""실패 처리와 재계획 — 담당 A (계획서 §4-1·§5, 핵심 테스트 5·6·7)

- 일시 오류로 실패한 칸은 다음 라운드에 그 칸만 다시 조사한다. 영구 오류는 재시도하지 않는다.
- 품질 평가가 지목한 칸만 재계획한다. 나머지 칸의 결과는 그대로 쓴다(칸 단위 교체).
- 다른 유효 결과가 이미 충족한 칸은 다시 보내지 않는다.
- 보낼 대상이 없으면 빈 계획으로 발행한다. 상한·웹 예산을 넘지 않는다.
"""

from src.graph import build_graph, invoke
from src.orchestrator import planner
from tests import mock_nodes

CONFIG = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"]}


def initial(tmp_path):
    return {"run_id": "replan-test", "trace": [],
            "run_config": CONFIG | {"runs_dir": str(tmp_path)}}


def quality(first_verdicts=None, *, rounds=1, schedule=None):
    """품질 평가 대역. 라운드 i(= retry_count)에 schedule[i] 판정으로 orchestrator 로 돌려보내고,
    schedule 이 끝나면 통과시킨다. schedule 이 없으면 첫 `rounds` 번 같은 판정을 쓴다."""
    plan = schedule if schedule is not None else [first_verdicts or {}] * rounds

    def fn(state):
        i = state.get("retry_count") or 0
        again = i < len(plan)
        return {"quality_eval": {"passed": not again, "next": "orchestrator" if again else "publish",
                                 "verdicts": plan[i] if again else {},
                                 "evaluated_report_version": 1},
                "trace": [{"node": "quality_eval", "status": "ok"}]}
    return fn


def run(tmp_path, workers, quality_eval):
    nodes = mock_nodes.all_nodes()
    nodes.pop("workers")
    graph = build_graph(workers=workers, **nodes | {"quality_eval": quality_eval})
    return invoke(graph, initial(tmp_path))


def flaky(fn, failures=1, exc=TimeoutError):
    """처음 `failures` 번은 예외, 그 뒤 정상."""
    calls = []

    def wrapped(state):
        calls.append(state["task"]["task_id"])
        if len(calls) <= failures:
            raise exc("일시 오류")
        return fn(state)
    wrapped.calls = calls
    return wrapped


# ── 핵심 5: 일시 오류는 다음 라운드에 그 칸만 재시도, 영구 오류는 재시도 없음 ──────────────

def test_transient_failure_is_retried_next_round(tmp_path):
    stakeholder = flaky(mock_nodes.workers()["stakeholder"])
    final = run(tmp_path, mock_nodes.workers() | {"stakeholder": stakeholder}, quality(rounds=1))

    assert stakeholder.calls == ["r0-stakeholder-both", "r1-stakeholder-both"]
    assert final["retry_count"] == 1
    assert [t["task_id"] for t in final["plan"]] == ["r1-stakeholder-both"]   # 그 칸만
    assert {c["technology"] for c in final["stakeholder"]["claims"]} == {"TurboQuant", "ITME"}
    assert not any(g.get("kind") == "execution_gap" for g in final["gaps"])


def test_permanent_failure_is_not_retried(tmp_path):
    market = flaky(mock_nodes.workers()["market"], failures=99, exc=KeyError)
    verdicts = {"coverage": {"status": "fail", "reasons": ["market 비어 있음"],
                             "target_cells": ["market:TurboQuant", "market:ITME"]}}
    final = run(tmp_path, mock_nodes.workers() | {"market": market}, quality(verdicts))

    assert market.calls == ["r0-market-both"]                  # 다시 보내지 않는다
    assert final["stop_reason"] == "no_replan_targets"
    assert final["plan"] == []
    assert final["report_paths"]                               # 빈 계획 → 발행


# ── 핵심 6·8(A 쪽): 품질 평가가 지목한 칸만 재계획, 나머지 칸은 그대로 ─────────────────────

def test_quality_targets_replan_only_those_cells(tmp_path):
    calls = []

    def market(state):
        calls.append(state["task"]["task_id"])
        return mock_nodes.workers()["market"](state)

    verdicts = {"groundedness_l1": {"status": "fail", "reasons": ["비용 주장 불일치"],
                                    "target_cells": ["market:ITME"]}}
    final = run(tmp_path, mock_nodes.workers() | {"market": market}, quality(verdicts))

    assert calls == ["r0-market-both", "r1-market-ITME"]
    task = final["plan"][0]
    assert task["technologies"] == ["ITME"]
    assert "groundedness_l1: 비용 주장 불일치" in task["rationale"]
    assert "다시 확인할 기존 근거" in task["rationale"]           # 칸 교체 소실 완화
    ids = sorted(c["claim_id"] for c in final["market"]["claims"])
    assert ids == ["r0-market-both:claim-market-turboquant", "r1-market-ITME:claim-market-itme"]
    assert final["maturity"]["claims"][0]["claim_id"].startswith("r0-")   # 다른 관점 유지


# ── 핵심 7: 다른 유효 결과가 이미 충족한 칸은 다시 보내지 않는다 ─────────────────────────

def test_failed_retry_keeps_the_earlier_valid_result(tmp_path):
    """재시도가 또 실패해도 이전 라운드의 정상 결과는 지워지지 않고, 그 칸은 더 보내지 않는다."""
    calls = []

    def market(state):
        calls.append(state["task"]["task_id"])
        if state["task"]["round"] >= 1:
            raise TimeoutError("재시도 중 시간 초과")
        return mock_nodes.workers()["market"](state)

    # 1라운드는 편향으로 market:ITME 를 지목, 2라운드는 추가 지목 없이 돌려보낸다.
    verdicts = {"bias": {"status": "fail", "reasons": ["출처 편중"], "target_cells": ["market:ITME"]}}
    final = run(tmp_path, mock_nodes.workers() | {"market": market},
                quality(schedule=[verdicts, {}]))

    # r1 재시도가 일시 오류로 실패했지만 그 칸은 r0 결과가 유효하므로 r2 에서 보내지 않는다
    assert calls == ["r0-market-both", "r1-market-ITME"]
    assert {c["technology"] for c in final["market"]["claims"]} == {"TurboQuant", "ITME"}
    assert final["stop_reason"] == "no_replan_targets"


# ── 상한·예산 ─────────────────────────────────────────────────────────────────────

def test_replan_stops_at_max_retry(tmp_path):
    verdicts = {"coverage": {"status": "fail", "reasons": ["x"], "target_cells": ["market:ITME"]}}
    final = run(tmp_path, mock_nodes.workers(), quality(verdicts, rounds=99))
    assert final["retry_count"] == 2
    assert final["stop_reason"] == "max_retry"
    assert final["report_paths"]                               # 무한 루프 없이 발행


def test_overflow_targets_are_recorded_not_dropped_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "_cfg", lambda: {"max_retry": 2, "max_workers": 2, "max_steps": 12,
                                                 "max_searches": 16})
    targets = [f"{p}:ITME" for p in ("maturity", "market", "stakeholder", "domain_assessment")]
    verdicts = {"coverage": {"status": "fail", "reasons": ["x"], "target_cells": targets}}
    final = run(tmp_path, mock_nodes.workers(), quality(verdicts))

    assert len(final["plan"]) == 2
    assert final["last_decision"]["decision"] == "dispatch"
    log = (tmp_path / "replan-test" / "decisions.jsonl").read_text(encoding="utf-8")
    assert '"overflow": ["r1-stakeholder-ITME", "r1-domain_assessment-ITME"]' in log


def test_web_budget_is_shared_and_exhausted_tasks_are_not_sent():
    plan = [planner.default_task("stakeholder", ["TurboQuant"], 1, "d"),
            planner.default_task("stakeholder", ["ITME"], 1, "d"),
            planner.default_task("market", ["ITME"], 1, "d")]
    used = [{"meta": {"web_searches_used": 13}}]

    keep, skipped = planner.assign_web_budget(plan, used)          # 남은 예산 3 → 2·1
    assert [t.get("web_budget") for t in keep] == [2, 1, 0]

    keep, skipped = planner.assign_web_budget(plan, [{"meta": {"web_searches_used": 16}}])
    assert [t["task_id"] for t in keep] == ["r1-market-ITME"]
    assert [r["task"]["task_id"] for r in skipped] == ["r1-stakeholder-TurboQuant",
                                                       "r1-stakeholder-ITME"]
    assert all(r["status"] == "failed" and r["error_kind"] == "permanent" for r in skipped)


def test_initial_plan_gives_stakeholder_the_whole_web_budget(tmp_path):
    seen = {}

    def stakeholder(state):
        seen["budget"] = state["task"]["web_budget"]
        return mock_nodes.workers()["stakeholder"](state)

    run(tmp_path, mock_nodes.workers() | {"stakeholder": stakeholder}, quality(rounds=0))
    assert seen["budget"] == 16
