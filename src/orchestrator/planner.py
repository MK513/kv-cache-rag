"""Orchestrator — 조사 계획 — 담당 A (계획서 §4-1)

research 결과를 받아 4관점 조사를 Task 목록(`plan`)으로 만든다. Worker 수는 이 목록의
길이로 실행 시점에 정해진다(Dynamic Fan-out). 코드에 Worker 목록이 고정돼 있지 않다.

**최초 계획** (계획서 §4-1)
1. 근거 가용성 사전 조사(`probe`, 결정적·LLM 없음) — 관점 허용 컬렉션에서 "기술 + 평가 기준 +
   도메인" 으로 검색해 유사도 ≥ τ 인 청크만 세고, chunk_id 중복 제거, 출처 묶음 수를 센다.
2. Task 개수 규칙(`task_specs`, 결정적) — 두 기술 모두 청크 ≥ T 면 기술별 2개, 한 기술 청크 0 이면
   다른 기술 1개 + 계획 단계 근거 공백, 그 외 두 기술 함께 1개. stakeholder 는 웹이라 1개 → 4~7개.
3. LLM 분해(`decompose`) — 정해진 Task 마다 focus·queries·rationale 만 채운다. **개수는 LLM 이
   바꾸지 못한다.** 개수가 다르거나 검증·호출에 실패하면 같은 개수의 결정적 기본 계획을 쓴다.
τ 가 아직 보정되지 않았으면(설정 null) 사전 조사를 건너뛰고 관점마다 Task 1개로 둔다.

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

from pydantic import BaseModel, Field

from src.llm import get_llm
from src.observability import log_decision
from src.orchestrator.selection import latest_by_cell, select_active_worker_results
from src.schema import PERSPECTIVES, TECHS, Gap, Task, WorkerMeta, WorkerResult
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


# ── 최초 계획: 사전 조사 → 개수 규칙 → LLM 분해 ─────────────────────────────────

# 사전 조사 대상과 검색 역할 이름. stakeholder 는 웹이라 사전 조사하지 않는다.
PROBE_ROLE = {"maturity": "maturity", "market": "market", "domain_assessment": "domain"}
PROBE_TOP_K = 20
ROLE_FOR_GAP = {"maturity": "maturity", "market": "market", "stakeholder": "stakeholder",
                "domain_assessment": "domain"}


def probe_query(perspective: str, tech: str, domain: str) -> str:
    return f"{tech} {CRITERIA[perspective]} {domain}".strip()


def probe(state, search=None) -> dict | None:
    """(관점, 기술) → {chunks, groups, queries}. τ 가 보정되지 않았으면 None."""
    tau = (_cfg().get("split") or {}).get("tau")
    if tau is None:
        return None
    if search is None:
        from src.rag.retrieve import search                  # 임베딩을 끌고 오므로 호출 시점에
    from src.rag.retrieve import ROLE_COLLECTIONS
    groups_of = (settings().get("quality") or {}).get("source_groups") or {}
    domain = (state.get("run_config") or {}).get("domain", "")

    found = {}
    for perspective, role in PROBE_ROLE.items():
        for tech in TECHS:
            query = probe_query(perspective, tech, domain)
            chunks = {}
            for collection in ROLE_COLLECTIONS[role]:
                for hit in search(query, collection=collection, technology=tech,
                                  top_k=PROBE_TOP_K, perspective=role):
                    if hit["cosine_score"] >= tau:
                        chunks[hit["chunk_id"]] = hit
            groups = {groups_of.get(h["source_id"], h["source_id"]) for h in chunks.values()}
            found[(perspective, tech)] = {"chunks": len(chunks), "groups": len(groups),
                                          "queries": [query]}
    return found


def task_specs(found: dict | None) -> tuple[list[tuple[str, list[str]]], list[tuple[str, str, list[str]]]]:
    """개수 규칙. 돌려주는 값: ([(관점, 기술 목록)], [(관점, 기술, 사전 조사 질의) — 계획 단계 공백])."""
    if found is None:
        return [(p, list(TECHS)) for p in PERSPECTIVES], []
    threshold = (_cfg().get("split") or {}).get("T") or 1
    specs, gaps = [], []
    for perspective in PERSPECTIVES:
        if perspective not in PROBE_ROLE:
            specs.append((perspective, list(TECHS)))
            continue
        counts = {tech: found[(perspective, tech)]["chunks"] for tech in TECHS}
        empty = [tech for tech in TECHS if counts[tech] == 0]
        if len(empty) == len(TECHS):
            specs.append((perspective, list(TECHS)))         # 둘 다 없으면 Worker 가 직접 확인한다
        elif empty:
            other = [tech for tech in TECHS if tech not in empty]
            specs.append((perspective, other))
            gaps += [(perspective, tech, found[(perspective, tech)]["queries"]) for tech in empty]
        elif all(counts[tech] >= threshold for tech in TECHS):
            specs += [(perspective, [tech]) for tech in TECHS]
        else:
            specs.append((perspective, list(TECHS)))
    return specs, gaps


def planned_gap_result(perspective: str, tech: str, queries: list[str], domain: str) -> dict:
    """사전 조사에서 근거가 0건인 칸 — Worker 를 보내지 않고 조사 이력이 있는 근거 공백을 남긴다."""
    task = Task.model_validate(default_task(perspective, [tech], 0, domain,
                                            rationale="사전 조사 근거 없음 — Worker 미배정"))
    gap = Gap(role=ROLE_FOR_GAP[perspective], technology=tech, kind="evidence_gap",
              item=f"{tech} {perspective} 근거 미확보",
              reason="사전 조사에서 유사도 임계값 이상 근거 0건", attempted_queries=queries)
    assessment = {"claims": [], "evidence": [], "sources": [], "gaps": [gap.model_dump()],
                  "status": "partial"}
    return WorkerResult(task=task, round=0, status="partial", assessment=assessment,
                        meta=WorkerMeta(attempted_queries=queries, retrieved=0)).model_dump()


class _PlannedTask(BaseModel):
    focus: str = Field(min_length=1, description="이 Task 가 답할 하위 질문 한 문장")
    queries: list[str] = Field(min_length=1, max_length=3, description="검색 질의 1~3개")
    rationale: str = Field(description="왜 이 하위 질문이 필요한가 (사전 조사·research 공백 근거)")


class _Decomposition(BaseModel):
    tasks: list[_PlannedTask]


def _decompose_prompt(state, specs, found) -> str:
    domain = (state.get("run_config") or {}).get("domain", "")
    gaps = [g.get("item", "") for g in (state.get("research") or {}).get("gaps") or []][:8]
    lines = []
    for i, (perspective, techs) in enumerate(specs, 1):
        avail = ""
        if found and perspective in PROBE_ROLE:
            avail = ", ".join(f"{t} 청크 {found[(perspective, t)]['chunks']}·출처묶음 "
                              f"{found[(perspective, t)]['groups']}" for t in techs)
        lines.append(f"{i}. 관점={perspective} 기술={'+'.join(techs)} 평가기준={CRITERIA[perspective]}"
                     + (f" 사전조사={avail}" if avail else ""))
    return (
        "KV cache 최적화 기술 평가 보고서를 위한 조사 계획의 각 항목을 구체화한다.\n"
        f"평가 도메인: {domain}\n"
        f"선행 기술 조사가 남긴 근거 공백: {' | '.join(gaps) or '없음'}\n\n"
        f"아래 {len(specs)}개 항목마다 정확히 하나씩, 같은 순서로 작성한다. 항목을 더하거나 빼지 않는다.\n"
        "- focus: 그 관점·기술에서 이번에 답할 하위 질문 한 문장\n"
        "- queries: 문서 검색 질의 1~3개 (기술명 포함)\n"
        "- rationale: 왜 필요한지 한 문장. 특정 기술의 우열을 전제하지 않는다\n\n"
        + "\n".join(lines))


def decompose(state, specs, found=None) -> tuple[list[dict], str]:
    """정해진 개수의 Task 를 LLM 으로 구체화한다. 돌려주는 값: (계획, "llm" | "default:<사유>")."""
    domain = (state.get("run_config") or {}).get("domain", "")
    fallback = [default_task(p, techs, 0, domain) for p, techs in specs]
    try:
        out = get_llm().with_structured_output(_Decomposition).invoke(
            _decompose_prompt(state, specs, found))
        if len(out.tasks) != len(specs):
            return fallback, f"default:LLM 이 {len(out.tasks)}개를 냈다(요구 {len(specs)}개)"
        plan = []
        for (perspective, techs), planned, base in zip(specs, out.tasks, fallback):
            plan.append(Task.model_validate(base | {
                "focus": planned.focus, "queries": planned.queries[:3],
                "rationale": planned.rationale}).model_dump())
        return plan, "llm"
    except Exception as exc:                                    # 계획 실패도 그래프를 멈추지 않는다
        return fallback, f"default:{type(exc).__name__}"


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


def _initial(state, step: int) -> dict:
    """최초 계획 — 사전 조사 → 개수 규칙 → LLM 분해 → 웹 예산."""
    domain = (state.get("run_config") or {}).get("domain", "")
    try:
        found = probe(state)
    except Exception as exc:                                    # 색인이 없어도 기본 개수로 계속
        found, probe_error = None, f"{type(exc).__name__}: {exc}"[:200]
    else:
        probe_error = ""
    specs, empty = task_specs(found)
    plan, source = decompose(state, specs, found)
    plan, skipped = assign_web_budget(plan, [])
    planned_gaps = [planned_gap_result(p, t, q, domain) for p, t, q in empty]

    summary = None if found is None else {f"{p}:{t}": v["chunks"] for (p, t), v in found.items()}
    decision = log_decision(
        state, "orchestrator", "dispatch",
        f"최초 계획 — Task {len(plan)}개 ({'사전 조사 반영' if found else '사전 조사 없음'}, 분해 {source})",
        round=0, tasks=[t["task_id"] for t in plan], probe=summary, probe_error=probe_error,
        planned_gaps=[f"{p}:{t}" for p, t, _ in empty])
    return {"plan": plan, "retry_count": 0, "step_count": step, "last_decision": decision,
            "worker_results": planned_gaps + skipped}


def orchestrator(state) -> dict:
    """계획을 세워 `plan` 에 둔다. 다음 엣지(dispatch)가 Task 마다 Worker 를 띄운다."""
    cfg = _cfg()
    step = (state.get("step_count") or 0) + 1
    results = state.get("worker_results") or []

    if not results:
        return _initial(state, step)

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
