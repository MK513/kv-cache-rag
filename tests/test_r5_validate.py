"""R5 validate.py 단위 테스트."""

from src.output import validate


def _base_state():
    """정상적인 신형 Assessment 구조."""
    return {
        "run_id": "run-test-001",
        "stakeholder": {
            "claims": [
                {
                    "claim_id": "claim-001",
                    "technology": "ITME",
                    "kind": "fact",
                    "text": "테스트용 주장",
                    "evidence_ids": ["evidence-001"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "evidence-001",
                    "source_id": "source-001",
                    "run_id": "run-test-001",
                    "quote": "테스트용 원문 근거",
                    "allowed_uses": ["stakeholder"],
                }
            ],
            "sources": [
                {
                    "source_id": "source-001",
                    "run_id": "run-test-001",
                    "url": "https://example.com/test",
                }
            ],
            "gaps": [],
            "status": "completed",
        },
        "validation_round": 0,
    }


def test_valid_structured_claim_passes():
    """정상 Claim → Evidence → Source 연결은 PASS."""

    result = validate.check(_base_state())

    assert result["validation"]["errors"] == []


def test_missing_evidence_fails():
    """존재하지 않는 Evidence ID를 참조하면 FAIL."""

    state = _base_state()
    state["stakeholder"]["claims"][0]["evidence_ids"] = [
        "evidence-missing"
    ]

    result = validate.check(state)

    kinds = [
        error["kind"]
        for error in result["validation"]["errors"]
    ]

    assert "Evidence 없음" in kinds


def test_cross_run_evidence_fails():
    """다른 run에서 생성된 Evidence는 FAIL."""

    state = _base_state()
    state["stakeholder"]["evidence"][0]["run_id"] = "run-other"

    result = validate.check(state)

    kinds = [
        error["kind"]
        for error in result["validation"]["errors"]
    ]

    assert "다른 run의 Evidence" in kinds


def test_disallowed_evidence_use_fails():
    """해당 노드에 허용되지 않은 Evidence 사용은 FAIL."""

    state = _base_state()
    state["stakeholder"]["evidence"][0]["allowed_uses"] = ["market"]

    result = validate.check(state)

    kinds = [
        error["kind"]
        for error in result["validation"]["errors"]
    ]

    assert "허용되지 않은 Evidence 사용" in kinds

def test_check_writes_the_validation_contract():
    """State 필드는 validation 하나다. validation_errors/validation_round 는 필드가 아니다."""

    result = validate.check(_base_state())

    assert set(result) == {"validation", "trace"}
    assert set(result["validation"]) == {"errors", "checked_nodes"}
    assert result["trace"][0]["node"] == "validate"


def test_errors_keep_claim_id_for_locating_the_body():
    """설계서 §8 — 검증 실패 시 본문 위치를 찾을 수 있도록 claim_id 를 유지한다."""

    state = _base_state()
    state["stakeholder"]["claims"][0]["evidence_ids"] = ["evidence-missing"]

    errors = validate.check(state)["validation"]["errors"]
    assert errors and all(error["claim_id"] for error in errors)
