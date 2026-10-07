"""Orchestrator — 조사 계획 — 담당 A (계획서 §4-1)

research 결과를 받아 4관점 조사를 Task 목록(`plan`)으로 만든다. Worker 수는 이 목록의
길이로 실행 시점에 정해진다(Dynamic Fan-out). 코드에 Worker 목록이 고정돼 있지 않다.

최초 계획은 결정적 기본 계획(관점마다 두 기술을 함께 조사하는 Task 1개)이다.
사전 조사·개수 규칙·LLM 분해는 A3 에서 붙인다.

**재계획**(품질 평가가 orchestrator 로 돌려보냈을 때, 계획서 §4-1)
- 대상: ① 일시 오류(transient)로 실패한 칸 ② 품질 평가가 지목한 칸(`verdicts[*].target_cells`).
  다른 유효 결과가 이미 충족한 칸은 뺀다 — 판정 단위는 실패 수가 아니라 미충족 칸이다.
  영구 오류(permanent)는 다시 돌려도 같으므로 재시도하지 않는다.
- 우선순위 순으로 최대 `max_workers` 개. 넘친 대상은 결정 로그에 남긴다(조용히 빼지 않는다).
- 대상이 한 기술이면 그 기술만 조사한다. 칸 단위 교체로 사라질 수 있는 기존 유효 Claim 은
  Task 의 rationale 에 "다시 확인할 근거"로 넘긴다.
- 보낼 대상이 없으면 빈 계획 → dispatch 가 publish 로 보낸다.
- 웹 검색은 실행 전체 예산이다. 라운드마다 남은 예산을 stakeholder Task 에 나누고, 0 이면
  보내지 않고 실행 실패 결과(permanent)를 남겨 execution_gap 이 되게 한다.
"""

from src.observability import log_decision
from src.orchestrator.selection import latest_by_cell, select_active_worker_results
from src.schema import PERSPECTIVES, TECHS, Task, WorkerResult
from src.settings import settings

# 품질 평가 기준 중 재조사로 고칠 수 있는 것의 우선순위. 실행 실패가 가장 먼저다.
VERDICT_PRIORITY = ["groundedness_l1", "coverage", "bias"]

# 관점별 평가 기준. focus·질의 템플릿과 사전 조사 질의에 쓴다.
CRITERIA = {
    "maturity": "기술 성숙도 TRL 실증 수준과 상위 단계 미도달 이유",
    "market": "시장 규모 상용 채택 생태계 지지",
    "stakeholder": "경쟁사 도입 기업 개발자 투자자 반응",
    "domain_assessment": "도메인 적용 적합성과 운영 제약",
}


def _task_id(round_: int, perspective: str, technologies: list[str]) -> str:
    target = "both" if len(technologies) == len(TECHS) else technologies[0]
    return f"r{round_}-{perspective}-{target}"


def default_task(perspective: str, technologies: list[str], round_: int, domain: str,
                 rationale: str = "기본 계획") -> dict:
    """결정적 Task 한 건. LLM 분해가 실패해도 같은 개수로 이것을 쓴다."""
    criteria = CRITERIA[perspective]
    return Task(
        task_id=_task_id(round_, perspective, technologies),
        perspective=perspective,
        technologies=technologies,
        focus=f"{' · '.join(technologies)} — {criteria}",
        queries=[f"{tech} {criteria}" for tech in technologies][:3],
        rationale=f"{rationale} (도메인: {domain})",
        round=round_,
    ).model_dump()


def default_plan(state, round_: int = 0) -> list[dict]:
    """관점마다 두 기술을 함께 조사하는 Task 1개 — 4개."""
    domain = (state.get("run_config") or {}).get("domain", "")
    return [default_task(p, list(TECHS), round_, domain) for p in PERSPECTIVES]


def _cfg() -> dict:
    return settings()["orchestrator"]


def assign_web_budget(plan: list[dict], worker_results: list[dict]) -> tuple[list[dict], list[dict]]:
    """남은 웹 검색 예산을 이번 라운드 stakeholder Task 에 나눈다.

    돌려주는 값: (보낼 계획, 예산이 없어 보내지 못한 Task 의 실패 결과)
    """
    used = sum((r.get("meta") or {}).get("web_searches_used", 0) for r in worker_results or [])
    remaining = max(0, _cfg()["max_searches"] - used)
    web = [t for t in plan if t["perspective"] == "stakeholder"]
    if not web:
        return plan, []
    share, extra = divmod(remaining, len(web))
    budgets = {t["task_id"]: share + (1 if i < extra else 0) for i, t in enumerate(web)}

    keep, skipped = [], []
    for t in plan:
        if t["perspective"] != "stakeholder":
            keep.append(t)
        elif budgets[t["task_id"]] > 0:
            keep.append(t | {"web_budget": budgets[t["task_id"]]})
        else:
            skipped.append(WorkerResult(
                task=Task.model_validate(t | {"web_budget": 0}), round=t["round"], status="failed",
                error_kind="permanent", error="웹 검색 예산 소진 — Worker 를 보내지 않음").model_dump())
    return keep, skipped


