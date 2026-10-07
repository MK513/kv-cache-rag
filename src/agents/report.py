"""보고서 생성 — 담당 C (agent/ow-quality) | 결정적 포맷터

추가 판단 LLM을 호출하지 않고, 이미 확정된 State 내용을
설계서 §9 목차에 맞춰 조립한다.

원칙:
- SUMMARY도 synthesis 결과만 사용해 결정적으로 생성한다.
- 결합 가설은 §4 본문이 아니라 §5 시사점에만 배치한다.
- conflicts는 실제로 존재할 때만 출력한다.
- 최종 REFERENCE는 reference.py가 실제 **표시된** Claim 의 근거를 기준으로 생성한다.

Orchestrator-Workers (계획서 §7-1·§7-2):
- 하나의 보고서 모델(무엇을 어느 절에 보여 주는가)에서 Markdown 과 `report_manifest` 를 함께
  만든다. 품질 평가는 Markdown 을 다시 파싱하지 않고 manifest 로 "실제로 표시된 것" 을 본다.
- 무효 Claim(`claim_flags` invalid·사람 검토 부결)은 본문·종합·참고문헌 어디에도 싣지 않는다.
- 10쪽 상한: 관점×기술 칸당 표시 상한과 압축 단계(0 기본 · 1 절반 · 2 최소 표시 수준).
  종합이 참조한 Claim 과 기술별 한계·비용 Claim 1건은 상한과 무관하게 남긴다.
- Gap 은 종류(근거 없음·조사 실행 실패·근거 무효)를 함께 표시한다.
- partial 발행이면 첫머리와 §6 에 미달 기준과 사유를 적는다(`build(partial=...)`, publish 가 호출).
"""

import math
import re

from src.agents.common import CITATION_RE, event
from src.llm import judge_model_name, llm_report, model_name
from src.orchestrator import quality_rules as rules
from src.output import reference
from src.schema import ASSESSMENT_ROLES, PERSPECTIVES, TECHS, ReportManifest
from src.settings import settings


def _bullets(items) -> str:
    """문자열 목록을 Markdown bullet로 변환한다."""
    return "\n".join(f"- {item}" for item in items) if items else "- 해당 없음"


KIND_LABEL = {"fact": "사실", "inference": "추론", "hypothesis": "가설"}
TECH_LABEL = {"both": "두 기술"}      # 내부 enum 값을 그대로 지면에 내보내지 않는다
ROLE_LABEL = {"research": "기술 조사", "maturity": "TRL", "market": "시장성",
              "stakeholder": "이해관계자", "domain": "도메인 적용", "synthesis": "종합"}
# 근거 공백의 종류(계획서 §3). 근거 없음은 조사 이력이 있을 때만 커버리지로 인정된다.
GAP_KIND_LABEL = {"evidence_gap": "근거 없음", "execution_gap": "조사 실행 실패",
                  "invalid_evidence": "근거 무효"}
CRITERION_LABEL = {"groundedness_l1": "근거 연결(L1)", "groundedness_l2": "종합 근거(L2)",
                   "neutrality": "중립성", "bias": "편향 통제", "coverage": "관점 커버리지",
                   "structure": "구조·분량"}
# item 이 이미 대상 기술을 말하고 있으면 접두어를 붙이지 않는다. LLM 이 쓴 문장이라
# "양 기술" · "두 기술" · 기술명 나열 등 표현이 갈린다.
TECH_ALIAS = {"both": ("두 기술", "양 기술", "TurboQuant", "ITME"),
              "TurboQuant": ("TurboQuant",), "ITME": ("ITME",)}


def _number_citations(markdown: str, marks: dict) -> str:
    """본문의 검증용 ID 를 REFERENCE 번호로 바꾼다.

    `[cefcd0973374]` 는 chunk_id 다 — validate 가 대조하는 내부 값이라 파이프라인에는
    필요하지만 독자는 어느 출처인지 알 수 없다. REFERENCE 에도 그 ID 가 없다.
    번호를 못 찾으면 원래 ID 를 남긴다. 조용히 지우면 인용이 사라진 것처럼 보인다.
    """
    numbered = CITATION_RE.sub(
        lambda m: f"[{marks[m.group(1)]}]" if m.group(1) in marks else m.group(0), markdown)
    # 같은 출처의 서로 다른 청크를 잇달아 인용하면 [1][1]·[1] [1] 이 된다. 지면에는 한 번이면 된다.
    return re.sub(r"\[(\d+)\](?:\s*\[\1\])+", r"[\1]", numbered)


