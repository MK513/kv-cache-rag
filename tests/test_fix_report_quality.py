"""20261007 실행에서 보고서가 무너진 원인별 회귀 테스트.

1 Claim 본문 240자 절단·인용 번호 반복·본문 속 `##` 제목   2 기술별 절 분리 실패
3 stakeholder 교차 인용 1건으로 초안 전체 폐기          4 인용 표기 변형·인용 누락으로 failed
5 편향 ③ 을 synthesis 로 보내 같은 판정 반복
"""

from langchain_core.runnables import RunnableLambda

from src.agents import common, report
from src.orchestrator import evaluator
from tests.test_quality_eval import FakeJudge, agreeable, evaluate, renderer, state  # noqa: F401 (fixtures)
from tests.test_r3_stakeholder import claim, factory, make_store

A, B = "e00130c62328", "425cea73318e"


def test_citation_variants_are_normalized():
    text = f"근거 [ {A} ]. 둘 [ {A}, {B} ] 셋 [{A}; {B}]"
    assert common.normalize_citations(text) == f"근거 [{A}]. 둘 [{A}][{B}] 셋 [{A}][{B}]"


def test_run_node_retries_once_when_no_citation(monkeypatch):
    prompts = []

    def fake(prompt):
        prompts.append(prompt.to_string())
        return "인용 없음" if len(prompts) == 1 else f"재작성 [ {A} ]"
    monkeypatch.setattr(common, "get_llm", lambda: RunnableLambda(fake))
    assert common.run_node("지시", "도메인", "근거") == f"재작성 [{A}]"
    assert len(prompts) == 2 and "재작성 지시" in prompts[1]


def test_split_by_tech_accepts_heading_variants():
    for tq, itme in (("## TurboQuant", "## ITME"), ("1. TurboQuant TRL 평가:", "### 2. ITME TRL 평가")):
        parts = common.split_by_tech(f"머리말\n{tq}\nTurboQuant는 양자화다 [{A}]\n{itme}\nITME 는 확장이다 [{B}]")
        assert parts["TurboQuant"].startswith(tq) and B not in parts["TurboQuant"]
        assert parts["ITME"].startswith(itme)
    assert common.split_by_tech("TurboQuant는 본문 줄이다\nITME는 본문 줄이다") == {}


def test_report_claims_keep_toc_and_cite_each_source_once():
    body = report._claims([{"text": f"## TurboQuant\n양자화 [{A}]", "kind": "fact"}])
    assert "## TurboQuant" not in body
    assert report._number_citations(f"x [{A}] [{B}] [{A}]", {A: 2, B: 2}) == "x [2]"


def test_stakeholder_drops_only_the_bad_claim_after_repair(tmp_path):
    store = make_store(tmp_path)

    def writer(**kw):
        good = next(e for e, v in store.manifest["evidence"].items() if "Developer Kim" in v["quote"])
        return {"claims": [claim(good), claim("invented", stakeholder_group="adopters")], "gaps": []}
    out = factory(store, writer)({"run_id": "test-run"})["stakeholder"]
    assert out["status"] == "partial" and len(out["claims"]) == 1
    adopters = next(g for g in out["gaps"] if g["technology"] == "TurboQuant" and g["item"] == "adopters")
    assert adopters["kind"] == "invalid_evidence"


def test_selectivity_without_hidden_limits_goes_back_to_orchestrator(state, monkeypatch):  # noqa: F811
    def answer(schema, prompt):
        if schema is evaluator.SelectivityJudgment and prompt.find("TurboQuant 에 대해") >= 0:
            return schema(selective=True, reason="한계 없음")
        return agreeable(schema, prompt)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: FakeJudge(answer))
    state["run_config"]["quality_judge"] = True
    out = evaluate(state)
    assert out["quality_eval"]["next"] == "orchestrator"
    assert "market:TurboQuant" in out["quality_eval"]["verdicts"]["bias"]["target_cells"]


def test_flagged_selectivity_shows_every_hidden_limit_claim():
    claims = [{"claim_id": cid, "technology": "TurboQuant", "kind": "fact", "text": text, "evidence_ids": []}
              for cid, text in (("a", "메모리 비용 증가"), ("b", "전송 지연 발생"))]
    state = {"market": {"claims": claims}}
    shown = report._select(state, set(), set(), 1, lambda c: set())
    assert [c["claim_id"] for c in shown["market"]] == ["a"]
    state["quality_eval"] = {"verdicts": {"bias": {"reasons": ["③ TurboQuant 선택적 근거 사용 — x"]}}}
    shown = report._select(state, set(), set(), 1, lambda c: set())
    assert [c["claim_id"] for c in shown["market"]] == ["a", "b"]


