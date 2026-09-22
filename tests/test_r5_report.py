"""R5 report.py 단위 테스트."""

from src.agents import report as report_module


def _assessment(role, text, *, claim_id=None, status="completed"):
    """평가 노드가 반환하는 Assessment. 본문(text) 필드는 더 이상 없다."""
    return {
        "status": status,
        "claims": [{"claim_id": claim_id or f"claim-{role}", "text": text,
                    "technology": "ITME", "kind": "fact",
                    "evidence_ids": [f"e-{role}"]}],
        "evidence": [{"evidence_id": f"e-{role}", "source_id": f"s-{role}",
                      "run_id": "r5-test", "collection": "ecosystem", "quote": "합성 인용",
                      "location": "p.1", "allowed_uses": [role]}],
        "sources": [{"source_id": f"s-{role}", "run_id": "r5-test", "collection": "ecosystem",
                     "allowed_uses": [role], "title": f"{role} 출처",
                     "url": f"https://example.org/{role}", "published_at": "2026-01-01",
                     "retrieved_at": "2026-09-22"}],
        "gaps": [],
    }


def _state():
    return {
        "run_id": "r5-test",
        "run_config": {"domain": "데이터센터/클라우드", "started_at": "2026-09-22T00:00:00+00:00"},
        "research": _assessment("research", "기술 조사 본문"),
        "maturity": _assessment("maturity", "TRL 평가 본문"),
        "market": _assessment("market", "시장성 평가 본문"),
        "stakeholder": _assessment("stakeholder", "이해관계자 평가 본문"),
        "domain_assessment": _assessment("domain", "도메인 평가 본문"),
        "gaps": [{"role": "market", "technology": "TurboQuant", "item": "서빙 처리량",
                  "reason": "공개 자료 미확인"}],
        "synthesis": {
            "agreements": ["두 기술 모두 KV cache 메모리 병목을 완화한다."],
            "conflicts": [],
            "gaps": ["두 기술의 결합 실측 자료 미확인"],
            "combination_hypothesis": (
                "두 기술은 서로 다른 시스템 계층에서 병목을 완화할 가능성이 있다."
            ),
        },
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


def test_approved_claims_are_preserved(monkeypatch):
    """각 평가 노드의 승인된 Claim 본문이 보고서에 그대로 포함되어야 한다."""

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

def test_rejected_claim_leaves_the_body_and_becomes_a_gap(monkeypatch):
    """§8 — 부결된 주장은 사실 서술에서 제외하고 평가 보류 항목으로 옮긴다."""
    monkeypatch.setattr(report_module, "model_name", lambda: "test-model")

    state = _state() | {"validation": {
        "rejected_claims": ["claim-market"],
        "claim_verdicts": {"claim-market": {"verdict": "부결", "reviewer": "권예리",
                                            "comment": "인접 CXL 제품 자료임"}},
    }}
    markdown = report_module.report(state)["report"]

    assert "시장성 평가 본문" not in markdown
    assert "검토 부결: claim-market" in markdown and "권예리" in markdown
    # §9 — 본문에서 실제 사용한 자료만 REFERENCE 에 남는다.
    assert "market 출처" not in markdown
    assert "maturity 출처" in markdown


def test_gaps_and_survey_date_are_in_the_limits_section(monkeypatch):
    """§9 목차표 — 6 한계는 자료 공백·조사 시점·분석의 한계를 담는다."""
    monkeypatch.setattr(report_module, "model_name", lambda: "test-model")

    markdown = report_module.report(_state())["report"]
    limits = markdown[markdown.index("## 6. 한계"):]

    assert "TurboQuant 서빙 처리량: 공개 자료 미확인" in limits
    assert "2026-09-22T00:00:00+00:00" in limits
    assert "### 5.3 결합 가설" in markdown        # 5 절에는 공백 소절이 없다


def test_claim_kind_is_visible(monkeypatch):
    """§6 — 사실 주장과 추론을 구분해 적는다."""
    monkeypatch.setattr(report_module, "model_name", lambda: "test-model")

    state = _state()
    state["domain_assessment"]["claims"].append({
        "claim_id": "claim-hypo", "text": "결합 효과 가설", "technology": "both",
        "kind": "hypothesis", "evidence_ids": ["e-domain"],
        "explanation": "개별 근거에서 도출한 추론이다",
    })
    markdown = report_module.report(state)["report"]

    assert "**[사실 · ITME]**" in markdown
    assert "**[가설 · both]**" in markdown
    assert "> 전제: 개별 근거에서 도출한 추론이다" in markdown
