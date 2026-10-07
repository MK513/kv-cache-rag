"""품질 평가와 재작업 라우팅 — 담당 C (agent/ow-quality). 계획서 §7·§9.

계약:
- `quality_eval(state)` → {"quality_eval": schema.QualityEval dict, "claim_flags", "repair_count",
  "step_count", "stop_reason", "last_decision", "trace"}. 다음 노드는 `quality_eval["next"]` 에 정해 둔다.
- `route_after_eval(state)` → `quality_eval["next"]`. publish·human_review 로 가기 직전에만 publish
  guard 로 버전을 대조하고, 구버전이면 "report" 로 돌려 보고서부터 다시 만든다.
- `apply_review(state)` → Human Review 판정을 읽어 부결 Claim 을 `claim_flags` 에
  `{status: "invalid", by: "human_review"}` 로 기록한다. `route_after_review` 가 synthesis | publish.

평가 대상은 **현재 보고서**(`report_manifest` = 실제로 표시된 것)다. 기준마다 코드 검사를 먼저
하고, 코드 검사를 통과한 기준만 LLM Judge 를 부른다(Hybrid, Notion 3안).

| 기준 | 코드 검사 | Judge |
|---|---|---|
| groundedness_l1 | 표시 Claim 의 claim→evidence→source 연결(validate 와 같은 검사) | 근거가 주장을 뒷받침하는가 (층화 표본) |
| groundedness_l2 | 종합 항목의 claim_ids 가 있고, 표시된 유효 Claim 만 가리키는가 | 참조 범위를 벗어난 서술인가 |
| neutrality | 승자·추천·우열 표현 패턴 | 우열 판정·조건 다른 수치의 단순 비교 |
| bias | ① 출처 묶음 ≥ 2 ② 단일 묶음 ≤ 60% (기술별), 사전 정의 예외 | ③ 선택적 근거 사용 |
| coverage | 4관점 × 2기술 칸마다 유효 Claim 또는 수용 가능한 evidence_gap | 관점을 실질적으로 다루는가 |
| structure | SUMMARY 처음·REFERENCE 끝·인용–참고문헌 일치·렌더링 쪽수 | — |

문제는 고칠 수 있는 단계로 나눈다 — 근거(→ orchestrator 재계획), 종합(→ synthesis), 보고서(→ report).
라우팅 순서는 계획서 §7-2 표. 상한(`step_count`·`retry_count`·`repair_count`)은 추가 실행 **전에**
검사하고, 닿으면 partial 로 publish 한다.
"""

import re

from pydantic import BaseModel, Field

from src.agents.common import CITATION_RE, event
from src.llm import judge_llm, judge_model_name
from src.observability import log_decision
from src.orchestrator import quality_rules as rules
from src.orchestrator.versions import check_publish_guard, sha256_of
from src.output import validate
from src.schema import ASSESSMENT_ROLES, PERSPECTIVES, TECHS, QualityEval, Verdict
from src.settings import settings
from src.state import run_dir
from src.tools.web_store import save_json

CRITERIA = ["groundedness_l1", "groundedness_l2", "neutrality", "bias", "coverage", "structure"]

# 승자·추천·우열 표현 (중립성 코드 검사). 원 논문이 자기 기준선 대비 개선을 보고하는 문장은
# 기술 간 우열이 아니라서 잡지 않는다 — "기준선 대비 1.8배" 는 통과, "ITME 가 더 우수하다" 는 실패.
VERDICT_RE = re.compile(
    r"승자|우승|더 우수|우수한 기술|우월|열등|압도|능가|최선의 선택|최적의 선택|"
    r"추천(?:한다|함|된다|할 만)|권장(?:한다|함)|보다 (?:낫|뛰어나|우수)|더 (?:낫|뛰어나)|"
    r"winner|superior|best choice|recommend", re.IGNORECASE)
# "어느 쪽이 더 우수한지는 근거가 없다" 처럼 우열 판정을 **유보·부정**하는 문장은 중립 서술이다.
# 실데이터(20260922-161416 실행)의 종합 문장에서 오탐이 확인돼 문장 단위로 걸러 낸다.
WITHHELD_RE = re.compile(r"는지는|는지 |지 않|없다|없으며|없고|수 없|단정하|판정하지|미확인")

PERSPECTIVE_CRITERIA = {
    "maturity": "TRL 단계와 그 근거(실증 환경·규모·재현 여부)",
    "market": "시장 규모·상용화/채택 현황(발표와 배포 구분)·생태계 지지·비용",
    "stakeholder": "경쟁사·채택사·개발사·투자자 등 이해관계자의 입장과 발언 근거",
    "domain_assessment": "데이터센터/클라우드 적용 조건(동시성·비용·운영 복잡도)과 잔여 비용",
}


class JudgeFailure(Exception):
    """Judge 호출이 재시도 후에도 실패했다. 판정 없이 통과시키지 않고 partial 로 발행한다."""


# ── Judge 출력 형식 ──────────────────────────────────────────────────────────