def _held(state: dict) -> str:
    """평가 보류를 남긴 관점이 있으면 머리말에 경고를 단다(§5 — 자료가 없는 항목은 평가 보류)."""
    held = [node for node in ASSESSMENT_ROLES
            if (state.get(node) or {}).get("status") in {"partial", "failed"}]
    if not held:
        return ""
    return ("\n> ⚠️ 근거를 확보하지 못해 **평가 보류** 항목을 남긴 관점: "
            f"{', '.join(held)}\n")


# ── 표시 상한과 압축 단계 ─────────────────────────────────────────────────────

def _caps(compaction: int) -> dict:
    """압축 단계별 표시 상한. 0 기본 · 1 절반 · 2 최소 표시 수준(칸마다 1개).

    SUMMARY·REFERENCE·편향 예외 문구는 어느 단계에서도 줄이지 않는다(계획서 §7-2).
    """
    cfg = settings().get("report") or {}
    claims = cfg.get("max_claims_per_cell") or 3
    implications = cfg.get("max_implications") or 4
    gaps = cfg.get("max_gaps") or 12
    chars = cfg.get("max_quote_chars") or 1500
    if compaction <= 0:
        return {"claims": claims, "implications": implications, "gaps": gaps, "chars": chars}
    if compaction == 1:
        return {"claims": max(1, math.ceil(claims / 2)),
                "implications": max(1, math.ceil(implications / 2)),
                "gaps": max(1, math.ceil(gaps / 2)), "chars": chars}
    return {"claims": 1, "implications": 1, "gaps": max(1, math.ceil(gaps / 4)), "chars": chars}


def _next_compaction(state: dict) -> int:
    """품질 평가가 쪽수 초과로 되돌려 보냈으면 한 단계 더 압축한다. 아니면 이전 단계를 유지한다."""
    previous = int((state.get("report_manifest") or {}).get("compaction") or 0)
    quality = state.get("quality_eval") or {}
    structure = (quality.get("verdicts") or {}).get("structure") or {}
    over = any(str(reason).startswith("pages") for reason in structure.get("reasons") or [])
    if (quality.get("next") == "report" and over
            and quality.get("evaluated_report_version") == state.get("report_version")):
        limit = (settings().get("report") or {}).get("max_compaction", 2)
        return min(previous + 1, limit)
    return previous


