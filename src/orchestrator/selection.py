"""누적된 Worker 결과에서 지금 쓸 결과를 고른다 — 담당 A (계획서 §5)

`worker_results` 는 operator.add 로 쌓이므로 재시도·재계획 이전 결과도 남아 있다.
여기서 한 번만, 결정적으로 고른다.

- 같은 `task_id` 결과가 여러 번 오면 1회만 반영한다. 내용이 다르면 정합성 오류다.
- (관점, 기술) 칸마다 그 칸을 대상으로 한 **가장 최근 round 의 실패하지 않은** 결과만 쓴다
  (칸 단위 교체). 재시도가 또 실패해도 이전 라운드의 정상 결과를 지우지 않는다. 실패 결과만
  있는 칸은 실행 실패로 본다.
- `claim_flags` 가 invalid 인 Claim 은 어느 결과에서든 뺀다.
- 결과 순서는 병렬 도착 순서에 의존하지 않도록 (round, task_id) 로 정렬한다.
- 유효 Claim 이 없는 칸에는 원인을 구분한 Gap 을 남긴다.
    execution_gap    — 그 칸의 최신 결과가 실행 실패
    invalid_evidence — Claim 은 있었으나 모두 무효 판정
    evidence_gap     — 조사했으나 근거를 만들지 못함 (조사 이력은 Worker 가 기록한 meta)
"""

from src.schema import PERSPECTIVES, TECHS, Gap

ROLE = {"maturity": "maturity", "market": "market", "stakeholder": "stakeholder",
        "domain_assessment": "domain"}


class IntegrityError(Exception):
    """같은 task_id 에 서로 다른 결과가 왔다. 어느 쪽이 맞는지 고를 근거가 없다."""


def _dedupe(worker_results: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for result in worker_results:
        task_id = result["task"]["task_id"]
        kept = seen.get(task_id)
        if kept is None:
            seen[task_id] = result
        elif kept != result:
            raise IntegrityError(f"{task_id}: 같은 Task 결과의 내용이 다르다")
    return sorted(seen.values(), key=lambda r: (r["round"], r["task"]["task_id"]))


def latest_by_cell(worker_results: list[dict]) -> dict[tuple[str, str], dict]:
    """(관점, 기술) → 그 칸을 대상으로 한 최신의 실패하지 않은 결과. 없으면 최신 실패 결과."""
    ok, failed = {}, {}
    for result in _dedupe(worker_results):           # round 오름차순이라 뒤가 이긴다
        table = failed if result["status"] == "failed" else ok
        for tech in result["task"]["technologies"]:
            table[(result["task"]["perspective"], tech)] = result
    return failed | ok


def _cell_gap(perspective, tech, kind, item, reason, attempted=()):
    return Gap(role=ROLE[perspective], technology=tech, kind=kind, item=item, reason=reason,
               attempted_queries=list(attempted)).model_dump()


def select_active_worker_results(worker_results: list[dict], claim_flags: dict | None = None) -> dict:
    """관점별 Assessment 4개와 칸별 공백, 이번 라운드 실패 목록을 돌려준다."""
    flags = claim_flags or {}
    invalid = {cid for cid, flag in flags.items() if (flag or {}).get("status") == "invalid"}
    results = _dedupe(worker_results or [])

    latest = latest_by_cell(results)

    assessments, cell_gaps = {}, []
    for perspective in PERSPECTIVES:
        cells = {tech: latest.get((perspective, tech)) for tech in TECHS}
        covered = [tech for tech, result in cells.items() if result is not None]
        if not covered:
            continue

        # 두 기술을 함께 다룬 결과의 both Claim 은 두 칸 모두 그 결과가 최신일 때만 쓰고,
        # 그때는 두 칸 모두의 근거로 센다(시장성 에이전트는 Claim 을 both 로만 낸다).
        shared = cells[TECHS[0]] if len({id(r) for r in cells.values()}) == 1 else None
        shared_body = (shared or {}).get("assessment") or {}
        both_all = [c for c in shared_body.get("claims", []) if c["technology"] == "both"]
        both_valid = [c for c in both_all if c["claim_id"] not in invalid]

        claims, gaps, bodies = list(both_valid), [], []
        if shared_body:
            gaps += [g for g in shared_body.get("gaps", []) if g["technology"] == "both"]
            bodies.append(shared_body)
        for tech in covered:
            result = cells[tech]
            if result["status"] == "failed":
                cell_gaps.append(_cell_gap(perspective, tech, "execution_gap",
                                           f"{tech} {perspective} 조사 미완료",
                                           f"Worker 실패({result['error_kind']}): {result['error']}"))
                continue
            body = result["assessment"]
            bodies.append(body)
            mine = [c for c in body["claims"] if c["technology"] == tech]
            valid = [c for c in mine if c["claim_id"] not in invalid]
            claims += valid
            gaps += [g for g in body["gaps"] if g["technology"] == tech]
            if not valid and not both_valid:
                had = mine or both_all
                cell_gaps.append(_cell_gap(
                    perspective, tech, "invalid_evidence" if had else "evidence_gap",
                    f"{tech} {perspective} 근거 미확보",
                    "Claim 이 모두 무효 판정됨" if had else "조사했으나 근거를 확보하지 못함",
                    result["meta"]["attempted_queries"]))

        cited = {eid for c in claims for eid in c["evidence_ids"]}
        evidence = {e["evidence_id"]: e for body in bodies for e in body["evidence"]
                    if e["evidence_id"] in cited}
        used = {e["source_id"] for e in evidence.values()}
        sources = {s["source_id"]: s for body in bodies for s in body["sources"]
                   if s["source_id"] in used}

        def has_claim(tech):
            return any(c["technology"] in (tech, "both") for c in claims)

        status = "completed" if claims and all(has_claim(t) for t in covered) else "partial"
        if not claims and all(cells[t]["status"] == "failed" for t in covered):
            status = "failed"
        assessments[perspective] = {
            "claims": sorted(claims, key=lambda c: c["claim_id"]),
            "evidence": [evidence[k] for k in sorted(evidence)],
            "sources": [sources[k] for k in sorted(sources)],
            "gaps": gaps,
            "status": status,
        }

    latest_round = max((r["round"] for r in results), default=0)
    failed_now = [r for r in results if r["round"] == latest_round and r["status"] == "failed"]
    return {"assessments": assessments, "cell_gaps": cell_gaps, "failed": failed_now}
