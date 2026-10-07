"""publish guard — 담당 C (agent/ow-quality). 0단계 계약 스텁.

계약 (계획서 §7-2): `check_publish_guard(state) -> list[str]` 는 어긋난 단계 이름 목록을
돌려준다(빈 목록이면 통과).
- report_manifest.based_on_synthesis_version == synthesis.synthesis_version
- quality_eval.evaluated_report_version == report_version, PDF 경로·sha256 일치
구버전이면 해당 단계부터 다시 생성하고, 같은 버전 파일 손상은 실행 오류로 처리한다.
"""


def check_publish_guard(state) -> list[str]:
    raise NotImplementedError("check_publish_guard 는 담당 C 가 agent/ow-quality 에서 구현한다 (계획서 §7-2)")