def _clip(text: str, limit: int) -> str:
    """Claim 본문을 표시 길이로 자른다. 잘린 부분의 인용 ID 는 끝에 다시 붙여 근거를 잃지 않는다."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit].rstrip()
    lost = [cid for cid in CITATION_RE.findall(text[limit:]) if f"[{cid}]" not in head]
    return head + "…" + "".join(f" [{cid}]" for cid in dict.fromkeys(lost))


# ── 보고서 모델: 무엇을 어느 절에 보여 주는가 ────────────────────────────────────

def _text(item) -> str:
    return item if isinstance(item, str) else (item.get("text") or "")


def _refs(item) -> list[str]:
    return [] if isinstance(item, str) else list(item.get("claim_ids") or [])


def _usable(items: list, excluded: set[str]) -> list:
    """무효 Claim 에만 기댄 종합 항목은 싣지 않는다. 남은 참조에서 무효 Claim 은 뺀다."""
    kept = []
    for item in items:
        refs = _refs(item)
        if refs and all(cid in excluded for cid in refs):
            continue
        if refs and not isinstance(item, str):
            item = item | {"claim_ids": [cid for cid in refs if cid not in excluded]}
        kept.append(item)
    return kept


def _select(state: dict, excluded: set[str], pinned: set[str], per_cell: int,
            groups_of) -> dict[str, list]:
    """관점(역할)·기술 칸마다 표시할 Claim 을 고른다. 원래 순서를 유지한다.

    - 칸 상한 `per_cell`. 종합이 참조한 Claim(`pinned`)은 먼저 넣고 상한을 넘어도 남긴다 —
      종합 문장이 보고서에 없는 Claim 을 가리키면 L2 를 통과할 수 없다.
    - 남은 자리는 그 기술에 아직 표시하지 않은 출처 묶음을 인용한 Claim 부터 채운다. 앞에서부터
      자르면 압축 단계에서 칸마다 같은 논문 Claim 만 남아 편향 ①② 가 표시 선택 때문에 실패한다.
    - 기술별로 한계·비용 Claim 이 하나도 표시되지 않았으면 숨은 것 중 첫 건을 더한다
      (압축 규칙, 편향 ③ 선택적 근거 사용 방지).
    - 그래도 기술별 표시 근거가 편향 ①② 기준을 넘지 못하면, 다른 출처 묶음을 인용한 숨은
      Claim 을 기술마다 최대 2건 더한다(수집 근거로는 통과하는데 표시만 몰리는 경우의 수리).
    """
    seen: dict[str, set[str]] = {tech: set() for tech in TECHS}

    def novel(claim) -> bool:
        return any(groups_of(claim) - seen[t] for t in rules.techs_of(claim.get("technology", "both")))

    def remember(claim):
        for t in rules.techs_of(claim.get("technology", "both")):
            seen[t] |= groups_of(claim)

    shown: dict[str, list] = {}
    for role in ASSESSMENT_ROLES:
        claims = [c for c in (state.get(role) or {}).get("claims") or []
                  if c.get("claim_id") not in excluded]
        keep: set[str] = set()
        buckets: dict[str, list] = {}
        for claim in claims:
            buckets.setdefault(claim.get("technology", "both"), []).append(claim)
        for items in buckets.values():
            chosen = [c for c in items if c["claim_id"] in pinned]
            for claim in chosen:
                remember(claim)
            rest = [c for c in items if c["claim_id"] not in pinned]
            while rest and len(chosen) < per_cell:
                pick = next((c for c in rest if novel(c)), rest[0])
                rest.remove(pick)
                chosen.append(pick)
                remember(pick)
            keep.update(c["claim_id"] for c in chosen)
        shown[role] = [c for c in claims if c["claim_id"] in keep]

    def add(role, claim):
        order = [c["claim_id"] for c in (state.get(role) or {}).get("claims") or []]
        shown[role] = sorted(shown[role] + [claim], key=lambda c: order.index(c["claim_id"]))
        remember(claim)

    def hidden(tech):
        return [(role, c) for role in ASSESSMENT_ROLES
                for c in (state.get(role) or {}).get("claims") or []
                if c.get("claim_id") not in excluded and c not in shown[role]
                and tech in rules.techs_of(c.get("technology", "both"))]

    # 품질 평가가 편향 ③(선택적 근거)으로 되돌려 보낸 기술은 숨은 한계·비용 Claim 을 모두 싣는다.
    bias = ((state.get("quality_eval") or {}).get("verdicts") or {}).get("bias") or {}
    flagged = {t for t in TECHS for reason in bias.get("reasons") or []
               if reason.startswith(f"③ {t} 선택적")}
    for tech in TECHS:
        limits = [(r, c) for r, c in hidden(tech) if rules.is_limit_claim(c)]
        if tech in flagged:
            for limit in limits:
                add(*limit)
            continue
        if any(rules.is_limit_claim(c) and tech in rules.techs_of(c.get("technology", "both"))
               for items in shown.values() for c in items):
            continue
        if limits:
            add(*limits[0])

    for tech in TECHS:
        for _ in range(2):
            counts = _group_counts([c for items in shown.values() for c in items], tech, groups_of)
            if not counts or _balanced(counts):
                break
            dominant = max(counts, key=counts.get)
            extra = next(((r, c) for r, c in hidden(tech) if groups_of(c) - {dominant}), None)
            if not extra:
                break
            add(*extra)
    return shown


def _group_counts(claims: list[dict], tech: str, groups_of) -> dict[str, int]:
    """기술별 표시 근거의 출처 묶음 분포 — 근사치(Claim 단위). 최종 판정은 quality_rules.bias_metrics."""
    counts: dict[str, int] = {}
    for claim in claims:
        if tech in rules.techs_of(claim.get("technology", "both")):
            for group in groups_of(claim):
                counts[group] = counts.get(group, 0) + 1
    return counts


def _balanced(counts: dict[str, int]) -> bool:
    bias = rules.quality_config().get("bias") or {}
    total = sum(counts.values())
    return (len(counts) >= bias.get("min_source_groups", 2)
            and max(counts.values()) / total <= bias.get("max_group_share", 0.6))


def _registries(state: dict) -> tuple[dict, dict]:
    """evidence·source 조회표. 합류 레지스트리가 있으면 그것을, 없으면 Assessment 에서 모은다."""
    evidence = dict(state.get("evidence_registry") or {})
    sources = dict(state.get("source_registry") or {})
    for role in ASSESSMENT_ROLES:
        assessment = state.get(role) or {}
        for item in assessment.get("evidence") or []:
            evidence.setdefault(item.get("evidence_id"), item)
        for item in assessment.get("sources") or []:
            sources.setdefault(item.get("source_id"), item)
    return evidence, sources


def _pick_gaps(state: dict, shown: dict, cap: int) -> tuple[list[dict], int]:
    """§6 에 표시할 Gap. 커버리지에 필요한 공백과 실행 실패·무효는 상한과 무관하게 싣는다."""
    covered = {cell for role in PERSPECTIVES for c in shown.get(role) or []
               for cell in rules.claim_cells(role, c)}
    required, failures, rest = [], [], []
    for gap in state.get("gaps") or []:
        perspective = rules.gap_perspective(gap)
        cells = [rules.cell(perspective, t) for t in rules.techs_of(gap.get("technology", "both"))]
        if gap.get("kind", "evidence_gap") != "evidence_gap":
            failures.append(gap)
        elif (perspective in PERSPECTIVES and rules.acceptable_gap(gap)
              and any(cell not in covered for cell in cells)):
            required.append(gap)
        else:
            rest.append(gap)
    room = max(0, cap - len(required) - len(failures))
    chosen = required + failures + rest[:room]
    order = {id(g): i for i, g in enumerate(state.get("gaps") or [])}
    return sorted(chosen, key=lambda g: order[id(g)]), len(rest) - min(room, len(rest))


def _model(state: dict, compaction: int) -> dict:
    """보고서 모델 — Markdown 과 manifest 가 모두 여기서 나온다."""
    caps = _caps(compaction)
    excluded = rules.excluded_claim_ids(state)
    synthesis = state.get("synthesis") or {}
    agreements = _usable(synthesis.get("agreements") or [], excluded)
    conflicts = _usable(synthesis.get("conflicts") or [], excluded)
    shown_agreements = agreements[:caps["implications"]]
    shown_conflicts = conflicts[:caps["implications"]]
    pinned = {cid for item in shown_agreements + shown_conflicts for cid in _refs(item)}
    evidence, sources = _registries(state)

    def groups_of(claim) -> set[str]:
        return {rules.source_group(sources.get((evidence.get(e) or {}).get("source_id"))
                                   or {"source_id": (evidence.get(e) or {}).get("source_id", "")})
                for e in claim.get("evidence_ids") or []}

    shown = _select(state, excluded, pinned, caps["claims"], groups_of)
    gaps, hidden_gaps = _pick_gaps(state, shown, caps["gaps"])

    displayed = [c for role in ASSESSMENT_ROLES for c in shown[role]]
    metrics = rules.bias_metrics(displayed, evidence, sources)
    exceptions = []
    for tech in TECHS:
        m = metrics[tech]
        entry = rules.bias_exception(tech)
        if entry and m["cited"] and not (m["ok_groups"] and m["ok_share"]):
            exceptions.append(entry | {"metrics": m})

    return {"compaction": compaction, "caps": caps, "excluded": excluded,
            "agreements": agreements, "conflicts": conflicts,
            "shown_agreements": shown_agreements, "shown_conflicts": shown_conflicts,
            "shown": shown, "gaps": gaps, "hidden_gaps": hidden_gaps,
            "evidence": evidence, "exceptions": exceptions}


# ── 렌더링 ───────────────────────────────────────────────────────────────────

def _claims(claims: list[dict], chars: int = 10_000) -> str:
    """Claim 을 본문으로 편다.

    §6 — *공개 자료에서 확인한 사실과 그 사실에서 추론한 내용을 구분한다.* 사실이 기본이라
    따로 표시하지 않고, 추론·가설만 전제와 함께 표시한다. 모든 문단에 "[사실]" 을 붙이면
    구분이 아니라 소음이 된다.
    """
    if not claims:
        return "- 승인된 주장 없음. 근거 공백은 §6 을 참고한다."

    blocks = []
    for claim in claims:
        # 모델이 Claim 본문에 쓴 `## TurboQuant` 같은 제목은 보고서 목차를 깨뜨린다. 제목 표시만 뗀다.
        body = _clip(re.sub(r"^#{1,6}\s*", "", claim.get("text") or "", flags=re.MULTILINE), chars)
        kind = claim.get("kind", "")
        if kind != "fact":
            note = KIND_LABEL.get(kind, kind)
            explanation = (claim.get("explanation") or "").strip()
            # 모델이 "전제는 ..." 으로 시작하는 경우가 잦다. "전제: 전제는" 이 되지 않게 한다.
            for prefix in ("전제는 ", "전제: ", "전제 "):
                if explanation.startswith(prefix):
                    explanation = explanation[len(prefix):].lstrip()
                    break
            body += f"\n\n> **{note}** — 실측 결과가 아니다."
            body += f" 전제: {explanation}" if explanation else ""
        blocks.append(body)
    return "\n\n".join(blocks)


def _cites(item, claims_by_id: dict) -> str:
    """종합 항목이 기댄 Claim 의 근거를 인용 표시로 붙인다(REFERENCE 번호로 바뀐다)."""
    marks = []
    for cid in _refs(item):
        for evidence_id in (claims_by_id.get(cid) or {}).get("evidence_ids") or []:
            if CITATION_RE.fullmatch(f"[{evidence_id}]") and evidence_id not in marks:
                marks.append(evidence_id)
    return (" " + "".join(f"[{e}]" for e in marks[:3])) if marks else ""


def _carried(state: dict) -> str:
    """이월된 검토 판정을 밝힌다(§10 — 조사 수행 시점과 정보의 검토 범위를 명시적으로 기록).

    주장과 근거가 완전히 같을 때만 옮기지만, 사람이 **이번 실행에서** 다시 본 것은 아니다.
    """
    validation = state.get("validation") or {}
    carried = validation.get("carried_claims") or []
    if not carried:
        return ""
    verdicts = validation.get("claim_verdicts") or {}
    runs = sorted({(verdicts.get(cid) or {}).get("carried_from", "") for cid in carried} - {""})
    return (f"- 내용 검토 이월: 주장 {len(carried)}건은 이전 실행"
            f"{'(' + ', '.join(runs) + ')' if runs else ''}의 판정을 그대로 사용했다. "
            f"주장 문장과 인용 근거가 완전히 같은 경우에만 옮겼으며, 이번 실행에서 다시 검토하지 않았다.")


def _reused() -> str:
    """캐시로 재사용한 모델 응답을 밝힌다(§6 — 실제 비용은 실행 기록으로 확인한다).

    적중은 이번 실행에서 모델이 새로 판단하지 않았다는 뜻이다. 조사 시점 해석에 영향을 준다.
    """
    report = llm_report()
    hits = report.get("cache_hits") or 0
    if not hits:
        return ""
    return (f"\n- 모델 응답 재사용: {hits}건은 이전 실행과 프롬프트가 같아 캐시된 응답을 "
            f"그대로 사용했다(이번 실행에서 새로 생성 {report.get('cache_misses', 0)}건).")


def _gap_line(gap: dict) -> str:
    item, technology = gap.get("item", ""), gap.get("technology", "")
    named = any(alias in item for alias in TECH_ALIAS.get(technology, (technology,)) if alias)
    head = item if named else f"{TECH_LABEL.get(technology, technology)} {item}".strip()
    line = f"{head}: {gap.get('reason', '')}".strip(": ").strip()
    kind = gap.get("kind", "evidence_gap")
    if kind != "evidence_gap":
        line += f" *({GAP_KIND_LABEL.get(kind, kind)} — 조사가 끝나지 않은 칸)*"
    elif gap.get("attempted_queries"):
        line += f" (조사 질의 {len(gap['attempted_queries'])}건 실행)"
    return line


def _gaps_section(state: dict, gaps: list[dict], hidden: int) -> str:
    """§6 한계의 근거 공백. 평평하게 나열하면 수십 줄이 되므로 관점별로 묶는다.

    §9 목차표 — 6 한계는 자료 공백을 담는다. 분량 상한으로 접은 건수는 숨기지 않고 밝힌다.
    """
    grouped: dict[str, list[str]] = {}
    for gap in gaps:
        grouped.setdefault(gap.get("role", ""), []).append(_gap_line(gap))

    validation = state.get("validation") or {}
    verdicts = validation.get("claim_verdicts") or {}
    for claim_id in validation.get("rejected_claims") or []:
        verdict = verdicts.get(claim_id) or {}
        grouped.setdefault("review", []).append(
            f"검토 부결: {claim_id} (검토자: {verdict.get('reviewer') or '검토자 미상'} — "
            f"{verdict.get('comment') or '사유 미기재'})")
    for claim_id, flag in sorted((state.get("claim_flags") or {}).items()):
        if (flag or {}).get("status") != "invalid" or claim_id in (validation.get("rejected_claims") or []):
            continue
        by = "사람 검토 부결" if flag.get("by") == "human_review" else "근거 무효(품질 평가)"
        grouped.setdefault("review", []).append(
            f"{by}: {claim_id} — {flag.get('reason') or '사유 미기재'}")

    if not grouped:
        return "- 해당 없음"

    blocks = []
    for role in list(ROLE_LABEL) + ["review"]:
        items = grouped.get(role)
        if not items:
            continue
        label = "내용 검토" if role == "review" else ROLE_LABEL[role]
        blocks.append(f"*{label}* ({len(items)}건)\n" + _bullets(items))
    if hidden:
        blocks.append(f"- 외 근거 공백 {hidden}건은 분량 상한으로 생략했다(실행 기록 State `gaps` 에 전부 남아 있다).")
    return "\n\n".join(blocks)


def _exceptions_section(exceptions: list[dict]) -> str:
    if not exceptions:
        return ""
    lines = ["", "**편향 예외 공개**", ""]
    for entry in exceptions:
        m = entry["metrics"]
        lines.append(
            f"- {entry['technology']}: 인용 근거의 출처 묶음 {m['n_groups']}개, 단일 묶음 비중 "
            f"{m['max_share']:.0%}. 사전 정의 예외 — {entry.get('reason', '')}. "
            f"조사 범위: {entry.get('scope', '미기재')}.")
    return "\n".join(lines) + "\n"


def _quality_scope(state: dict, displayed: list[str]) -> str:
    """자동 품질 평가의 범위와 한계 (계획서 §7-1 — 검사 범위 n/N 을 보고서 §6 에 적는다)."""
    lines = []
    total = len(displayed)
    if rules.judge_enabled(state):
        cells = {}
        for role in PERSPECTIVES + ["research"]:
            for claim in (state.get(role) or {}).get("claims") or []:
                if claim.get("claim_id") in displayed:
                    for key in rules.claim_cells(role, claim):
                        cells.setdefault(key, []).append(claim["claim_id"])
        size = rules.quality_config().get("judge_sample", 60)
        sampled = len(rules.stratified_sample(cells, rules.recheck_claim_ids(state), size))
        how = "전수" if sampled >= total else "관점×기술 층화 결정적 표본"
        lines.append(
            f"- 자동 품질 평가: 표시된 Claim {total}건 전부의 주장→근거→출처 연결을 코드로 검사했고, "
            f"근거가 주장을 뒷받침하는지는 LLM Judge({judge_model_name()}, 고정 프롬프트)가 "
            f"{sampled}/{total}건({how}) 판정했다. 종합 문장의 참조 범위·중립성·관점 커버리지·"
            f"선택적 근거 사용도 Judge 가 함께 판정했다.")
        lines.append("- Judge 는 생성과 같은 계열 모델이라 자기평가 편향이 남을 수 있다"
                     + (". 표본 밖 Claim 은 코드 검사만 거쳤다." if sampled < total else "."))
    else:
        lines.append(
            f"- 자동 품질 평가는 코드 기반 평가(1안)만 수행했다 — 표시된 Claim {total}건의 "
            "주장→근거→출처 연결, 종합 문장의 Claim 참조, 우열 표현, 출처 묶음 분포, 관점 커버리지, "
            "구조·분량. 근거가 주장을 의미적으로 뒷받침하는지는 자동 판정하지 않았다.")
    if any((f or {}).get("by") == "human_review" for f in (state.get("claim_flags") or {}).values()) \
            or (state.get("validation") or {}).get("reviewers"):
        lines.append("- 사람 검토(Human Review)를 거쳤으며 부결된 Claim 은 본문·종합·참고문헌에서 제외했다.")
    return "\n".join(lines)


def _partial_banner(partial: dict | None) -> str:
    if not partial:
        return ""
    failed = "; ".join(f"{CRITERION_LABEL.get(name, name)}({', '.join(reasons[:2]) or '사유 미기재'})"
                       for name, reasons in partial.get("failed", []))
    return ("\n> ⚠️ **부분 발행(partial)** — 품질 기준을 모두 충족하지 못한 상태로 발행했다. "
            f"종료 사유: {partial.get('stop_reason') or '미기재'}."
            + (f" 미달 기준: {failed}." if failed else "") + " 상세는 §6.\n")


def _partial_limits(partial: dict | None) -> str:
    if not partial:
        return ""
    lines = ["", "**품질 평가 미달 (부분 발행)**", ""]
    for name, reasons in partial.get("failed", []):
        lines.append(f"- {CRITERION_LABEL.get(name, name)}: " + ("; ".join(reasons) or "사유 미기재"))
    lines.append(f"- 종료 사유: {partial.get('stop_reason') or '미기재'}")
    return "\n".join(lines) + "\n"


SUMMARY_TOP = 3     # §9 — SUMMARY 는 PDF 반 페이지 이내다. 전체 목록은 §5·§6 에 있다.


def _top(items, section) -> str:
    """앞의 몇 건만 싣고 나머지는 해당 절을 가리킨다."""
    shown = _bullets(items[:SUMMARY_TOP])
    rest = len(items) - SUMMARY_TOP
    return shown + (f"\n- 외 {rest}건은 {section} 참고" if rest > 0 else "")


def _conflict_line(conflict) -> str:
    return f"**{conflict['perspective']}** — {conflict['why']}"


def _summary(agreements: list, conflicts: list, gaps: list) -> str:
    """LLM 없이 synthesis 의 확정 결과만 사용해 SUMMARY 를 만든다.

    §9 목차표 — SUMMARY 는 *평가 결과, 관점별 주요 차이, **중요한** 공백* 을 반 페이지
    이내로 담는다. 전부 나열하면 분량 점검에 걸려 제출본이 생성되지 않는다.
    """
    parts = []

    if agreements:
        parts.append("네 관점에서 공통적으로 확인된 사항은 다음과 같다.\n"
                     + _top([_text(a) for a in agreements], "§5.1"))

    if conflicts:
        parts.append("관점별 평가가 실제로 달라진 지점은 다음과 같다.\n"
                     + _top([_conflict_line(c) for c in conflicts], "§5.2"))

    if gaps:
        parts.append("평가 과정에서 확인된 주요 근거 공백은 다음과 같다.\n"
                     + _top(gaps, "§6"))

    if not parts:
        return "확정된 종합 평가 결과가 없다."

    return "\n\n".join(parts)


def _implications(items: list, total: int, render, claims_by_id: dict, empty: str) -> str:
    if not items:
        return empty
    lines = [f"- {render(item)}{_cites(item, claims_by_id)}" for item in items]
    if total > len(items):
        lines.append(f"- 외 {total - len(items)}건은 분량 상한으로 생략했다")
    return "\n".join(lines)


def build(state: dict, *, version: int = 1, compaction: int = 0,
          partial: dict | None = None) -> tuple[str, dict]:
    """보고서 모델 하나에서 Markdown 과 `report_manifest` 를 함께 만든다. 판단 LLM 을 부르지 않는다."""
    synthesis = state.get("synthesis") or {}
    config = state.get("run_config") or {}
    domain = config.get("domain", "데이터센터/클라우드")
    m = _model(state, compaction)
    shown = m["shown"]
    claims_by_id = {c["claim_id"]: c for items in shown.values() for c in items}

    summary = _summary(m["shown_agreements"], m["shown_conflicts"], synthesis.get("gaps") or [])
    # REFERENCE 는 표시된 Claim 의 근거에서만 만든다(§9 — 실제 인용 자료만).
    cited_state = {role: dict(state.get(role) or {}) | {"claims": shown[role]}
                   for role in ASSESSMENT_ROLES}
    references, marks = reference.build(cited_state | {"run_config": config})
    displayed_ids = list(claims_by_id)

    md = f"""# KV cache 최적화 기술 다관점 평가

