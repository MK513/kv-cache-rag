"""R5 human review worksheet 단위 테스트."""

from src.output.review import build_review_rows


def _state():
    return {
        "stakeholder": {
            "claims": [
                {
                    "claim_id": "claim-001",
                    "technology": "ITME",
                    "kind": "fact",
                    "text": "테스트 주장",
                    "evidence_ids": ["evidence-001"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "evidence-001",
                    "source_id": "source-001",
                    "quote": "테스트 원문 구절",
                }
            ],
            "sources": [
                {
                    "source_id": "source-001",
                    "source_type": "official",
                    "url": "https://example.com/test",
                }
            ],
            "gaps": [],
            "status": "completed",
        }
    }


def test_review_row_created():
    rows = build_review_rows(_state())

    assert len(rows) == 1

    row = rows[0]

    assert row["claim_id"] == "claim-001"
    assert row["node"] == "stakeholder"
    assert row["technology"] == "ITME"
    assert row["evidence_id"] == "evidence-001"
    assert row["source_id"] == "source-001"
    assert row["evidence_quote"] == "테스트 원문 구절"
    assert row["location"] == "https://example.com/test"


def test_review_fields_are_empty_for_human_input():
    rows = build_review_rows(_state())

    row = rows[0]

    assert row["review_result"] == ""
    assert row["reviewer"] == ""
    assert row["review_comment"] == ""

def test_row_keeps_the_evidence_location():
    """§3 — Evidence 는 페이지 또는 웹 문단 위치를 기록한다. 검토자가 원문을 찾을 단서다."""
    state = {
        "market": {
            "claims": [{"claim_id": "c1", "text": "주장", "technology": "ITME",
                        "kind": "fact", "evidence_ids": ["e1"]}],
            "evidence": [{"evidence_id": "e1", "source_id": "s1", "collection": "ecosystem",
                          "quote": "인용", "location": "p.7"}],
            "sources": [{"source_id": "s1", "collection": "ecosystem", "title": "출처"}],
        }
    }
    row = build_review_rows(state)[0]

    assert row["location"] == "p.7"
    assert row["source_type"] == "ecosystem"   # 직접 근거와 인접 생태계를 가르는 단서
