"""품질 규칙 공용 함수 — 담당 C (agent/ow-quality)

`report` 노드(무엇을 보여 줄지)와 `quality_eval` 노드(보여 준 것이 기준을 넘는지)가 **같은
규칙**을 써야 한다. 한쪽만 바꾸면 보고서는 예외를 공개했는데 평가는 모른다거나, 평가가
지목한 칸을 보고서가 다른 방식으로 세는 일이 생긴다. 그래서 둘이 함께 쓰는 판단만 여기 둔다.

LLM·파일·그래프를 건드리지 않는 순수 함수만 둔다(양쪽에서 import 해도 순환이 생기지 않게).
"""

import re
from urllib.parse import urlparse

from src.schema import PERSPECTIVES, TECHS
from src.settings import settings

# Gap.role 은 역할 이름(domain), State 키·Task.perspective 는 domain_assessment 다.
ROLE_OF = {"research": "research", "maturity": "maturity", "market": "market",
           "stakeholder": "stakeholder", "domain_assessment": "domain"}
PERSPECTIVE_OF_ROLE = {role: key for key, role in ROLE_OF.items()} | {"synthesis": "synthesis"}

# 한계·비용 Claim — 편향 ③(선택적 근거 사용)과 압축 규칙(한계·비용 Claim 1건 유지)이 함께 쓴다.
# 성능 향상만 남기고 잔여 비용을 빼는 것이 확증 편향의 전형이라, 이 판정은 넓게 잡는다.
LIMIT_RE = re.compile(
    r"비용|손실|저하|한계|제약|지연|오버헤드|복잡|부담|위험|리스크|미검증|미확인|불확실|"
    r"overhead|cost|latency|penalty|degrad|limitation|loss",
    re.IGNORECASE)


def is_limit_claim(claim: dict) -> bool:
    return bool(LIMIT_RE.search(claim.get("text") or ""))


def techs_of(technology: str) -> list[str]:
    """Claim·Gap 이 덮는 기술 칸. both 는 두 칸 모두다."""
    return list(TECHS) if technology == "both" else [technology]


def cell(perspective: str, technology: str) -> str:
    """재계획 대상 칸 표기 `perspective:tech` (schema.Verdict.target_cells)."""
    return f"{perspective}:{technology}"


def excluded_claim_ids(state: dict) -> set[str]:
    """보고서에서 빼야 하는 Claim — 품질 평가·사람 검토의 무효 판정 (계획서 §5).

    `claim_flags` 의 `invalid` 와, 옛 흐름(review 노드)의 `validation.rejected_claims` 를 함께 본다.
    `recheck` 는 빼지 않는다 — Judge 표본에 반드시 넣어 다시 볼 대상이다.
    """
    flags = state.get("claim_flags") or {}
    invalid = {cid for cid, flag in flags.items() if (flag or {}).get("status") == "invalid"}
    rejected = set((state.get("validation") or {}).get("rejected_claims") or [])
    return invalid | rejected


def recheck_claim_ids(state: dict) -> set[str]:
    flags = state.get("claim_flags") or {}
    return {cid for cid, flag in flags.items() if (flag or {}).get("status") == "recheck"}


def acceptable_gap(gap: dict) -> bool:
    """커버리지가 인정하는 근거 공백 (계획서 §7-1).

    Worker 어댑터가 실제로 실행한 질의(`attempted_queries`)가 있는 `evidence_gap` 만 인정한다.
    실행 실패(`execution_gap`)·무효 근거(`invalid_evidence`)는 조사가 끝나지 않았다는 뜻이고,
    질의 기록이 없는 공백은 LLM 이 focus 를 옮겨 적은 것과 구별할 수 없다.
    """
    return (gap.get("kind", "evidence_gap") == "evidence_gap"
            and bool(gap.get("attempted_queries")))


def gap_perspective(gap: dict) -> str:
    return PERSPECTIVE_OF_ROLE.get(gap.get("role", ""), gap.get("role", ""))


