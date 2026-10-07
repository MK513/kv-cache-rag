"""Orchestrator — 조사 계획 — 담당 A (계획서 §4-1)

research 결과를 받아 4관점 조사를 Task 목록(`plan`)으로 만든다. Worker 수는 이 목록의
길이로 실행 시점에 정해진다(Dynamic Fan-out). 코드에 Worker 목록이 고정돼 있지 않다.

A1 단계는 결정적 기본 계획(관점마다 두 기술을 함께 조사하는 Task 1개)만 쓴다.
사전 조사·개수 규칙·LLM 분해(A3)와 재계획(A2)은 이 파일에 이어서 붙인다.
"""

from src.observability import log_decision
from src.schema import PERSPECTIVES, TECHS, Task

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


def orchestrator(state) -> dict:
    """계획을 세워 `plan` 에 둔다. 다음 엣지(dispatch)가 Task 마다 Worker 를 띄운다."""
    step = (state.get("step_count") or 0) + 1
    if state.get("worker_results"):
        # 품질 평가 뒤 다시 들어온 경우 — 재계획은 A2 에서 붙인다. 지금은 더 보내지 않는다.
        decision = log_decision(state, "orchestrator", "stop", "재계획 미구현(A2)", round=None)
        return {"plan": [], "step_count": step, "stop_reason": "replan_not_implemented",
                "last_decision": decision}

    plan = default_plan(state, round_=0)
    decision = log_decision(state, "orchestrator", "dispatch", "최초 계획 — 결정적 기본 계획",
                            round=0, tasks=[t["task_id"] for t in plan])
    return {"plan": plan, "retry_count": 0, "step_count": step, "last_decision": decision}
