"""품질 평가·재작업 라우팅 — 담당 C (계획서 §7, §10 핵심 6·8·10·11 의 C 부분)

LLM Judge 는 기본으로 끄고(코드 기반 평가) Judge 경로는 대역 모델로 따로 본다.
"""

import json

import pytest

from src.orchestrator import evaluator, quality_rules
from src.orchestrator.versions import PublishGuardError, check_publish_guard
from src.output import pdf
from tests.quality_fixtures import collected, fake_renderer, reported

MARKET_TQ = "r0-market-both:claim_market_turboquant_contract"
STAKE_ITME = "r1-stakeholder-both:claim_stakeholder_itme_contract"


@pytest.fixture(autouse=True)
def renderer(monkeypatch):
    render = fake_renderer(lambda text: 6)
    monkeypatch.setattr(pdf, "render_pdf", render)
    return render


@pytest.fixture
def state(tmp_path):
    return reported(collected(tmp_path))


def evaluate(state):
    return state | evaluator.quality_eval(state)


def test_clean_report_passes_and_records_the_decision(state, tmp_path):
    out = evaluate(state)
    quality = out["quality_eval"]

    assert quality["passed"] and quality["next"] == "publish"
    assert quality["evaluated_report_version"] == 1 and quality["pdf_sha256"]
    assert quality["verdicts"]["coverage"]["status"] == "pass"    # market:ITME 는 조사 이력 있는 evidence_gap
    assert quality["scope"]["pages"] == 6
    assert out["step_count"] == state["step_count"] + 1 and out["repair_count"] == 0
    directory = tmp_path / state["run_id"]
    assert json.loads((directory / "quality-v1.json").read_text())["passed"] is True
    line = json.loads((directory / "decisions.jsonl").read_text().splitlines()[-1])
    assert line["node"] == "quality_eval" and line["decision"] == "publish"
    assert evaluator.route_after_eval(out) == "publish"


def test_human_review_is_optional(state):
    state["run_config"]["human_review"] = True
    assert evaluate(state)["quality_eval"]["next"] == "human_review"


def test_execution_gap_only_cell_does_not_pass(state):
    """핵심 6 — 실행 실패 Gap 만 있는 필수 칸은 통과 근거가 되지 못한다."""
    state["stakeholder"]["claims"] = [c for c in state["stakeholder"]["claims"]
                                      if c["claim_id"] != STAKE_ITME]
    state["gaps"].append({"role": "stakeholder", "technology": "ITME", "item": "stakeholder 조사",
                          "reason": "TimeoutError", "kind": "execution_gap",
                          "attempted_queries": ["ITME 투자"]})
    out = evaluate(reported(state))
    coverage = out["quality_eval"]["verdicts"]["coverage"]

    assert not out["quality_eval"]["passed"]
    assert coverage["target_cells"] == ["stakeholder:ITME"]
    assert out["quality_eval"]["next"] == "orchestrator"


def test_gap_without_attempted_queries_is_not_accepted(state):
    """focus 를 옮겨 적은 공백(조사 이력 없음)은 커버리지로 인정하지 않는다."""
    state["gaps"][0]["attempted_queries"] = []
    state["market"]["gaps"][0]["attempted_queries"] = []
    out = evaluate(reported(state))

    assert "market:ITME" in out["quality_eval"]["verdicts"]["coverage"]["target_cells"]


def test_broken_link_invalidates_the_claim_and_targets_its_cell(state):
    """핵심 8 (C 부분) — L1 탈락 Claim 은 claim_flags invalid, 그 칸만 재계획 대상."""
    state["market"]["claims"][0]["evidence_ids"] = ["ffffffffffff"]
    out = evaluate(state)
    l1 = out["quality_eval"]["verdicts"]["groundedness_l1"]

    assert out["claim_flags"][MARKET_TQ]["status"] == "invalid"
    assert out["claim_flags"][MARKET_TQ]["by"] == "quality_eval"
    assert l1["target_cells"] == ["market:TurboQuant"]
    assert out["quality_eval"]["next"] == "orchestrator"