class ClaimJudgment(BaseModel):
    claim_id: str
    supported: bool = Field(description="인용 구절이 주장을 직접 뒷받침하면 true")
    reason: str = ""


class L1Judgment(BaseModel):
    verdicts: list[ClaimJudgment] = Field(default_factory=list)


class ItemJudgment(BaseModel):
    index: int
    within_scope: bool = Field(description="참조 Claim 이 말하는 범위 안의 서술이면 true")
    reason: str = ""


class L2Judgment(BaseModel):
    verdicts: list[ItemJudgment] = Field(default_factory=list)


class NeutralityJudgment(BaseModel):
    neutral: bool = Field(description="기술 간 우열 판정·조건이 다른 수치의 단순 비교가 없으면 true")
    issues: list[str] = Field(default_factory=list)


class SelectivityJudgment(BaseModel):
    selective: bool = Field(description="성능 향상만 골라 싣고 같은 기술의 한계·비용 근거를 뺐으면 true")
    reason: str = ""


class CellJudgment(BaseModel):
    technology: str
    substantive: bool = Field(description="이 관점의 평가 기준을 실질적으로 다루면 true")
    reason: str = ""


class CoverageJudgment(BaseModel):
    cells: list[CellJudgment] = Field(default_factory=list)


JUDGE_RULES = """너는 기술 평가 보고서의 품질 심사자다. 아래 입력만 보고 판정한다.
- 입력에 없는 지식으로 보충하지 않는다. 판단할 근거가 부족하면 엄격하게(실패 쪽으로) 판정한다.
- 판정 사유는 한 문장으로 짧게 쓴다."""


def _judge(schema, prompt: str):
    """temperature 0·고정 프롬프트·LLM 캐시. 실패하면 `max_judge_retries` 만큼 다시 부른다."""
    tries = 1 + int((settings().get("orchestrator") or {}).get("max_judge_retries", 1))
    last = None
    for _ in range(tries):
        try:
            return judge_llm().with_structured_output(schema).invoke(f"{JUDGE_RULES}\n\n{prompt}")
        except Exception as exc:          # 네트워크·파싱 오류 모두 같은 처리 — 재시도 후 실패
            last = exc
    raise JudgeFailure(f"{schema.__name__}: {type(last).__name__}: {last}")


def _judge_all(schema, prompt_for, keys: list, key_of, items_of) -> dict:
    """요청한 항목마다 판정을 받는다. 응답에서 빠진 항목은 그것만 한 번 다시 묻는다.

    구조화 출력은 긴 목록에서 일부 항목을 빠뜨리곤 한다. 빠진 항목을 통과로 세면 판정하지 않은
    것을 판정했다고 보고하게 된다. 다시 물어도 빠지면 JudgeFailure — 판정 없이 통과시키지 않는다.
    """
    judged: dict = {}
    missing = list(keys)
    for _ in range(2):
        result = _judge(schema, prompt_for(missing))
        for verdict in items_of(result):
            key = key_of(verdict)
            if key in missing and key not in judged:
                judged[key] = verdict
        missing = [k for k in keys if k not in judged]
        if not missing:
            return judged
    raise JudgeFailure(f"{schema.__name__}: 응답 누락 {len(missing)}건 — {', '.join(map(str, missing[:5]))}")


# ── 평가 문맥 ────────────────────────────────────────────────────────────────

class Context:
    """평가에 필요한 조회표. 표시 여부는 manifest 가 기준이다."""

    def __init__(self, state: dict):
        self.state = state
        self.manifest = state.get("report_manifest") or {}
        self.excluded = rules.excluded_claim_ids(state)
        sections = self.manifest.get("sections") or {}
        self.claims: dict[str, tuple[str, dict]] = {}       # claim_id → (역할, Claim) — 전체
        for role in ASSESSMENT_ROLES:
            for claim in (state.get(role) or {}).get("claims") or []:
                self.claims.setdefault(claim.get("claim_id"), (role, claim))
        self.shown: dict[str, list[str]] = {role: list(sections.get(role) or [])
                                            for role in ASSESSMENT_ROLES}
        self.displayed = [cid for role in ASSESSMENT_ROLES for cid in self.shown[role]]
        self.evidence = dict(state.get("evidence_registry") or {})
        self.sources = dict(state.get("source_registry") or {})
        for role in ASSESSMENT_ROLES:
            assessment = state.get(role) or {}
            for item in assessment.get("evidence") or []:
                self.evidence.setdefault(item.get("evidence_id"), item)
            for item in assessment.get("sources") or []:
                self.sources.setdefault(item.get("source_id"), item)

    def claim(self, claim_id: str) -> dict:
        return (self.claims.get(claim_id) or ("", {}))[1]

    def displayed_claims(self) -> list[dict]:
        return [self.claim(cid) for cid in self.displayed if self.claim(cid)]

    def valid_claims(self, role: str | None = None) -> list[dict]:
        """표시 여부와 관계없이 수집된 유효 Claim (무효 판정 제외)."""
        return [claim for cid, (r, claim) in self.claims.items()
                if cid not in self.excluded and (role is None or r == role)]

    def cells(self) -> dict[str, list[str]]:
        """표시 Claim 의 관점×기술 층. research 는 'research:tech' 층으로 둔다."""
        out: dict[str, list[str]] = {}
        for role in ASSESSMENT_ROLES:
            for cid in self.shown[role]:
                claim = self.claim(cid)
                if claim:
                    for key in rules.claim_cells(role, claim):
                        out.setdefault(key, []).append(cid)
        return out


