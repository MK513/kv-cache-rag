"""R5 report.py 단위 테스트."""

from src.agents import report as report_module


def _state():
    return {
        "domain": "데이터센터/클라우드",
        "tech_status": {
            "TurboQuant": "ok",
            "ITME": "ok",
        },
        "research": {
            "text": "기술 조사 본문",
        },
        "maturity": {
            "text": "TRL 평가 본문",
        },
        "market": {
            "text": "시장성 평가 본문",
        },
        "stakeholder": {
            "text": "이해관계자 평가 본문",
        },
        "domain_assessment": {
            "text": "도메인 평가 본문",
        },
        "synthesis": {
            "agreements": [
                "두 기술 모두 KV cache 메모리 병목을 완화한다."
            ],
            "conflicts": [],
            "gaps": [
                "두 기술의 결합 실측 자료 미확인"
            ],
            "combination_hypothesis": (
                "두 기술은 서로 다른 시스템 계층에서 "
                "병목을 완화할 가능성이 있다."
            ),
        },
        "sources": [],
        "web_sources": [],
    }


def test_report_created_without_conflicts(monkeypatch):
    """conflicts가 비어 있어도 보고서는 정상 생성되어야 한다."""

    monkeypatch.setattr(
        report_module,
        "model_name",
        lambda: "test-model",
    )

    result = report_module.report(_state())

    assert "report" in result

    markdown = result["report"]

    assert "# KV cache 최적화 기술 다관점 평가" in markdown
    assert "## SUMMARY" in markdown
    assert "## 4. 관점별 평가" in markdown
    assert "## 5. 시사점" in markdown
    assert "## 6. 한계" in markdown
    assert "## REFERENCE" in markdown


def test_no_conflict_is_not_error(monkeypatch):
    """상충 0건은 오류가 아니라 정상적인 평가 결과다."""

    monkeypatch.setattr(
        report_module,
        "model_name",
        lambda: "test-model",
    )

    result = report_module.report(_state())

    markdown = result["report"]

    assert "확인된 관점 간 상충 없음" in markdown


def test_existing_state_text_is_preserved(monkeypatch):
    """각 평가 노드의 확정 본문이 보고서에 그대로 포함되어야 한다."""

    monkeypatch.setattr(
        report_module,
        "model_name",
        lambda: "test-model",
    )

    result = report_module.report(_state())

    markdown = result["report"]

    assert "기술 조사 본문" in markdown
    assert "TRL 평가 본문" in markdown
    assert "시장성 평가 본문" in markdown
    assert "이해관계자 평가 본문" in markdown
    assert "도메인 평가 본문" in markdown


def test_report_trace_is_deterministic(monkeypatch):
    """보고서 생성 노드는 결정적 formatter로 기록되어야 한다."""

    monkeypatch.setattr(
        report_module,
        "model_name",
        lambda: "test-model",
    )

    result = report_module.report(_state())

    assert result["trace"][0]["node"] == "report"
    assert result["trace"][0]["deterministic"] is True