**대상 기술** TurboQuant (SW 압축) · ITME (HW 메모리 확장)
**적용 도메인** {domain}
**생성 모델** {model_name()}
{_partial_banner(partial)}{_held(state)}
## SUMMARY

{summary}

## 1. 분석 배경

KV cache는 재계산 낭비를 줄이는 장치이지만, 문맥이 길어질수록 Key·Value 텐서가
토큰 수에 비례해 증가해 가속기 HBM을 소진시킨다. 연산 병목이 메모리 병목으로
이동하는 구조다.

본 보고서는 KV cache 데이터 축소 접근인 TurboQuant와 메모리 계층 확장 접근인
ITME를 대상으로, TRL·시장성·이해관계자·도메인 네 관점에서 평가한다.
두 기술의 개별 근거를 우선 검토하며, 동일 시스템에서 두 기술을 결합했을 때의
효과는 공개 실측 자료가 확인되지 않는 한 가설로 분리한다.

## 2. 기술 선정

데이터센터/클라우드 도메인을 먼저 확정한 뒤, 동일한 KV cache 메모리 병목에
서로 다른 시스템 계층에서 접근하는 기술을 각각 선정했다.

| 접근 | 기술 | 선정 이유 |
|---|---|---|
| KV cache 데이터 축소 | TurboQuant | KV cache 양자화를 통해 저장량과 메모리 사용량을 줄이는 접근 |
| 메모리 계층 확장 | ITME | CXL-Hybrid 기반 계층적 메모리 확장으로 용량과 데이터 이동 문제에 접근 |