class Findings:
    """기준별 판정과, 고칠 단계(근거·종합·보고서)별 문제 목록."""

    def __init__(self):
        self.verdicts: dict[str, dict] = {name: {"status": "pass", "reasons": [], "target_cells": []}
                                          for name in CRITERIA}
        self.problems = {"evidence": [], "synthesis": [], "report": []}
        self.flags: dict[str, dict] = {}

    def fail(self, criterion: str, stage: str, reason: str, cells=()):
        verdict = self.verdicts[criterion]
        verdict["status"] = "fail"
        verdict["reasons"].append(reason)
        for cell in cells:
            if cell not in verdict["target_cells"]:
                verdict["target_cells"].append(cell)
        self.problems[stage].append(f"{criterion}: {reason}")

    def mark(self, criterion: str, status: str, reason: str = ""):
        if self.verdicts[criterion]["status"] == "pass":
            self.verdicts[criterion]["status"] = status
        if reason:
            self.verdicts[criterion]["reasons"].append(reason)

    def ok(self, criterion: str) -> bool:
        return self.verdicts[criterion]["status"] != "fail"


def _invalidate(findings: Findings, state: dict, role: str, claim: dict, reason: str):
    """L1 탈락 Claim 을 claim_flags 에 기록하고 고칠 단계를 고른다.

    관점 Claim 은 그 칸을 재계획한다. research 는 Worker 가 아니라 재계획할 수 없으므로 보고서를
    다시 만들어 그 Claim 을 빼는 것으로 처리한다.
    """
    cid = claim.get("claim_id")
    findings.flags[cid] = {"status": "invalid", "reason": reason, "by": "quality_eval",
                           "at_report_version": state.get("report_version")}
    if role in PERSPECTIVES:
        findings.fail("groundedness_l1", "evidence", f"{cid}: {reason}", rules.claim_cells(role, claim))
    else:
        findings.fail("groundedness_l1", "report", f"{cid}: {reason}")


# ── 코드 검사 ────────────────────────────────────────────────────────────────

def _code_l1(ctx: Context, findings: Findings):
    run_id = ctx.state.get("run_id")
    for role in ASSESSMENT_ROLES:
        assessment = ctx.state.get(role) or {}
        for cid in ctx.shown[role]:
            found_role, claim = ctx.claims.get(cid) or ("", {})
            if not claim or found_role != role:
                findings.fail("groundedness_l1", "report", f"{cid}: manifest 에 있으나 {role} 에 없다")
                continue
            if cid in ctx.excluded:
                findings.fail("groundedness_l1", "report", f"{cid}: 무효 Claim 이 보고서에 표시됐다")
                continue
            errors = validate._structured_errors(
                role, {"claims": [claim], "evidence": assessment.get("evidence") or [],
                       "sources": assessment.get("sources") or []}, run_id)
            empty = [e for e in claim.get("evidence_ids") or []
                     if not (ctx.evidence.get(e) or {}).get("quote")]
            if errors or empty:
                kinds = sorted({e["kind"] for e in errors} | ({"인용 구절 없음"} if empty else set()))
                _invalidate(findings, ctx.state, role, claim, "연결 실패 — " + ", ".join(kinds))


def _synthesis_items(state: dict, excluded: set[str]) -> list[dict]:
    """보고서에 실릴 수 있는 종합 항목. 무효 Claim 에만 기댄 항목은 report 가 싣지 않는다."""
    synthesis = state.get("synthesis") or {}
    items = []
    for kind in ("agreements", "conflicts"):
        for item in synthesis.get(kind) or []:
            if isinstance(item, str):
                item = {"text": item, "claim_ids": []}
            refs = list(item.get("claim_ids") or [])
            if refs and all(cid in excluded for cid in refs):
                continue
            text = item.get("text") or f"{item.get('perspective', '')} — {item.get('why', '')}"
            items.append({"kind": kind, "text": text,
                          "claim_ids": [cid for cid in refs if cid not in excluded]})
    return items


def _code_l2(ctx: Context, findings: Findings):
    displayed = set(ctx.displayed)
    for item in _synthesis_items(ctx.state, ctx.excluded):
        if not item["claim_ids"]:
            findings.fail("groundedness_l2", "synthesis", f"근거 Claim 이 없는 종합 문장: {item['text'][:60]}")
    sections = ctx.manifest.get("sections") or {}
    for key in ("summary", "implications"):
        for cid in sections.get(key) or []:
            if cid in ctx.excluded or cid not in ctx.claims:
                findings.fail("groundedness_l2", "synthesis", f"{key} 이 무효·미상 Claim 을 가리킨다: {cid}")
            elif cid not in displayed:
                findings.fail("groundedness_l2", "report", f"{key} 이 표시되지 않은 Claim 을 가리킨다: {cid}")