def test_retry_limit_publishes_partial_without_the_invalid_claim(state, tmp_path):
    """핵심 8·11 — 재계획 예산이 없으면 partial 로 발행하고 무효 Claim 은 보고서에 없다."""
    state["market"]["claims"][0]["evidence_ids"] = ["ffffffffffff"]
    state["retry_count"] = 2
    out = evaluate(state)

    assert out["quality_eval"]["next"] == "publish" and "MAX_RETRY" in out["stop_reason"]
    published = out | pdf.publish(out)
    body = (tmp_path / state["run_id"] / "report.md").read_text(encoding="utf-8")

    assert published["run_status"] == "partial"
    market = body[body.index("### 4.2 시장성"):body.index("### 4.3")]
    assert "[" not in market and "승인된 주장 없음" in market
    assert "부분 발행(partial)" in body and "근거 연결(L1)" in body
    assert "src-market-turboquant" not in body                 # 그 Claim 의 출처도 REFERENCE 에서 빠진다


def test_ranking_language_goes_back_to_synthesis(state):
    state["synthesis"]["agreements"][0]["text"] = "TurboQuant 가 ITME 보다 더 우수하다"
    out = evaluate(reported(state))

    assert out["quality_eval"]["verdicts"]["neutrality"]["status"] == "fail"
    assert out["quality_eval"]["next"] == "synthesis" and out["repair_count"] == 1


def test_withheld_comparison_is_not_a_verdict(state):
    """실데이터 오탐 — "어느 쪽이 더 우수한지는 근거가 없다" 는 우열을 유보한 중립 서술이다."""
    state["synthesis"]["agreements"][0]["text"] = (
        "어느 쪽이 전체 도메인 성능에서 더 우수한지는 동일 조건의 비교 근거가 없다.")
    assert evaluate(reported(state))["quality_eval"]["verdicts"]["neutrality"]["status"] == "pass"
    assert evaluator._verdict_phrases("판단을 유보한다. ITME 가 더 우수하다.") == ["더 우수"]


def test_ungrounded_synthesis_item_fails_l2(state):
    state["synthesis"]["agreements"].append({"text": "근거 없는 종합", "claim_ids": []})
    out = evaluate(reported(state))

    assert out["quality_eval"]["verdicts"]["groundedness_l2"]["status"] == "fail"
    assert out["quality_eval"]["next"] == "synthesis"


def test_repair_limit_publishes_partial(state):
    state["synthesis"]["agreements"].append({"text": "근거 없는 종합", "claim_ids": []})
    state = reported(state) | {"repair_count": 2}
    out = evaluate(state)

    assert out["quality_eval"]["next"] == "publish" and "max_repairs" in out["stop_reason"]


def test_step_limit_publishes_partial(state):
    state["synthesis"]["agreements"].append({"text": "근거 없는 종합", "claim_ids": []})
    out = evaluate(reported(state) | {"step_count": 11})

    assert out["quality_eval"]["next"] == "publish" and "max_steps" in out["stop_reason"]


def test_too_many_pages_goes_back_to_report_and_compacts(state, monkeypatch):
    """10쪽 초과 → report 재조립 → 압축 단계가 올라간다 → 재검사."""
    monkeypatch.setattr(pdf, "render_pdf", fake_renderer(lambda text: 9 if "압축 1단계" in text else 12))
    out = evaluate(state)
    assert out["quality_eval"]["next"] == "report"
    assert out["quality_eval"]["verdicts"]["structure"]["reasons"] == ["pages: 12쪽 > 10쪽"]

    again = reported(out)
    assert again["report_manifest"]["compaction"] == 1 and again["report_version"] == 2
    assert evaluate(again)["quality_eval"]["passed"]


def test_missing_pdf_dependency_is_reported_not_failed(state, monkeypatch):
    def boom(markdown_path, pdf_path):
        raise OSError("cannot load library 'libpango-1.0-0'")
    monkeypatch.setattr(pdf, "render_pdf", boom)
    quality = evaluate(state)["quality_eval"]

    assert quality["passed"] and quality["pdf_path"] == ""
    assert "미측정" in quality["verdicts"]["structure"]["reasons"][0]


# ── 편향 ①② 와 사전 정의 예외 ─────────────────────────────────────────────────

