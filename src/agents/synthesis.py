"""평가 종합 — 담당: R5 | State 읽기 전용

우열 판정 금지를 프롬프트가 아니라 **출력 스키마** 로 강제한다.
conflicts 가 비면 구조 검증에서 걸려 보고서로 넘어가지 않는다.
validation_errors 를 받으면 해당 인용을 빼고 재작성한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.llm import get_llm

PERSPECTIVES = ["maturity", "market", "stakeholder", "domain_assessment"]


class Conflict(BaseModel):
    """한 관점에서 두 기술의 평가가 갈리는 지점."""

    perspective: Literal["TRL", "시장성", "이해관계자", "도메인"]
    favors: Literal["TurboQuant", "ITME", "판단보류"] = Field(
        description="이 관점의 근거가 상대적으로 유리하게 읽히는 쪽. 종합 승자가 아니다."
    )
    why: str = Field(description="관점이 갈리는 이유와 근거")


class Synthesis(BaseModel):
    agreements: list[str] = Field(description="네 관점이 공통으로 가리키는 사실")
    conflicts: list[Conflict] = Field(
        min_length=1, description="관점에 따라 평가가 갈리는 지점. 최소 1건 필수."
    )
    gaps: list[str] = Field(description="관점별 gaps 병합 + 결합 효과 등 실측 없는 항목")
    combination_hypothesis: str = Field(
        description="TurboQuant+ITME 결합 가설. 추론임을 명시. 본문이 아니라 시사점에만 쓴다."
    )


PROMPT = """너는 평가 종합 담당이다. 네 관점의 결과를 받아 정리한다.

- agreements: 관점들이 공통으로 가리키는 사실
- conflicts: **관점에 따라 평가가 갈리는 지점.** 이것이 이 보고서의 목적이다.
  종합 우승자를 뽑는 것이 아니라 왜 관점마다 답이 달라지는지를 쓴다.
- gaps: 각 관점이 보고한 근거 공백을 모두 병합한다. 두 기술을 동일 시스템에서
  함께 측정한 공개 자료가 확인되지 않으면 결합 효과를 반드시 여기 넣는다.
- combination_hypothesis: 결합 가설. ①②의 개별 근거에서 도출한 **추론**임을 명시한다.

"더 우수하다 / 승자" 같은 표현을 쓰지 않는다.
{fix}

#TRL 평가:
{maturity}

#시장성 평가:
{market}

#이해관계자 평가:
{stakeholder}

#도메인 적용 평가:
{domain_assessment}

#기술 조사 상태: {tech_status}
#관점별 근거 공백: {gaps}
"""

FIX = """
#재작성 지시: 아래 인용 오류가 보고되었다. 해당 ID 를 인용하지 말고,
근거가 남지 않는 문장은 삭제하거나 "미확인" 으로 바꿔 다시 작성하라.
{errors}
"""


def synthesis(state) -> dict:
    errors = state.get("validation_errors") or []
    llm = get_llm().with_structured_output(Synthesis)

    result = llm.invoke(PROMPT.format(
        fix=FIX.format(errors=errors) if errors else "",
        tech_status=state.get("tech_status", {}),
        gaps=sorted({g for p in PERSPECTIVES for g in state.get(p, {}).get("gaps", [])}
                    | set(state.get("gaps") or [])),
        **{p: state.get(p, {}).get("text", "(없음)") for p in PERSPECTIVES},
    ))

    return {
        "synthesis": result.model_dump(),
        "trace": [{"node": "synthesis", "conflicts": len(result.conflicts),
                   "rewrite": bool(errors)}],
    }