def _section(markdown: str, start: str, end: str) -> str:
    i = markdown.find(start)
    if i < 0:
        return ""
    j = markdown.find(end, i + len(start))
    return markdown[i:j if j >= 0 else len(markdown)]


def _verdict_text(markdown: str) -> str:
    """중립성 검사 범위 — SUMMARY 와 §5 시사점(관점별 비교 문장 포함)."""
    return (_section(markdown, "## SUMMARY", "## 1.")
            + _section(markdown, "## 5. 시사점", "## 6."))


def _verdict_phrases(text: str) -> list[str]:
    """우열·추천 표현. 같은 문장에 유보·부정 표현이 있으면 판정이 아니라서 뺀다."""
    hits = set()
    for sentence in re.split(r"(?<=[.!?。])\s+|\n", text):
        found = VERDICT_RE.findall(sentence)
        if found and not WITHHELD_RE.search(sentence):
            hits.update(found)
    return sorted(hits)


def _code_neutrality(ctx: Context, findings: Findings):
    hits = _verdict_phrases(_verdict_text(ctx.state.get("report") or ""))
    if hits:
        findings.fail("neutrality", "synthesis", "우열·추천 표현: " + ", ".join(hits))


def _code_bias(ctx: Context, findings: Findings) -> dict:
    """편향 ①② — 원인에 따라 고칠 단계를 나눈다.

    보고서는 칸마다 일부 Claim 만 싣는다. 표시된 Claim 만 한 묶음에 몰렸고 **수집한 유효 Claim
    전체로는 기준을 통과**하면 고르는 쪽(report)의 문제다 — 재조사로 보내면 표시 결과가 그대로라
    재계획 라운드만 쓰고 partial 로 끝난다. 수집 근거 전체로도 실패할 때만 재조사(orchestrator).
    """
    metrics = rules.bias_metrics(ctx.displayed_claims(), ctx.evidence, ctx.sources)
    collected = rules.bias_metrics(ctx.valid_claims(), ctx.evidence, ctx.sources)
    disclosed = set(ctx.manifest.get("disclosed_exceptions") or [])
    for tech in TECHS:
        m = metrics[tech]
        if not m["cited"] or (m["ok_groups"] and m["ok_share"]):
            continue                         # 인용 0건은 커버리지가 잡는다
        why = (f"{tech} 인용 근거 출처 묶음 {m['n_groups']}개, 단일 묶음 비중 {m['max_share']:.0%}")
        c = collected[tech]
        if c["ok_groups"] and c["ok_share"]:
            findings.fail("bias", "report",
                          f"{why} — 수집 근거 전체는 묶음 {c['n_groups']}개·비중 {c['max_share']:.0%}로 "
                          "통과, 표시 선택이 한 묶음에 몰렸다")
            continue
        if rules.bias_exception(tech):
            if tech in disclosed:
                findings.mark("bias", "accepted_exception", f"{why} — 사전 정의 예외 공개됨")
            else:
                findings.fail("bias", "report", f"{why} — 예외 사유가 보고서에 공개되지 않았다")
            continue
        dominant = max(c["groups"], key=c["groups"].get)
        cells = []
        for perspective in PERSPECTIVES:
            groups = {rules.source_group(ctx.sources.get((ctx.evidence.get(e) or {}).get("source_id")) or {})
                      for claim in ctx.valid_claims(perspective)
                      if tech in rules.techs_of(claim.get("technology", "both"))
                      for e in claim.get("evidence_ids") or []}
            if groups <= {dominant}:
                cells.append(rules.cell(perspective, tech))
        findings.fail("bias", "evidence", why, cells)
    return metrics


def _code_coverage(ctx: Context, findings: Findings) -> list[str]:
    """칸마다 유효 Claim 또는 수용 가능한 evidence_gap. 유효 Claim 으로 채운 칸 목록을 돌려준다."""
    state_gaps = state_gaps_by_cell(ctx.state)
    shown_gaps = {(g.get("perspective"), g.get("item"))
                  for g in ctx.manifest.get("gaps") or [] if g.get("kind") == "evidence_gap"}
    by_claims = []
    for perspective in PERSPECTIVES:
        for tech in TECHS:
            key = rules.cell(perspective, tech)
            shown = [cid for cid in ctx.shown[perspective]
                     if cid not in ctx.excluded and tech in rules.techs_of(ctx.claim(cid).get("technology", "both"))]
            if shown:
                by_claims.append(key)
                continue
            gaps = state_gaps.get(key, [])
            accepted = [g for g in gaps if rules.acceptable_gap(g)]
            if any((perspective, g.get("item")) in shown_gaps for g in accepted):
                continue
            hidden_valid = [c for c in (ctx.state.get(perspective) or {}).get("claims") or []
                            if c.get("claim_id") not in ctx.excluded
                            and tech in rules.techs_of(c.get("technology", "both"))]
            if hidden_valid or accepted:
                findings.fail("coverage", "report", f"{key}: 근거가 있으나 보고서에 표시되지 않았다")
                continue
            kinds = sorted({g.get("kind", "evidence_gap") for g in gaps}) or ["근거 부족"]
            findings.fail("coverage", "evidence", f"{key}: {', '.join(kinds)}", [key])
    return by_claims