# ── 출처 묶음과 편향 지표 ①② ─────────────────────────────────────────────────

def quality_config() -> dict:
    return settings().get("quality") or {}


def source_group(source: dict) -> str:
    """출처 묶음. 설정 표 → URL 도메인 → source_id 순으로 정한다."""
    groups = quality_config().get("source_groups") or {}
    source_id = source.get("source_id", "")
    if source_id in groups:
        return groups[source_id]
    host = urlparse(source.get("url") or "").hostname or ""
    if host:
        parts = host.removeprefix("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    return source_id or "미상"


def bias_metrics(claims: list[dict], evidence: dict, sources: dict) -> dict[str, dict]:
    """기술별 인용 근거의 출처 묶음 분포 (지표 ① 묶음 수, ② 단일 묶음 비중).

    비중은 **인용 evidence 건수** 기준이다. 같은 청크를 여러 Claim 이 인용해도 한 번만 센다 —
    한 문장을 여러 번 말했다고 근거가 늘지 않는다.
    """
    cited: dict[str, set[str]] = {tech: set() for tech in TECHS}
    for claim in claims:
        for tech in techs_of(claim.get("technology", "both")):
            if tech in cited:
                cited[tech].update(claim.get("evidence_ids") or [])

    bias = quality_config().get("bias") or {}
    min_groups = bias.get("min_source_groups", 2)
    max_share = bias.get("max_group_share", 0.6)
    result = {}
    for tech, evidence_ids in cited.items():
        counts: dict[str, int] = {}
        for evidence_id in sorted(evidence_ids):
            item = evidence.get(evidence_id) or {}
            source = sources.get(item.get("source_id")) or {"source_id": item.get("source_id", "")}
            group = source_group(source)
            counts[group] = counts.get(group, 0) + 1
        total = sum(counts.values())
        share = max(counts.values()) / total if total else 0.0
        result[tech] = {"groups": counts, "n_groups": len(counts), "max_share": round(share, 3),
                        "cited": total,
                        "ok_groups": len(counts) >= min_groups,
                        "ok_share": total > 0 and share <= max_share}
    return result


def bias_exception(technology: str) -> dict | None:
    """사전 정의 편향 예외(설정 `quality.exceptions`). 없으면 None."""
    for entry in quality_config().get("exceptions") or []:
        if entry.get("technology") == technology:
            return entry
    return None


# ── Judge 표본 ───────────────────────────────────────────────────────────────

def judge_enabled(state: dict) -> bool:
    config = state.get("run_config") or {}
    if "quality_judge" in config:
        return bool(config["quality_judge"])
    return bool(quality_config().get("judge", True))


def stratified_sample(cells: dict[str, list[str]], must: set[str], size: int) -> list[str]:
    """관점×기술 층화 결정적 표본 (계획서 §7-1).

    `size` 이하면 전수. 넘으면 `recheck` Claim 을 먼저 넣고, 칸을 이름순으로 돌며 한 건씩
    채운다. 같은 입력이면 항상 같은 표본이다(난수 없음).
    """
    # both Claim 은 두 칸에 모두 들어 있다. 한 번만 센다.
    every = list(dict.fromkeys(cid for key in sorted(cells) for cid in cells[key]))
    if len(every) <= size:
        return every
    picked = [cid for cid in every if cid in must][:size]
    queues = {key: list(cells[key]) for key in sorted(cells)}
    while len(picked) < size and any(queues.values()):
        for key in sorted(queues):
            while queues[key] and queues[key][0] in picked:
                queues[key].pop(0)
            if queues[key] and len(picked) < size:
                picked.append(queues[key].pop(0))
    return picked


def claim_cells(perspective: str, claim: dict) -> list[str]:
    return [cell(perspective, tech) for tech in techs_of(claim.get("technology", "both"))]


ALL_CELLS = [cell(p, t) for p in PERSPECTIVES for t in TECHS]