def single_group(monkeypatch, exceptions):
    config = quality_rules.quality_config() | {
        "source_groups": {f"src-{p}-turboquant": "google"
                          for p in ("research", "maturity", "market", "stakeholder", "domain_assessment")},
        "exceptions": exceptions}
    monkeypatch.setattr(quality_rules, "quality_config", lambda: config)


def test_single_source_group_without_exception_goes_back_to_orchestrator(state, monkeypatch):
    single_group(monkeypatch, [])
    out = evaluate(reported(state))
    bias = out["quality_eval"]["verdicts"]["bias"]

    assert bias["status"] == "fail" and out["quality_eval"]["next"] == "orchestrator"
    assert "maturity:TurboQuant" in bias["target_cells"]


def test_disclosed_exception_is_accepted(state, monkeypatch):
    single_group(monkeypatch, [{"technology": "TurboQuant", "reason": "다른 묶음 자료 없음",
                                "scope": "색인 전체"}])
    out = reported(state)
    assert out["report_manifest"]["disclosed_exceptions"] == ["TurboQuant"]
    assert "편향 예외 공개" in out["report"]

    quality = evaluate(out)["quality_eval"]
    assert quality["passed"] and quality["verdicts"]["bias"]["status"] == "accepted_exception"


def test_undisclosed_exception_goes_back_to_report(state, monkeypatch):
    single_group(monkeypatch, [{"technology": "TurboQuant", "reason": "다른 묶음 자료 없음"}])
    out = reported(state)
    out["report_manifest"]["disclosed_exceptions"] = []

    assert evaluate(out)["quality_eval"]["next"] == "report"


# ── publish guard (핵심 10) ──────────────────────────────────────────────────

def test_stale_report_after_resynthesis_is_blocked(state):
    out = evaluate(state)
    out["synthesis"] = out["synthesis"] | {"synthesis_version": 2}   # 종합만 다시 돌았다

    assert check_publish_guard(out) == ["report"]
    assert evaluator.route_after_eval(out) == "report"
    published = pdf.publish(out)
    assert published["run_status"] == "failed" and published["report_paths"] == []


def test_old_quality_eval_on_new_report_is_blocked(state):
    out = evaluate(state)
    newer = reported(out)                       # 보고서 v2, 평가는 v1 그대로
    assert check_publish_guard(newer) == ["quality_eval"]


def test_tampered_pdf_is_an_execution_error(state):
    out = evaluate(state)
    with open(out["quality_eval"]["pdf_path"], "ab") as f:
        f.write(b"tampered")
    with pytest.raises(PublishGuardError):
        check_publish_guard(out)


# ── Judge (대역 모델) ─────────────────────────────────────────────────────────

class FakeJudge:
    def __init__(self, answer):
        self.answer = answer
        self.schemas = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, prompt):
        self.schemas.append(self.schema.__name__)
        return self.answer(self.schema, prompt)


def asked(prompt):
    """대역 Judge 가 프롬프트에서 판정 대상 키를 읽는다 — L1 claim_id, L2 번호, 커버리지 기술."""
    import re
    claims = [line.split()[2] for line in prompt.splitlines() if line.startswith("- Claim ")]
    indices = [int(m) for m in re.findall(r"^\[(\d+)\] ", prompt, re.MULTILINE)]
    techs = re.search(r"기술 (.+?) 각각", prompt)
    return claims, indices, techs.group(1).split(", ") if techs else []


def agreeable(schema, prompt):
    claims, indices, techs = asked(prompt)
    if schema is evaluator.L1Judgment:
        return schema(verdicts=[{"claim_id": cid, "verdict": "supported"} for cid in claims])
    if schema is evaluator.L2Judgment:
        return schema(verdicts=[{"index": i, "within_scope": True} for i in indices])
    if schema is evaluator.NeutralityJudgment:
        return schema(neutral=True)
    if schema is evaluator.SelectivityJudgment:
        return schema(selective=False)
    return schema(cells=[{"technology": t, "substantive": True} for t in techs])


def test_judge_runs_on_every_criterion(state, monkeypatch):
    judge = FakeJudge(agreeable)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: judge)
    state["run_config"]["quality_judge"] = True
    out = evaluate(state)

    assert out["quality_eval"]["passed"]
    assert {"L1Judgment", "L2Judgment", "NeutralityJudgment", "SelectivityJudgment",
            "CoverageJudgment"} <= set(judge.schemas)
    assert out["quality_eval"]["scope"]["l1"] == "9/9"


