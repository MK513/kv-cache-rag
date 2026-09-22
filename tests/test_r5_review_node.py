"""내용 검토 노드 — 담당: R5 (설계서 §8, 부록 A 의 `R` 노드)

형식 검사와 사람의 내용 검토가 한 노드다. **ID 대조만으로 자동 통과시키지 않는다.**
"""

import csv

import pytest

from src.output import review as node


def assessment(role):
    return {
        "status": "completed",
        "claims": [{"claim_id": f"claim-{role}", "text": f"{role} 주장", "technology": "ITME",
                    "kind": "fact", "evidence_ids": [f"e-{role}"]}],
        "evidence": [{"evidence_id": f"e-{role}", "source_id": f"s-{role}", "run_id": "r5-test",
                      "collection": "ecosystem", "quote": "합성 인용", "location": "p.1",
                      "allowed_uses": [role]}],
        "sources": [{"source_id": f"s-{role}", "run_id": "r5-test", "collection": "ecosystem",
                     "allowed_uses": [role], "title": f"{role} 출처"}],
        "gaps": [],
    }


@pytest.fixture
def state(tmp_path):
    return {"run_id": "r5-test", "run_config": {"runs_dir": str(tmp_path)},
            "market": assessment("market")}


def fill(path, result, reviewer="권예리"):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for row in rows:
        row["review_result"] = result
        row["reviewer"] = reviewer
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_first_pass_writes_worksheet_and_waits(state, tmp_path):
    """판정이 비어 있으면 통과시키지 않는다 — 검토 대기."""
    update = node.review(state)

    assert set(update) == {"validation", "review_status", "trace"}
    assert update["review_status"] == "pending"
    assert update["validation"]["pending_claims"] == ["claim-market"]
    assert (tmp_path / "r5-test" / "review.csv").exists()


def test_filled_worksheet_passes(state, tmp_path):
    node.review(state)
    fill(tmp_path / "r5-test" / "review.csv", "확인")

    update = node.review(state)
    assert update["review_status"] == "passed"
    assert update["validation"]["reviewers"] == ["권예리"]
    assert update["validation"]["claim_verdicts"]["claim-market"]["verdict"] == "확인"


def test_rejected_claim_passes_review_but_is_listed_for_exclusion(state, tmp_path):
    """§8 — 부결된 주장은 판정된 것이다. 사실 서술에서 빼도록 목록으로 남긴다."""
    node.review(state)
    fill(tmp_path / "r5-test" / "review.csv", "부결")

    update = node.review(state)
    assert update["review_status"] == "passed"
    assert update["validation"]["rejected_claims"] == ["claim-market"]


def test_unknown_verdict_becomes_an_error(state, tmp_path):
    node.review(state)
    fill(tmp_path / "r5-test" / "review.csv", "글쎄요")

    errors = node.review(state)["validation"]["errors"]
    assert any(error["kind"] == "판정 값 미상" for error in errors)


def test_existing_worksheet_is_not_overwritten(state, tmp_path):
    """재개할 때 사람이 채운 판정을 지우지 않는다."""
    node.review(state)
    path = tmp_path / "r5-test" / "review.csv"
    fill(path, "확인")
    before = path.read_text(encoding="utf-8")

    node.review(state)
    assert path.read_text(encoding="utf-8") == before


def test_format_errors_survive_into_validation(state, tmp_path):
    """형식 검사 결과가 같은 validation 에 실린다 — final_check 이 이걸 본다."""
    state["market"]["claims"][0]["evidence_ids"] = ["e-missing"]

    errors = node.review(state)["validation"]["errors"]
    assert any(error["kind"] == "Evidence 없음" for error in errors)
