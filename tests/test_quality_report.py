"""보고서 manifest·표시 상한·partial 발행·Human Review — 담당 C (계획서 §7-1·§7-2·§9)"""

import csv
import json
from pathlib import Path

import pytest

from src.agents import report
from src.orchestrator import evaluator
from src.output import pdf
from tests.quality_fixtures import collected, fake_renderer, reported

MATURITY_TQ = "r0-maturity-both:claim_maturity_turboquant_contract"


@pytest.fixture
def state(tmp_path):
    return collected(tmp_path)


def add_claims(state, role, tech, n, text="추가 주장 {i}", prefix="x"):
    evidence = state[role]["evidence"][0]["evidence_id"]
    for i in range(n):
        state[role]["claims"].append({"claim_id": f"{prefix}-{role}-{tech}-{i}", "text": text.format(i=i),
                                      "technology": tech, "kind": "fact", "evidence_ids": [evidence]})


def techs(state, role, ids, tech):
    by_id = {c["claim_id"]: c for c in state[role]["claims"]}
    return [cid for cid in ids if by_id[cid]["technology"] == tech]


def test_manifest_lists_what_is_shown(state):
    out = report.report(state)
    manifest = out["report_manifest"]

    assert out["report_version"] == 1 and manifest["based_on_synthesis_version"] == 1
    assert MATURITY_TQ in manifest["sections"]["maturity"]
    assert manifest["sections"]["summary"] == sorted(state["synthesis"]["agreements"][0]["claim_ids"]
                                                     + state["synthesis"]["conflicts"][0]["claim_ids"])
    assert manifest["gaps"][0] == {"perspective": "market", "technology": "ITME",
                                   "kind": "evidence_gap", "item": "ITME 상용 채택 사례"}
    assert "src-market-turboquant" in manifest["references"]
    assert "(조사 질의 1건 실행)" in out["report"]
    assert report.report(state | out)["report_version"] == 2


def test_cell_cap_keeps_pinned_and_limit_claims(state):
    """칸 상한 3 — 종합이 참조한 Claim 과 한계·비용 Claim 은 상한과 무관하게 남는다."""
    add_claims(state, "maturity", "TurboQuant", 5)
    add_claims(state, "maturity", "ITME", 4)
    add_claims(state, "maturity", "ITME", 1, text="ITME 는 원격 메모리 접근 지연 오버헤드가 있다",
               prefix="limit")
    shown = report.report(state)["report_manifest"]["sections"]

    assert MATURITY_TQ in shown["maturity"]                     # 종합이 참조 → 항상 표시
    assert len(techs(state, "maturity", shown["maturity"], "TurboQuant")) == 3
    assert "x-maturity-ITME-3" not in shown["maturity"]            # 상한 밖
    # 한계·비용 Claim 은 상한 밖이었지만 기술별 1건은 남긴다(압축 규칙, 편향 ③ 방지)
    assert "limit-maturity-ITME-0" in shown["maturity"]


def test_minimum_level_shows_one_claim_per_cell(state):
    add_claims(state, "maturity", "TurboQuant", 5)
    state["synthesis"]["agreements"] = []
    state["synthesis"]["conflicts"] = []
    md, manifest = report.build(state, compaction=2)

    assert len(techs(state, "maturity", manifest["sections"]["maturity"], "TurboQuant")) == 1
    assert manifest["compaction"] == 2 and "압축 2단계" in md
    assert md.index("## SUMMARY") < md.index("## REFERENCE")


def test_invalid_claim_is_gone_from_body_synthesis_and_reference(state):
    state["claim_flags"] = {MATURITY_TQ: {"status": "invalid", "reason": "Judge: 수치 불일치",
                                         "by": "quality_eval"}}
    out = report.report(state)

    assert MATURITY_TQ not in out["report_manifest"]["sections"]["maturity"]
    assert MATURITY_TQ not in out["report_manifest"]["sections"]["summary"]
    assert "src-maturity-turboquant" not in out["report_manifest"]["references"]
    assert "품질 평가에서 근거 무효로 판정해 본문에서 뺀 Claim 1건" in out["report"]


def test_gap_kinds_are_labeled(state):
    state["gaps"].append({"role": "stakeholder", "technology": "ITME", "item": "투자자 발언",
                          "reason": "TimeoutError", "kind": "execution_gap"})
    md = report.report(state)["report"]

    assert "투자자 발언" in md and "*(조사가 끝나지 않은 칸 — 조사 실행 실패 1건)*" in md


def test_limits_section_states_the_quality_scope(state):
    md = report.report(state)["report"]
    assert "코드 기반 평가(1안)" in md and "표시된 Claim 9건" in md

    state["run_config"]["quality_judge"] = True
    md = report.report(state)["report"]
    assert "9/9건(전수)" in md and "자기평가 편향" in md
    assert "워크시트에서 사람이 확인" not in md                 # 옛 한계 문구는 교체됐다


