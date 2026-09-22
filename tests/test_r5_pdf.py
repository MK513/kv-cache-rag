"""R5 pdf.py 단위 테스트."""

from pathlib import Path

from src.output import pdf


def _valid_markdown():
    return """# KV cache 최적화 기술 다관점 평가

## SUMMARY

핵심 평가 결과 요약입니다.

## 1. 분석 배경

테스트 본문입니다.

| 항목 | 내용 |
|---|---|
| 테스트 | 정상 |

## 6. 한계

테스트 한계입니다.

## REFERENCE

- 테스트 출처
"""


def test_validation_error_blocks_submission(tmp_path):
    """validation error가 있으면 제출본을 만들면 안 된다."""
    md = tmp_path / "report.md"
    md.write_text(_valid_markdown(), encoding="utf-8")

    result = pdf.build_submission(
        markdown_path=md,
        final_path=tmp_path / "final.pdf",
        validation_errors=[
            {
                "node": "domain_assessment",
                "kind": "Evidence 없음",
                "ids": ["evidence-missing"],
            }
        ],
    )

    assert result["generated"] is False
    assert result["reason"] == "validation_errors 존재"
    assert result["path"] is None


def test_missing_markdown_blocks_submission(tmp_path):
    """Markdown 파일이 없으면 제출본을 만들면 안 된다."""
    result = pdf.build_submission(
        markdown_path=tmp_path / "missing.md",
        final_path=tmp_path / "final.pdf",
        validation_errors=[],
    )

    assert result["generated"] is False
    assert "Markdown 파일 없음" in result["reason"]


def test_quality_checks_pass_for_basic_report():
    """기본 보고서 구조는 사전 품질 점검을 통과해야 한다."""
    checks = pdf.quality_checks(_valid_markdown())

    by_name = {
        check["check"]: check
        for check in checks
    }

    assert by_name["summary"]["passed"] is True
    assert by_name["korean"]["passed"] is True
    assert by_name["table"]["passed"] is True
    assert by_name["reference"]["passed"] is True


def test_reference_missing_fails():
    """REFERENCE가 없으면 품질 점검에서 실패해야 한다."""
    markdown = """# 보고서

## SUMMARY

테스트 요약입니다.

## 1. 분석 배경

본문입니다.
"""

    checks = pdf.quality_checks(markdown)

    reference_check = next(
        check
        for check in checks
        if check["check"] == "reference"
    )

    assert reference_check["passed"] is False