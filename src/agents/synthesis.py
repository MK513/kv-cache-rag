"""평가 종합 — 담당: R5 | State 읽기 전용

네 관점의 평가 결과를 종합한다.

원칙:
- 새로운 외부 근거를 검색하지 않는다.
- 앞선 평가 노드에서 확정된 내용만 재사용한다.
- 관점 간 차이나 상충을 억지로 만들지 않는다.
- 실제 차이가 확인되는 경우에만 conflicts 에 기록한다.
- TurboQuant + ITME 결합 효과는 공개 실측 근거가 없으면 가설로만 기록한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.llm import get_llm


PERSPECTIVES = [
    "maturity",
    "market",
    "stakeholder",
    "domain_assessment",
]


class Conflict(BaseModel):
    """관점에 따라 평가 내용이 실제로 달라지는 지점."""

    perspective: Literal["TRL", "시장성", "이해관계자", "도메인"]

    why: str = Field(
        description=(
            "어떤 근거 또는 조건 때문에 관점별 평가가 달라지는지 설명한다. "
            "기술 우열이나 종합 승자를 판정하지 않는다."
        )
    )


class Synthesis(BaseModel):
    agreements: list[str] = Field(
        default_factory=list,
        description="여러 관점에서 공통적으로 확인되는 사실 또는 방향"
    )

    conflicts: list[Conflict] = Field(
        default_factory=list,
        description=(
            "관점별 평가가 실제로 달라지는 지점. "
            "차이가 확인되지 않으면 빈 목록을 허용한다."
        )
    )

    gaps: list[str] = Field(
        default_factory=list,
        description="관점별 근거 공백과 아직 실증되지 않은 항목"
    )

    combination_hypothesis: str = Field(
        description=(
            "TurboQuant와 ITME의 결합 가능성에 대한 가설. "
            "공개 결합 실험이 확인되지 않았다면 실측 결과가 아닌 추론임을 명시한다."
        )
    )


PROMPT = """너는 평가 종합 담당이다.

TRL, 시장성, 이해관계자, 도메인 적용 평가 결과를 종합한다.

반드시 다음 원칙을 따른다.

1. 아래 입력에 포함된 내용만 사용한다.
2. 새로운 성능 수치, 시장 사례, 채택 사례, 실험 결과를 만들어내지 않는다.
3. 특정 기술을 종합 승자 또는 더 우수한 기술로 판정하지 않는다.
4. agreements에는 여러 관점에서 공통적으로 확인되는 내용을 적는다.
5. conflicts에는 실제로 평가가 달라지는 경우만 적는다.
   차이가 없다면 빈 목록으로 둔다.
6. 상충이나 차이를 보고서 목적을 위해 억지로 만들어내지 않는다.
7. 각 관점에서 근거가 없다고 판단한 내용은 gaps에 유지한다.
8. TurboQuant와 ITME를 동일 시스템에서 함께 측정한 공개 실험이
   확인되지 않았다면 결합 효과는 실측 결과처럼 쓰지 않는다.
9. combination_hypothesis는 개별 기술의 확인된 특성으로부터 도출한
   가설임을 명확히 표현한다.
{fix}

# TRL 평가
{maturity}

# 시장성 평가
{market}

# 이해관계자 평가
{stakeholder}

# 도메인 적용 평가
{domain_assessment}

# 기술 조사 상태
{tech_status}

# 기존 근거 공백
{gaps}
"""


FIX = """
# 재작성 지시

앞선 검증 단계에서 아래 오류가 확인되었다.

{errors}

오류가 있는 근거에 의존한 내용을 확정 사실처럼 사용하지 않는다.
근거가 남지 않는 항목은 삭제하거나 근거 공백으로 유지한다.
"""


def synthesis(state) -> dict:
    errors = state.get("validation_errors") or []

    # 각 평가 노드가 기록한 gap과 기술 조사 단계의 gap을 병합한다.
    merged_gaps = sorted(
        {
            gap
            for perspective in PERSPECTIVES
            for gap in (state.get(perspective, {}).get("gaps", []) or [])
        }
        | set(state.get("gaps") or [])
    )

    llm = get_llm().with_structured_output(Synthesis)

    result = llm.invoke(
        PROMPT.format(
            fix=FIX.format(errors=errors) if errors else "",
            tech_status=state.get("tech_status", {}),
            gaps=merged_gaps,
            **{
                perspective: state.get(perspective, {}).get("text", "(없음)")
                for perspective in PERSPECTIVES
            },
        )
    )

    # 앞 단계에서 확인된 gap이 synthesis 출력에서 누락되지 않도록 병합한다.
    result.gaps = sorted(set(result.gaps) | set(merged_gaps))

    return {
        "synthesis": result.model_dump(),
        "trace": [
            {
                "node": "synthesis",
                "agreements": len(result.agreements),
                "conflicts": len(result.conflicts),
                "gaps": len(result.gaps),
                "rewrite": bool(errors),
            }
        ],
    }