두 기술은 상호 배타적인 대안으로 가정하지 않는다. 결합 효과는 §5.3의 가설로만
다룬다.

## 3. 기술 개요

{_claims(shown["research"], m["caps"]["chars"])}

## 4. 관점별 평가

### 4.1 TRL (기술성숙도)

{_claims(shown["maturity"], m["caps"]["chars"])}

### 4.2 시장성

{_claims(shown["market"], m["caps"]["chars"])}

### 4.3 이해관계자

{_claims(shown["stakeholder"], m["caps"]["chars"])}

### 4.4 도메인 적용 ({domain})

{_claims(shown["domain_assessment"], m["caps"]["chars"])}

## 5. 시사점

### 5.1 관점 간 일치

{_implications(m["shown_agreements"], len(m["agreements"]), _text, claims_by_id, "- 해당 없음")}

### 5.2 관점 간 차이 및 상충

{_implications(m["shown_conflicts"], len(m["conflicts"]), _conflict_line, claims_by_id,
               "- 확인된 관점 간 상충 없음")}

### 5.3 결합 가설 (추론 — 실측 근거 아님)

{synthesis.get('combination_hypothesis') or '해당 없음'}

## 6. 한계
{_partial_limits(partial)}
**확인된 근거 공백**

{_gaps_section(state, m["gaps"], m["hidden_gaps"])}