def test_judge_rejection_invalidates_the_claim(state, monkeypatch):
    def answer(schema, prompt):
        if schema is evaluator.L1Judgment and MARKET_TQ in prompt:
            return schema(verdicts=[{"claim_id": MARKET_TQ, "verdict": "unsupported", "reason": "수치 불일치"}])
        return agreeable(schema, prompt)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: FakeJudge(answer))
    state["run_config"]["quality_judge"] = True
    out = evaluate(state)

    assert out["claim_flags"][MARKET_TQ]["status"] == "invalid"
    assert out["quality_eval"]["next"] == "orchestrator"


def test_judge_failure_publishes_partial(state, monkeypatch):
    def answer(schema, prompt):
        raise TimeoutError("judge timeout")
    monkeypatch.setattr(evaluator, "judge_llm", lambda: FakeJudge(answer))
    state["run_config"]["quality_judge"] = True
    out = evaluate(state)

    assert not out["quality_eval"]["passed"]
    assert out["quality_eval"]["next"] == "publish" and "Judge 실패" in out["stop_reason"]


# ── 층화 표본 ────────────────────────────────────────────────────────────────

def test_stratified_sample_is_deterministic_and_keeps_recheck():
    cells = {f"cell{i}": [f"c{i}-{j}" for j in range(20)] for i in range(4)}
    sample = quality_rules.stratified_sample(cells, {"c3-19"}, 10)

    assert len(sample) == 10 and sample[0] == "c3-19"
    assert sample == quality_rules.stratified_sample(cells, {"c3-19"}, 10)
    assert {cid.split("-")[0] for cid in sample} == {"c0", "c1", "c2", "c3"}    # 층마다 뽑는다
    assert quality_rules.stratified_sample({"a": ["x"], "b": ["x", "y"]}, set(), 60) == ["x", "y"]


# ── 리뷰 반영: Judge 응답 누락 (PR #19) ──────────────────────────────────────

def judge_on(state, monkeypatch, answer):
    judge = FakeJudge(answer)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: judge)
    state["run_config"]["quality_judge"] = True
    return judge


def test_missing_l1_items_are_asked_again(state, monkeypatch):
    """처음 응답이 일부를 빠뜨리면 빠진 것만 다시 묻는다. 기록은 실제 판정 수다."""
    first = {}

    def answer(schema, prompt):
        claims, _, _ = asked(prompt)
        if schema is evaluator.L1Judgment and len(claims) > 1 and not first.get(claims[0]):
            first[claims[0]] = True
            return schema(verdicts=[{"claim_id": claims[0], "verdict": "supported"}])   # 나머지 누락
        return agreeable(schema, prompt)
    judge = judge_on(state, monkeypatch, answer)
    out = evaluate(state)

    assert out["quality_eval"]["passed"] and out["quality_eval"]["scope"]["l1"] == "9/9"
    assert judge.schemas.count("L1Judgment") > len({"research", "maturity", "market",
                                                     "stakeholder", "domain_assessment"})


def test_items_the_judge_never_answers_are_not_passed(state, monkeypatch):
    """다시 물어도 빠지면 통과가 아니라 Judge 실패 — partial 로 발행한다."""
    def answer(schema, prompt):
        if schema is evaluator.L1Judgment:
            claims, _, _ = asked(prompt)        # STAKE_ITME 에는 몇 번을 물어도 답하지 않는다
            return schema(verdicts=[{"claim_id": cid, "verdict": "supported"}
                                    for cid in claims if cid != STAKE_ITME])
        return agreeable(schema, prompt)
    judge_on(state, monkeypatch, answer)
    out = evaluate(state)

    assert not out["quality_eval"]["passed"]
    assert out["quality_eval"]["next"] == "publish" and "응답 누락" in out["stop_reason"]
    assert out["quality_eval"]["verdicts"]["groundedness_l1"]["status"] == "skipped"


def test_missing_l2_index_is_not_passed(state, monkeypatch):
    def answer(schema, prompt):
        if schema is evaluator.L2Judgment:
            return schema(verdicts=[])
        return agreeable(schema, prompt)
    judge_on(state, monkeypatch, answer)

    assert "L2Judgment: 응답 누락" in evaluate(state)["stop_reason"]