def replan_targets(state) -> tuple[list[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """재조사할 (관점, 기술) 칸과 칸별 사유. 우선순위 순서로 돌려준다."""
    results = state.get("worker_results") or []
    latest = latest_by_cell(results)
    reasons: dict[tuple[str, str], list[str]] = {}

    for cell, result in latest.items():                       # ① 일시 오류 실패
        if result["status"] == "failed" and result["error_kind"] == "transient":
            reasons.setdefault(cell, []).append(f"실행 실패 재시도: {result['error']}")

    verdicts = (state.get("quality_eval") or {}).get("verdicts") or {}
    order = VERDICT_PRIORITY + sorted(set(verdicts) - set(VERDICT_PRIORITY))
    for name in order:                                        # ② 품질 평가가 지목한 칸
        verdict = verdicts.get(name) or {}
        if verdict.get("status") != "fail":
            continue
        for target in verdict.get("target_cells") or []:
            perspective, _, tech = target.partition(":")
            if perspective in PERSPECTIVES and tech in TECHS:
                reasons.setdefault((perspective, tech), []).append(
                    f"{name}: {'; '.join(verdict.get('reasons') or []) or '미달'}")

    # 영구 오류로 실패한 칸은 다시 돌려도 같다 — 품질 평가가 지목해도 보내지 않는다.
    blocked = {cell for cell, r in latest.items()
               if r["status"] == "failed" and r["error_kind"] == "permanent"}
    return [cell for cell in reasons if cell not in blocked], reasons


def _existing_evidence(state, perspective: str, tech: str) -> str:
    """칸 단위 교체로 사라질 수 있는 기존 유효 Claim 을 새 Worker 에 넘긴다."""
    selected = select_active_worker_results(state.get("worker_results") or [],
                                            state.get("claim_flags"))
    claims = (selected["assessments"].get(perspective) or {}).get("claims") or []
    mine = [c for c in claims if c["technology"] in (tech, "both")]
    if not mine:
        return ""
    cited = sorted({e for c in mine for e in c["evidence_ids"]})
    return f" · 다시 확인할 기존 근거: Claim {len(mine)}건, 인용 청크 {', '.join(cited[:8])}"


def replan(state, round_: int) -> tuple[list[dict], dict]:
    """미충족 칸만 새 Task 로 만든다. 돌려주는 값: (계획, 결정 로그용 정보)."""
    cells, reasons = replan_targets(state)
    limit = _cfg()["max_workers"]
    domain = (state.get("run_config") or {}).get("domain", "")

    by_perspective: dict[str, list[str]] = {}
    for perspective, tech in cells:
        by_perspective.setdefault(perspective, []).append(tech)

    plan = []
    for perspective, techs in by_perspective.items():
        techs = [t for t in TECHS if t in techs]               # 순서 고정
        why = " / ".join(r for t in techs for r in reasons[(perspective, t)])
        carry = "".join(_existing_evidence(state, perspective, t) for t in techs)
        plan.append(default_task(perspective, techs, round_, domain,
                                 rationale=f"재계획 — {why}{carry}"))
    overflow = [t["task_id"] for t in plan[limit:]]
    return plan[:limit], {"targets": [f"{p}:{t}" for p, t in cells], "overflow": overflow}


def orchestrator(state) -> dict:
    """계획을 세워 `plan` 에 둔다. 다음 엣지(dispatch)가 Task 마다 Worker 를 띄운다."""
    cfg = _cfg()
    step = (state.get("step_count") or 0) + 1
    results = state.get("worker_results") or []

    if not results:
        plan, skipped = assign_web_budget(default_plan(state, round_=0), results)
        decision = log_decision(state, "orchestrator", "dispatch", "최초 계획 — 결정적 기본 계획",
                                round=0, tasks=[t["task_id"] for t in plan])
        return {"plan": plan, "retry_count": 0, "step_count": step, "last_decision": decision,
                "worker_results": skipped}

    retry = state.get("retry_count") or 0
    if retry >= cfg["max_retry"] or step > cfg["max_steps"]:
        why = "max_retry" if retry >= cfg["max_retry"] else "max_steps"
        decision = log_decision(state, "orchestrator", "stop", f"상한 도달: {why}", round=retry)
        return {"plan": [], "step_count": step, "stop_reason": why, "last_decision": decision}

    round_ = retry + 1
    plan, info = replan(state, round_)
    plan, skipped = assign_web_budget(plan, results)
    if not plan:
        decision = log_decision(state, "orchestrator", "stop", "재계획할 대상 없음",
                                round=round_, skipped=[r["task"]["task_id"] for r in skipped], **info)
        return {"plan": [], "retry_count": round_, "step_count": step,
                "stop_reason": "no_replan_targets", "last_decision": decision,
                "worker_results": skipped}

    decision = log_decision(state, "orchestrator", "dispatch", "재계획 — 미충족 칸만",
                            round=round_, tasks=[t["task_id"] for t in plan],
                            skipped=[r["task"]["task_id"] for r in skipped], **info)
    return {"plan": plan, "retry_count": round_, "step_count": step, "last_decision": decision,
            "worker_results": skipped}