# ── 20261007-170016 실행: Judge 가 Claim 대부분을 무효로 만들어 본문이 비고 §6 이 54줄이 된 원인 ──

def test_judge_sees_the_whole_evidence_quote(state, monkeypatch):  # noqa: F811
    long_quote = "가" * 1500 + "끝표지"
    for item in state["evidence_registry"].values():
        item["quote"] = long_quote
    prompts = []

    def answer(schema, prompt):
        prompts.append(prompt)
        return agreeable(schema, prompt)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: FakeJudge(answer))
    state["run_config"]["quality_judge"] = True
    evaluate(state)
    assert any("끝표지" in p for p in prompts)


def test_partial_judgment_keeps_the_claim_and_notes_it(state, monkeypatch):  # noqa: F811
    from tests.test_quality_eval import MARKET_TQ, asked

    def answer(schema, prompt):
        if schema is evaluator.L1Judgment:
            claims, _, _ = asked(prompt)
            return schema(verdicts=[{"claim_id": c, "verdict": "partial" if c == MARKET_TQ else "supported"}
                                    for c in claims])
        return agreeable(schema, prompt)
    monkeypatch.setattr(evaluator, "judge_llm", lambda: FakeJudge(answer))
    state["run_config"]["quality_judge"] = True
    out = evaluate(state)
    assert out["claim_flags"][MARKET_TQ]["status"] == "partial"
    assert out["quality_eval"]["passed"]
    rebuilt = report.report(out)
    assert MARKET_TQ in rebuilt["report_manifest"]["sections"]["market"]
    assert "일부 세부 서술이 인용 구절로 확인되지 않은 Claim 1건" in rebuilt["report"]


def test_task_queries_are_added_to_the_defaults():
    task = {"technologies": ["ITME"], "queries": ["ITME testbed"], "focus": "f"}
    _, queries, _ = common.task_for({"task": task}, ["기본 질의"])
    assert queries == ["기본 질의", "ITME testbed"]


# ── 20261007-171655 실행: 본문 중간의 불필요한 글 ──

def test_clip_stops_at_a_sentence_not_inside_a_word_or_citation():
    text = "첫 문장이다. " * 30 + f"마지막 문장은 [{A}] 로 끝난다"
    clipped = report._clip(text, len(text) - 12)
    assert clipped.split("…")[0].rstrip().endswith("다.")
    assert "[e0013" not in clipped.replace(f"[{A}]", "")


def test_absence_claims_are_picked_last_and_techs_are_ordered():
    claims = [
        {"claim_id": "itme", "technology": "ITME", "kind": "fact", "text": "ITME 실험", "evidence_ids": []},
        {"claim_id": "none", "technology": "TurboQuant", "kind": "fact",
         "text": "경쟁사 발언은 확인되지 않는다", "evidence_ids": []},
        {"claim_id": "real", "technology": "TurboQuant", "kind": "fact",
         "text": "SGLang 이슈에 TurboQuant 지원 요청이 올라왔다", "evidence_ids": []},
    ]
    shown = report._select({"stakeholder": {"claims": claims}}, set(), set(), 1, lambda c: set())
    assert [c["claim_id"] for c in shown["stakeholder"]] == ["real", "itme"]


def test_partial_notice_has_no_internal_ids():
    partial = {"stop_reason": "Judge 실패 — L1Judgment: 응답 누락 3건 — r0-market-both:claim_market_①_x",
               "failed": [("groundedness_l2", ["근거 Claim 이 없는 종합 문장: …"] * 5),
                          ("structure", ["pages: 11쪽 > 10쪽"])]}
    text = report._partial_banner(partial) + report._partial_limits(partial)
    assert "L1Judgment" not in text and "claim_market" not in text and "근거 Claim 이 없는" not in text
    assert "판정 응답 누락 3건" in text and "종합 근거(L2) 5건" in text and "구조·분량 11쪽 > 10쪽" in text


def test_gap_items_drop_cyrillic_and_hanja():
    gaps = common.gaps_from_text("근거 공백: ITME 1.81배 세부 실험표 стой | 漢字", role="maturity", reason="r")
    assert [g.item for g in gaps] == ["ITME 1.81배 세부 실험표"]
