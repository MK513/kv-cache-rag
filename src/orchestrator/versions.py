"""publish guard — 담당 C (agent/ow-quality)

계약 (계획서 §7-2): `check_publish_guard(state) -> list[str]` 는 다시 만들어야 하는 단계 이름을
앞 단계부터 돌려준다(빈 목록이면 통과).

- `report_manifest.based_on_synthesis_version == synthesis.synthesis_version` — 아니면 "report"
- `quality_eval.evaluated_report_version == report_version`(manifest 도 같은 버전) — 아니면 "quality_eval"
- 같은 버전인데 평가한 PDF 의 sha256 이 다르면 **파일 손상**이라 다시 만들 대상이 아니라 실행 오류다
  (`PublishGuardError`). 무엇을 평가했는지 증명할 수 없는 PDF 를 제출본으로 내보내지 않는다.

`route_after_eval`(publish·human_review 로 가기 직전)과 `publish` 노드가 같은 함수를 쓴다.
"""

import hashlib
from pathlib import Path


class PublishGuardError(Exception):
    """같은 버전의 평가 산출물이 손상됐다. 재생성으로 덮지 않고 실행을 끝낸다."""


def sha256_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_publish_guard(state) -> list[str]:
    stale = []
    synthesis = state.get("synthesis") or {}
    manifest = state.get("report_manifest") or {}
    quality = state.get("quality_eval") or {}
    version = state.get("report_version")

    if "synthesis_version" in synthesis and \
            manifest.get("based_on_synthesis_version") != synthesis["synthesis_version"]:
        stale.append("report")
    if quality.get("evaluated_report_version") != version or manifest.get("report_version") != version:
        stale.append("quality_eval")
    if stale:
        return stale

    pdf_path = quality.get("pdf_path") or ""
    if pdf_path:
        if not Path(pdf_path).exists():
            raise PublishGuardError(f"평가한 PDF 가 없다: {pdf_path}")
        actual = sha256_of(pdf_path)
        if actual != quality.get("pdf_sha256"):
            raise PublishGuardError(f"평가한 PDF 가 바뀌었다(보고서 v{version}): {pdf_path}")
    return []