**조사 시점과 검토 범위**

- 조사 수행 시점: {config.get('started_at', '미상')}
- 근거 범위: 지정 Doc Pool 색인(papers_core·ecosystem·context)과 이번 실행에서 본문을
  확보한 웹 자료로 한정한다. 후보로만 조회한 자료는 근거로 쓰지 않는다.
{_carried(state)}{_reused()}{_exceptions_section(m["exceptions"])}
**분석의 한계**

- 본 평가는 공개된 논문·백서·사례 자료를 기반으로 하며 자체 실측 벤치마크가 아니다.
- TRL·시장성·이해관계자 평가는 공개 정보 기반 추정이므로 실제 최신 상용 배치 현황과 차이가 있을 수 있다.
- TurboQuant와 ITME의 결합 효과는 동일 시스템에서 함께 측정된 공개 자료가 확인되지 않는 한 실측 결과가 아닌 가설로 다룬다.
- GPU 벤치마크 수치는 원 논문의 보고값이며 본 프로젝트가 직접 측정한 결과가 아니다.
- 논문마다 평가 모델·하드웨어·부하 조건이 다르므로 성능 개선 배수만으로 기술 간 우열을 단정하지 않는다.
{_quality_scope(state, displayed_ids)}
- 확증 편향을 줄이기 위해 기술별 인용 근거의 출처 묶음 분포를 검사하고, 성능 향상뿐 아니라 잔여 비용과 근거 공백도 함께 기록한다.
{"- 분량 상한(10쪽)을 지키기 위해 압축 " + str(compaction) + "단계로 표시 항목을 줄였다. 생략한 Claim 은 실행 기록에 남아 있다." if compaction else ""}