def test_coverage_technology_case_is_normalized(state, monkeypatch):
    def answer(schema, prompt):
        if schema is evaluator.CoverageJudgment:
            _, _, techs = asked(prompt)
            return schema(cells=[{"technology": t.lower(), "substantive": t != "ITME"} for t in techs])
        return agreeable(schema, prompt)
    judge_on(state, monkeypatch, answer)
    coverage = evaluate(state)["quality_eval"]["verdicts"]["coverage"]

    assert coverage["status"] == "fail" and "maturity:ITME" in coverage["target_cells"]


# ── 리뷰 반영: 편향 ①② 원인 구분 (PR #19) ─────────────────────────────────────

def diverse_hidden(state, monkeypatch):
    """표시 Claim 은 모두 google 묶음, 숨은 maturity Claim 4건은 서로 다른 묶음."""
    roles = ("research", "maturity", "market", "stakeholder", "domain_assessment")
    config = quality_rules.quality_config() | {
        "source_groups": {f"src-{p}-turboquant": "google" for p in roles}, "exceptions": []}
    monkeypatch.setattr(quality_rules, "quality_config", lambda: config)
    maturity = state["maturity"]
    for i in range(4):
        evidence_id = f"{i}" * 12
        maturity["sources"].append({"source_id": f"other-{i}", "run_id": state["run_id"],
                                    "collection": "papers_core", "allowed_uses": ["maturity"],
                                    "title": f"다른 묶음 {i}"})
        maturity["evidence"].append({"evidence_id": evidence_id, "source_id": f"other-{i}",
                                     "run_id": state["run_id"], "collection": "papers_core",
                                     "quote": "다른 묶음 인용", "location": "page:1",
                                     "allowed_uses": ["maturity"]})
        maturity["claims"].append({"claim_id": f"hidden-{i}", "text": f"TurboQuant 다른 근거 {i}",
                                   "technology": "TurboQuant", "kind": "fact",
                                   "evidence_ids": [evidence_id]})
    return state


def test_display_only_skew_goes_back_to_report_not_orchestrator(state, monkeypatch):
    """수집 근거 전체로는 통과하는데 표시만 한 묶음이면 재조사가 아니라 보고서 수리다."""
    out = reported(diverse_hidden(collected_from(state), monkeypatch))
    sections = out["report_manifest"]["sections"]
    sections["maturity"] = [c for c in sections["maturity"] if not c.startswith("hidden-")]
    out["report_manifest"]["references"] = [r for r in out["report_manifest"]["references"]
                                            if not r.startswith("other-")]
    quality = evaluate(out)["quality_eval"]
    bias = quality["verdicts"]["bias"]

    assert bias["status"] == "fail" and bias["target_cells"] == []
    assert "표시 선택이 한 묶음에 몰렸다" in bias["reasons"][0]
    assert quality["next"] == "report"


def test_report_selection_spreads_source_groups(state, monkeypatch):
    """보고서가 다른 묶음 근거를 골라 실으므로 report 단계 수리로 실제로 고쳐진다."""
    out = reported(diverse_hidden(collected_from(state), monkeypatch))

    assert sum(c.startswith("hidden-") for c in out["report_manifest"]["sections"]["maturity"]) >= 3
    assert evaluate(out)["quality_eval"]["verdicts"]["bias"]["status"] == "pass"


def test_skew_in_collected_evidence_still_goes_to_orchestrator(state, monkeypatch):
    roles = ("research", "maturity", "market", "stakeholder", "domain_assessment")
    config = quality_rules.quality_config() | {
        "source_groups": {f"src-{p}-turboquant": "google" for p in roles}, "exceptions": []}
    monkeypatch.setattr(quality_rules, "quality_config", lambda: config)
    quality = evaluate(reported(collected_from(state)))["quality_eval"]

    assert quality["verdicts"]["bias"]["target_cells"] and quality["next"] == "orchestrator"


def collected_from(state):
    """보고서 결과 키를 지운 State — 보고서를 처음부터 다시 만든다."""
    return {k: v for k, v in state.items() if k not in ("report", "report_manifest", "report_version")}