# ── partial 발행 (핵심 11) ───────────────────────────────────────────────────

def failing(state):
    state["synthesis"]["agreements"].append({"text": "근거 없는 종합", "claim_ids": []})
    out = reported(state) | {"repair_count": 2}
    return out | evaluator.quality_eval(out)


def test_partial_is_marked_up_front_and_in_limits(state, monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "render_pdf", fake_renderer(lambda text: 7))
    out = failing(state)
    update = pdf.publish(out)
    body = (tmp_path / state["run_id"] / "report.md").read_text(encoding="utf-8")

    assert update["run_status"] == "partial"
    assert body.index("부분 발행(partial)") < body.index("## SUMMARY")
    assert "**품질 평가 미달 (부분 발행)**" in body and "종합 근거(L2)" in body
    assert (tmp_path / state["run_id"] / "final" / pdf.FINAL_FILENAME).exists()


def test_partial_over_ten_pages_is_compacted_before_publishing(state, monkeypatch, tmp_path):
    """10쪽 초과 상태로는 발행하지 않는다 — 최소 표시 수준으로 다시 렌더링한다."""
    monkeypatch.setattr(pdf, "render_pdf", fake_renderer(lambda text: 8 if "압축 2단계" in text else 13))
    out = failing(state)
    update = pdf.publish(out)
    result = json.loads((tmp_path / state["run_id"] / "submission.json").read_text())

    assert update["run_status"] == "partial"
    assert result["generated"] and result["pages"] == 8 and result["compaction"] == 2


def test_partial_that_cannot_fit_is_not_submitted(state, monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "render_pdf", fake_renderer(lambda text: 13))
    update = pdf.publish(failing(state))

    assert not (tmp_path / state["run_id"] / "final" / pdf.FINAL_FILENAME).exists()
    assert len(update["report_paths"]) == 1


def test_passed_report_publishes_the_evaluated_pdf(state, monkeypatch, tmp_path):
    render = fake_renderer(lambda text: 7)
    monkeypatch.setattr(pdf, "render_pdf", render)
    out = reported(state)
    out = out | evaluator.quality_eval(out)
    update = pdf.publish(out)

    final = tmp_path / state["run_id"] / "final" / pdf.FINAL_FILENAME
    assert update["run_status"] == "completed" and final.exists()
    assert final.read_bytes() == Path(out["quality_eval"]["pdf_path"]).read_bytes()
    assert len(render.calls) == 1                     # 평가한 PDF 를 그대로 옮긴다


# ── Human Review (선택, 계획서 §9) ───────────────────────────────────────────

@pytest.fixture
def reviewed(state, monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "render_pdf", fake_renderer(lambda text: 7))
    state["run_config"] |= {"human_review": True, "review_ledger": str(tmp_path / "ledger.json")}
    out = reported(state)
    return out | evaluator.quality_eval(out)


def fill(path, verdict_for):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for row in rows:
        row["review_result"], row["reviewer"] = verdict_for(row), "검토자"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_rejected_claim_is_flagged_and_goes_back_to_synthesis(reviewed, tmp_path):
    from src.output import review

    assert reviewed["quality_eval"]["next"] == "human_review"
    path, _ = review.prepare_worksheet(reviewed)
    fill(path, lambda row: "부결" if row["claim_id"] == MATURITY_TQ else "확인")
    out = reviewed | evaluator.apply_review(reviewed)

    flag = out["claim_flags"][MATURITY_TQ]
    assert (flag["status"], flag["by"], flag["reviewer"]) == ("invalid", "human_review", "검토자")
    assert evaluator.route_after_review(out) == "synthesis"
    assert MATURITY_TQ not in report.report(out)["report_manifest"]["sections"]["maturity"]


def test_no_rejection_goes_to_publish_and_pending_is_logged(reviewed, tmp_path):
    out = reviewed | evaluator.apply_review(reviewed)          # worksheet 를 만들고 판정 없음

    assert evaluator.route_after_review(out) == "publish"
    line = json.loads((tmp_path / reviewed["run_id"] / "decisions.jsonl").read_text().splitlines()[-1])
    assert line["node"] == "apply_review" and len(line["pending"]) == 9


def test_worksheet_keeps_earlier_verdicts_when_new_claims_appear(reviewed):
    from src.output import review

    path, _ = review.prepare_worksheet(reviewed)
    fill(path, lambda row: "확인")
    reviewed["maturity"]["claims"].append(dict(reviewed["maturity"]["claims"][0], claim_id="new-claim"))
    reviewed["report_manifest"]["sections"]["maturity"].append("new-claim")
    review.prepare_worksheet(reviewed)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))

    assert {r["claim_id"]: r["review_result"] for r in rows}["new-claim"] == ""
    assert sum(r["review_result"] == "확인" for r in rows) == len(rows) - 1