def state_gaps_by_cell(state: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for gap in state.get("gaps") or []:
        perspective = rules.gap_perspective(gap)
        if perspective in PERSPECTIVES:
            for tech in rules.techs_of(gap.get("technology", "both")):
                out.setdefault(rules.cell(perspective, tech), []).append(gap)
    return out


def _code_structure(ctx: Context, findings: Findings, pages: int | None, render_note: str):
    markdown = ctx.state.get("report") or ""
    headings = re.findall(r"^## (.+)$", markdown, re.MULTILINE)
    if not headings or headings[0].strip() != "SUMMARY":
        findings.fail("structure", "report", "SUMMARY 가 맨 앞 절이 아니다")
    if not headings or headings[-1].strip() != "REFERENCE":
        findings.fail("structure", "report", "REFERENCE 가 맨 끝 절이 아니다")
    raw = sorted(set(CITATION_RE.findall(markdown)))
    if raw:
        findings.fail("structure", "report", f"참고문헌 번호로 바뀌지 않은 인용 {len(raw)}건")
    cited_sources = sorted({(ctx.evidence.get(e) or {}).get("source_id") for c in ctx.displayed_claims()
                            for e in c.get("evidence_ids") or []} - {None})
    if sorted(ctx.manifest.get("references") or []) != cited_sources:
        findings.fail("structure", "report", "인용 근거의 출처와 REFERENCE 목록이 다르다")
    limit = int((settings().get("report") or {}).get("max_pages") or 10)
    if pages is None:
        findings.mark("structure", "pass", f"렌더링 쪽수 미측정 — {render_note}")
    elif pages > limit:
        findings.fail("structure", "report", f"pages: {pages}쪽 > {limit}쪽")


# ── Judge ────────────────────────────────────────────────────────────────────

def _evidence_block(ctx: Context, claim: dict) -> str:
    lines = []
    for evidence_id in claim.get("evidence_ids") or []:
        item = ctx.evidence.get(evidence_id) or {}
        source = ctx.sources.get(item.get("source_id")) or {}
        lines.append(f"  - 근거 {evidence_id} ({source.get('title') or item.get('source_id', '')}, "
                     f"{item.get('location', '')}): {(item.get('quote') or '')[:600]}")
    return "\n".join(lines)


def _judge_l1(ctx: Context, findings: Findings) -> dict:
    size = int(rules.quality_config().get("judge_sample", 60))
    strata = ctx.cells()
    sample = rules.stratified_sample(strata, rules.recheck_claim_ids(ctx.state), size)
    batches: dict[str, list[str]] = {}
    for cid in sample:
        role = ctx.claims[cid][0]
        batches.setdefault(role, []).append(cid)
    judged_count = 0
    for role, ids in batches.items():
        def prompt(subset):
            body = "\n".join(f"- Claim {cid} [{ctx.claim(cid).get('kind')}]: {ctx.claim(cid).get('text')}\n"
                             f"{_evidence_block(ctx, ctx.claim(cid))}" for cid in subset)
            return ("각 Claim 이 함께 적힌 인용 구절로 뒷받침되는지 판정한다. 수치·단위·비교 기준선·"
                    "실험 조건이 구절과 다르면 뒷받침되지 않는다. 추론·가설 Claim 은 전제가 구절에 "
                    f"있는지만 본다. verdicts 에 Claim 마다 한 건씩, claim_id 를 그대로 적는다.\n\n{body}")
        judged = _judge_all(L1Judgment, prompt, ids, lambda v: v.claim_id.strip(), lambda r: r.verdicts)
        judged_count += len(judged)
        for cid in ids:
            verdict = judged[cid]
            if not verdict.supported:
                _invalidate(findings, ctx.state, role, ctx.claim(cid),
                            f"Judge: 근거가 주장을 뒷받침하지 않음 — {verdict.reason}")
    total = len(dict.fromkeys(cid for ids in strata.values() for cid in ids))
    # 보낸 수가 아니라 실제로 판정을 받은 수를 기록한다(보고서 §6 의 n/N 근거).
    return {"l1": f"{judged_count}/{total}",
            "strata": {key: sum(1 for cid in ids if cid in sample) for key, ids in sorted(strata.items())},
            "recheck": sorted(rules.recheck_claim_ids(ctx.state) & set(sample))}


def _judge_l2(ctx: Context, findings: Findings):
    items = _synthesis_items(ctx.state, ctx.excluded)
    if not items:
        return
    def prompt(indices):
        body = "\n".join(
            f"[{i}] {items[i]['text']}\n" + "\n".join(f"  - 참조 {cid}: {ctx.claim(cid).get('text', '')}"
                                                   for cid in items[i]["claim_ids"])
            for i in indices)
        return ("각 종합 문장이 참조 Claim 이 말하는 범위 안에서만 서술하는지 판정한다. 참조에 없는 "
                "수치·사례·인과를 덧붙였으면 범위를 벗어난 것이다. verdicts 에 문장마다 한 건씩, "
                "대괄호 번호를 index 로 적는다.\n\n" + body)
    judged = _judge_all(L2Judgment, prompt, list(range(len(items))), lambda v: v.index, lambda r: r.verdicts)
    for i, verdict in sorted(judged.items()):
        if not verdict.within_scope:
            findings.fail("groundedness_l2", "synthesis",
                          f"참조 범위 밖 서술: {items[i]['text'][:60]} — {verdict.reason}")


def _judge_neutrality(ctx: Context, findings: Findings):
    text = _verdict_text(ctx.state.get("report") or "")
    if not text:
        return
    result = _judge(NeutralityJudgment, "다음 보고서 발췌(SUMMARY·시사점)에 두 기술의 우열 판정, 추천, "
                                        "실험 조건이 다른 수치의 단순 비교가 있는지 판정한다.\n\n" + text)
    if not result.neutral:
        findings.fail("neutrality", "synthesis", "Judge: " + ("; ".join(result.issues) or "우열 서술"))


def _judge_selectivity(ctx: Context, findings: Findings):
    displayed = set(ctx.displayed)
    for tech in TECHS:
        def covers(claim, tech=tech):
            return tech in rules.techs_of(claim.get("technology", "both"))
        shown = [c for c in ctx.displayed_claims() if covers(c)]
        hidden_limits = [claim for cid, (_, claim) in ctx.claims.items()
                         if cid not in displayed and cid not in ctx.excluded
                         and covers(claim) and rules.is_limit_claim(claim)]
        if not shown:
            continue
        body = ("# 보고서에 실은 Claim\n" + "\n".join(f"- {c.get('text')}" for c in shown)
                + "\n\n# 보고서에 싣지 않은 같은 기술의 한계·비용 Claim\n"
                + ("\n".join(f"- {c.get('text')}" for c in hidden_limits) or "(없음)"))
        result = _judge(SelectivityJudgment,
                        f"{tech} 에 대해 보고서가 성능 향상만 골라 싣고 한계·비용 근거를 빠뜨렸는지 "
                        f"판정한다.\n\n{body}")
        if result.selective:
            # 어떤 Claim 을 싣는지는 report 의 _select 가 정한다. synthesis 로 보내면 표시가 바뀌지 않아
            # 같은 판정이 반복되고 max_repairs 로 끝났다(20261007 실행). 숨은 한계·비용 Claim 이 있으면
            # report 가 싣게 하고, 이미 다 실었으면 근거가 모자란 것이라 재조사한다.
            if hidden_limits:
                findings.fail("bias", "report", f"③ {tech} 선택적 근거 사용 — {result.reason}")
            else:
                findings.fail("bias", "evidence", f"③ {tech} 한계·비용 근거 미수집 — {result.reason}",
                              [rules.cell("market", tech), rules.cell("domain_assessment", tech)])


# 모델이 "turboquant" 처럼 대소문자를 바꿔 돌려줘도 같은 기술로 대조한다.
CANONICAL_TECH = {t.lower(): t for t in TECHS}


def _judge_coverage(ctx: Context, findings: Findings, by_claims: list[str]):
    for perspective in PERSPECTIVES:
        techs = [t for t in TECHS if rules.cell(perspective, t) in by_claims]
        if not techs:
            continue
        body = "\n".join(f"- [{ctx.claim(cid).get('technology')}] {ctx.claim(cid).get('text')}"
                         for cid in ctx.shown[perspective])

        def prompt(subset, perspective=perspective, body=body):
            return (f"관점 '{perspective}' 의 평가 기준은 {PERSPECTIVE_CRITERIA[perspective]} 이다. "
                    f"기술 {', '.join(subset)} 각각에 대해 아래 본문이 이 기준을 실질적으로 다루는지 "
                    f"판정한다(cells 에 기술마다 한 건, technology 는 기술 이름 그대로).\n\n{body}")
        judged = _judge_all(CoverageJudgment, prompt, techs,
                            lambda v: CANONICAL_TECH.get(v.technology.strip().lower(), v.technology),
                            lambda r: r.cells)
        for tech in techs:
            if not judged[tech].substantive:
                key = rules.cell(perspective, tech)
                findings.fail("coverage", "evidence", f"{key}: Judge — {judged[tech].reason}", [key])


# ── 렌더링 ───────────────────────────────────────────────────────────────────

def _render(state: dict, version: int) -> tuple[str, str, int | None, str]:
    """현재 보고서를 버전 파일로 렌더링하고 쪽수를 잰다. PDF 의존성이 없으면 미측정으로 둔다."""
    from src.output import pdf

    directory = run_dir(state)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / f"report-v{version}.md"
    pdf_path = directory / f"report-v{version}.pdf"
    markdown_path.write_text(state.get("report") or "", encoding="utf-8")
    try:
        pdf.render_pdf(markdown_path, pdf_path)
    except Exception as exc:
        return "", "", None, f"{type(exc).__name__}: {exc}"[:200]
    return str(pdf_path), sha256_of(pdf_path), pdf.count_pages(pdf_path), ""


# ── 노드 ─────────────────────────────────────────────────────────────────────

PUBLISH_SIDE = ("publish", "human_review")


def _limits() -> dict:
    orch = settings().get("orchestrator") or {}
    return {"max_retry": int(orch.get("max_retry", 2)), "max_repairs": int(orch.get("max_repairs", 2)),
            "max_steps": int(orch.get("max_steps", 12)),
            "max_guard_retries": int(orch.get("max_guard_retries", 2))}


def _guard(state: dict, quality: dict, next_node: str, stop_reason: str) -> tuple[str, str, int]:
    """발행 쪽으로 가기 전에 이번 평가 결과로 publish guard 를 본다. → (next, stop_reason, guard_retry_count)

    구버전이면 보고서부터 다시 만든다. 조건부 엣지는 State 를 쓸 수 없어 횟수는 여기서 센다.
    상한에 닿으면 publish 로 보내고, publish 가 같은 guard 로 제출본 없이 failed 로 끝낸다.
    """
    count = int(state.get("guard_retry_count") or 0)
    if next_node not in PUBLISH_SIDE:
        return next_node, stop_reason, count
    stale = check_publish_guard(state | {"quality_eval": quality})
    if not stale:
        return next_node, stop_reason, count
    limit = _limits()["max_guard_retries"]
    if count < limit:
        return "report", stop_reason, count + 1
    return "publish", f"publish guard 구버전({', '.join(stale)}) — 보고서 재생성 {limit}회 소진", count


def _route(state: dict, findings: Findings, judge_error: str, step: int) -> tuple[str, str, bool]:
    """계획서 §7-2 순서대로 다음 노드를 정한다. → (next, stop_reason, repair)"""
    limits = _limits()
    problems = findings.problems
    if judge_error:
        return "publish", f"Judge 실패 — {judge_error}", False
    if not any(problems.values()):
        human = (state.get("run_config") or {}).get("human_review")
        return ("human_review" if human else "publish"), "", False
    if step >= limits["max_steps"]:
        return "publish", f"max_steps({limits['max_steps']}) 도달", False
    if problems["evidence"]:
        if int(state.get("retry_count") or 0) >= limits["max_retry"]:
            return "publish", f"MAX_RETRY({limits['max_retry']}) 도달 — 근거 문제 미해결", False
        return "orchestrator", "", False
    repairs = int(state.get("repair_count") or 0)
    if repairs >= limits["max_repairs"]:
        return "publish", f"max_repairs({limits['max_repairs']}) 도달", False
    return ("synthesis" if problems["synthesis"] else "report"), "", True


def quality_eval(state) -> dict:
    """현재 보고서를 6기준으로 평가하고 다음 노드를 정한다(계획서 §7)."""
    version = int(state.get("report_version") or 1)
    step = int(state.get("step_count") or 0) + 1
    ctx = Context(state)
    findings = Findings()

    pdf_path, pdf_sha, pages, render_note = _render(state, version)

    _code_l1(ctx, findings)
    _code_l2(ctx, findings)
    _code_neutrality(ctx, findings)
    metrics = _code_bias(ctx, findings)
    by_claims = _code_coverage(ctx, findings)
    _code_structure(ctx, findings, pages, render_note)

    judge = rules.judge_enabled(state)
    scope: dict = {"judge": judge, "displayed_claims": len(ctx.displayed), "pages": pages}
    judge_error = ""
    if judge:
        scope["judge_model"] = judge_model_name()
        # L1 에서 탈락한 Claim 이 섞이면 L2·커버리지 판정이 흔들린다. 코드 검사를 통과한 기준만 본다.
        steps = [("groundedness_l1", lambda: scope.update(_judge_l1(ctx, findings))),
                 ("groundedness_l2", lambda: _judge_l2(ctx, findings)),
                 ("neutrality", lambda: _judge_neutrality(ctx, findings)),
                 ("bias", lambda: _judge_selectivity(ctx, findings)),
                 ("coverage", lambda: _judge_coverage(ctx, findings, by_claims))]
        for criterion, run in steps:
            # ③ 은 ①② 예외와 무관하게 매번 검사한다. 나머지는 코드 검사 통과 기준만.
            if criterion != "bias" and not findings.ok(criterion):
                continue
            try:
                run()
            except JudgeFailure as exc:
                judge_error = str(exc)
                findings.mark(criterion, "skipped", f"Judge 실패: {exc}")
                break
    else:
        scope["l1"] = f"0/{len(ctx.displayed)} (코드 기반 평가 — 1안)"

    next_node, stop_reason, repair = _route(state, findings, judge_error, step)
    passed = not any(findings.problems.values()) and not judge_error
    quality = QualityEval(
        passed=passed, next=next_node,
        verdicts={name: Verdict(**v) for name, v in findings.verdicts.items()},
        evaluated_report_version=version, pdf_path=pdf_path, pdf_sha256=pdf_sha, scope=scope,
    ).model_dump()
    next_node, stop_reason, guard_count = _guard(state, quality, next_node, stop_reason)
    quality["next"] = next_node

    flags = dict(state.get("claim_flags") or {}) | findings.flags
    repair_count = int(state.get("repair_count") or 0) + (1 if repair else 0)
    save_json(run_dir(state) / f"quality-v{version}.json",
              quality | {"problems": findings.problems, "bias_metrics": metrics,
                         "new_flags": findings.flags, "stop_reason": stop_reason})
    failed = [name for name, v in findings.verdicts.items() if v["status"] == "fail"]
    guarded = guard_count > int(state.get("guard_retry_count") or 0)
    reason = (f"publish guard 구버전 — 보고서 재생성 {guard_count}회째" if guarded else
              stop_reason if stop_reason.startswith("publish guard") else
              "모든 기준 통과" if passed else
              stop_reason or "; ".join(findings.problems["evidence"] + findings.problems["synthesis"]
                                       + findings.problems["report"])[:500])
    decision = log_decision(state, "quality_eval", next_node, reason, report_version=version,
                            failed=failed, passed=passed, round=state.get("retry_count", 0),
                            target_cells=sorted({c for v in findings.verdicts.values()
                                                 for c in v["target_cells"]}),
                            invalidated=sorted(findings.flags), scope=scope,
                            guard_retry_count=guard_count)
    update = {"quality_eval": quality, "claim_flags": flags, "repair_count": repair_count,
              "step_count": step, "guard_retry_count": guard_count, "last_decision": decision,
              "trace": [event("quality_eval", "ok" if passed else "failed", next=next_node,
                              report_version=version, failed=failed, pages=pages,
                              invalidated=len(findings.flags))]}
    if stop_reason:
        update["stop_reason"] = stop_reason
    return update


def route_after_eval(state) -> str:
    """그래프 조건부 엣지. 판단은 quality_eval 이 했고 여기서는 결과를 읽는다.

    발행 쪽(publish·human_review)으로 갈 때만 publish guard 를 다시 본다. 종합만 바뀌어 보고서·평가가
    구버전이면 보고서부터 다시 만든다(report → quality_eval). 재생성 횟수는 quality_eval 이 세며
    (`guard_retry_count`), 상한에 닿으면 publish 로 보내 publish 가 failed 로 끝낸다 — 같은 구버전
    판정으로 보고서를 끝없이 다시 만들지 않는다. 같은 버전 파일 손상은 예외다.
    """
    nxt = state["quality_eval"]["next"]
    if nxt in PUBLISH_SIDE and check_publish_guard(state) and \
            int(state.get("guard_retry_count") or 0) < _limits()["max_guard_retries"]:
        return "report"
    return nxt


# ── Human Review (선택, 계획서 §9) ───────────────────────────────────────────

def apply_review(state) -> dict:
    """사람이 채운 worksheet 판정을 읽어 부결 Claim 을 claim_flags 에 기록한다.

    worksheet 는 `review.prepare_worksheet` 가 human_review 에서 멈추기 전에 만든다. 없으면 여기서
    만들고 판정 없음으로 처리한다. 빈 칸은 미판정이며 승인으로 세지 않는다 — 결정 로그에 남긴다.
    부결이 새로 생기면 synthesis 로(그 Claim 은 입력에서 빠진다), 없으면 publish.
    """
    from src.output import review

    path, _ = review.prepare_worksheet(state)
    verdicts, promoted = review.load_review_verdicts(state, path)
    flags = dict(state.get("claim_flags") or {})
    displayed = {cid for role in ASSESSMENT_ROLES
                 for cid in ((state.get("report_manifest") or {}).get("sections") or {}).get(role) or []}
    rejected = []
    for cid, verdict in sorted(verdicts.items()):
        if verdict["verdict"] != "부결" or (flags.get(cid) or {}).get("status") == "invalid":
            continue
        flags[cid] = {"status": "invalid", "by": "human_review",
                      "reason": verdict.get("comment") or "사람 검토 부결",
                      "reviewer": verdict.get("reviewer", ""),
                      "at_report_version": state.get("report_version")}
        rejected.append(cid)
    pending = sorted(displayed - set(verdicts))
    nxt = "synthesis" if rejected else "publish"
    decision = log_decision(state, "apply_review", nxt,
                            f"부결 {len(rejected)}건, 미판정 {len(pending)}건",
                            rejected=rejected, pending=pending, worksheet=str(path),
                            promoted=promoted)
    return {"claim_flags": flags, "last_decision": decision,
            "trace": [event("apply_review", next=nxt, rejected=len(rejected), pending=len(pending))]}


def route_after_review(state) -> str:
    decision = (state.get("last_decision") or {}).get("decision")
    return decision if decision in ("synthesis", "publish") else "publish"