{references}
"""
    md = _number_citations(md, marks)

    sections = {role: [c["claim_id"] for c in shown[role]] for role in ASSESSMENT_ROLES}
    sections["summary"] = sorted({cid for item in (m["shown_agreements"][:SUMMARY_TOP]
                                                   + m["shown_conflicts"][:SUMMARY_TOP])
                                  for cid in _refs(item)})
    sections["implications"] = sorted({cid for item in m["shown_agreements"] + m["shown_conflicts"]
                                       for cid in _refs(item)})
    manifest = ReportManifest(
        report_version=version,
        based_on_synthesis_version=int(synthesis.get("synthesis_version") or 1),
        sections=sections,
        gaps=[{"perspective": rules.gap_perspective(g), "technology": g.get("technology", "both"),
               "kind": g.get("kind", "evidence_gap"), "item": g.get("item", "")} for g in m["gaps"]],
        cited_evidence_ids=sorted({e for c in claims_by_id.values() for e in c.get("evidence_ids") or []}),
        references=reference.cited_source_ids(cited_state),
        disclosed_exceptions=[e["technology"] for e in m["exceptions"]],
        compaction=compaction,
        partial=bool(partial),
    ).model_dump()
    return md, manifest


def report(state) -> dict:
    """현재 State 를 §9 목차에 맞춰 Markdown + manifest 로 조립한다. 판단 LLM 을 부르지 않는다(§6)."""
    compaction = _next_compaction(state)
    version = int(state.get("report_version") or 0) + 1
    md, manifest = build(state, version=version, compaction=compaction)
    return {
        "report": md,
        "report_manifest": manifest,
        "report_version": version,
        "trace": [event("report", chars=len(md), report_version=version, compaction=compaction,
                        claims=sum(len(ids) for role, ids in manifest["sections"].items()
                                   if role in ASSESSMENT_ROLES),
                        deterministic=True)],
    }
