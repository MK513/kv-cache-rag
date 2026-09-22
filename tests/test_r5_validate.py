"""R5 validate.py 단위 테스트."""

from src.output import validate


def _fake_build():
    """실제 FAISS/manifest를 읽지 않는 테스트용 index."""
    return {
        "chunks": {
            "chunk-001": object(),
            "chunk-002": object(),
        }
    }


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


def test_valid_structured_claim_passes(monkeypatch):
    """정상 Claim → Evidence → Source 연결은 PASS."""
    monkeypatch.setattr(
        validate,
        "_build_index",
        _fake_build,
    )

    result = validate.check(
        _base_state(),
        stage="post_assessment",
    )

    assert result["validation_errors"] == []


def test_missing_evidence_fails(monkeypatch):
    """존재하지 않는 Evidence ID를 참조하면 FAIL."""
    monkeypatch.setattr(
        validate,
        "_build_index",
        _fake_build,
    )

    state = _base_state()
    state["stakeholder"]["claims"][0]["evidence_ids"] = [
        "evidence-missing"
    ]

    result = validate.check(
        state,
        stage="post_assessment",
    )

    kinds = [
        error["kind"]
        for error in result["validation_errors"]
    ]

    assert "Evidence 없음" in kinds


def test_cross_run_evidence_fails(monkeypatch):
    """다른 run에서 생성된 Evidence는 FAIL."""
    monkeypatch.setattr(
        validate,
        "_build_index",
        _fake_build,
    )

    state = _base_state()
    state["stakeholder"]["evidence"][0]["run_id"] = "run-other"

    result = validate.check(
        state,
        stage="post_assessment",
    )

    kinds = [
        error["kind"]
        for error in result["validation_errors"]
    ]

    assert "다른 run의 Evidence" in kinds


def test_disallowed_evidence_use_fails(monkeypatch):
    """해당 노드에 허용되지 않은 Evidence 사용은 FAIL."""
    monkeypatch.setattr(
        validate,
        "_build_index",
        _fake_build,
    )

    state = _base_state()
    state["stakeholder"]["evidence"][0]["allowed_uses"] = ["market"]

    result = validate.check(
        state,
        stage="post_assessment",
    )

    kinds = [
        error["kind"]
        for error in result["validation_errors"]
    ]

    assert "허용되지 않은 Evidence 사용" in kinds