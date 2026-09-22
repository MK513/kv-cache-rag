"""R5 reference.py 단위 테스트."""

from src.output import reference


def _state():
    return {
        "stakeholder": {
            "claims": [
                {
                    "claim_id": "claim-001",
                    "technology": "ITME",
                    "kind": "fact",
                    "text": "실제로 사용된 주장",
                    "evidence_ids": ["evidence-used"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "evidence-used",
                    "source_id": "source-used",
                    "quote": "실제로 인용된 원문",
                },
                {
                    "evidence_id": "evidence-unused",
                    "source_id": "source-unused",
                    "quote": "검색됐지만 사용되지 않은 원문",
                },
            ],
            "sources": [
                {
                    "source_id": "source-used",
                    "source_type": "official",
                    "title": "Used Source",
                    "published_at": "2026-09-01",
                    "retrieved_at": "2026-09-22",
                    "url": "https://example.com/used",
                },
                {
                    "source_id": "source-unused",
                    "source_type": "media",
                    "title": "Unused Source",
                    "published_at": "2026-09-02",
                    "retrieved_at": "2026-09-22",
                    "url": "https://example.com/unused",
                },
            ],
            "gaps": [],
            "status": "completed",
        }
    }


def test_only_used_source_is_in_reference():
    """실제 Claim이 사용한 Source만 REFERENCE에 포함되어야 한다."""
    output = reference.build(state=_state())

    assert "Used Source" in output
    assert "https://example.com/used" in output

    assert "Unused Source" not in output
    assert "https://example.com/unused" not in output


def test_reference_has_web_section():
    """웹 Source는 웹 조회 자료 섹션에 들어가야 한다."""
    output = reference.build(state=_state())

    assert "## REFERENCE" in output
    assert "### [C] 웹 조회 자료" in output


def test_duplicate_source_is_removed():
    """여러 Claim이 같은 Source를 써도 REFERENCE에는 한 번만 나와야 한다."""
    state = _state()

    state["stakeholder"]["claims"].append(
        {
            "claim_id": "claim-002",
            "technology": "ITME",
            "kind": "inference",
            "text": "같은 출처를 사용하는 두 번째 주장",
            "evidence_ids": ["evidence-used"],
        }
    )

    output = reference.build(state=state)

    assert output.count("https://example.com/used") == 1
