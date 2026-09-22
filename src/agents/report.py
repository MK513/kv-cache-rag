"""보고서 생성 — 담당: R5 | 결정적 포맷터

추가 판단 LLM을 호출하지 않고, 이미 확정된 State 내용을
설계서 §9 목차에 맞춰 조립한다.

원칙:
- SUMMARY도 synthesis 결과만 사용해 결정적으로 생성한다.
- 결합 가설은 §4 본문이 아니라 §5 시사점에만 배치한다.
- conflicts는 실제로 존재할 때만 출력한다.
- 최종 REFERENCE는 reference.py가 실제 사용 근거를 기준으로 생성한다.
"""

from src.llm import model_name
from src.output import reference


def _bullets(items) -> str:
    """문자열 목록을 Markdown bullet로 변환한다."""
    return "\n".join(f"- {item}" for item in items) if items else "- 해당 없음"


def _status(tech_status: dict) -> str:
    """근거 미확보로 평가 보류된 기술이 있으면 경고 문구를 만든다."""
    if not tech_status or all(v == "ok" for v in tech_status.values()):
        return ""

    held = [
        tech
        for tech, status in tech_status.items()
        if status != "ok"
    ]

    return (
        "\n> ⚠️ 원문 근거를 확보하지 못해 "
        f"**평가 보류**로 남긴 기술: {', '.join(held)}\n"
    )


def _summary(synthesis: dict) -> str:
    """LLM 없이 synthesis의 확정 결과만 사용해 SUMMARY를 생성한다."""
    parts = []

    agreements = synthesis.get("agreements") or []
    conflicts = synthesis.get("conflicts") or []
    gaps = synthesis.get("gaps") or []

    if agreements:
        parts.append(
            "네 관점에서 공통적으로 확인된 사항은 다음과 같다.\n"
            + _bullets(agreements)
        )

    if conflicts:
        conflict_lines = [
            f"**{conflict['perspective']}** — {conflict['why']}"
            for conflict in conflicts
        ]

        parts.append(
            "관점별 평가가 실제로 달라진 지점은 다음과 같다.\n"
            + _bullets(conflict_lines)
        )

    if gaps:
        parts.append(
            "평가 과정에서 확인된 주요 근거 공백은 다음과 같다.\n"
            + _bullets(gaps)
        )

    if not parts:
        return "확정된 종합 평가 결과가 없다."

    return "\n\n".join(parts)


def _conflicts(conflicts: list[dict]) -> str:
    """관점별 차이를 Markdown으로 변환한다."""
    if not conflicts:
        return "- 확인된 관점 간 상충 없음"

    return "\n".join(
        f"- **{conflict['perspective']}** — {conflict['why']}"
        for conflict in conflicts
    )


def report(state) -> dict:
    """검증된 State를 최종 Markdown 보고서로 조립한다."""
    synthesis = state["synthesis"]

    summary = _summary(synthesis)
    conflicts = _conflicts(
        synthesis.get("conflicts") or []
    )

    md = f"""# KV cache 최적화 기술 다관점 평가

**대상 기술** TurboQuant (SW 압축) · ITME (HW 메모리 확장)  
**적용 도메인** {state['domain']}  
**생성 모델** {model_name()}
{_status(state.get('tech_status', {}))}
## SUMMARY

{summary}

## 1. 분석 배경

KV cache는 재계산 낭비를 줄이는 장치이지만, 문맥이 길어질수록 Key·Value 텐서가
토큰 수에 비례해 증가해 가속기 HBM을 소진시킨다. 연산 병목이 메모리 병목으로
이동하는 구조다.

본 보고서는 KV cache 데이터 축소 접근인 TurboQuant와 메모리 계층 확장 접근인
ITME를 대상으로, TRL·시장성·이해관계자·도메인 네 관점에서 평가한다.
두 기술의 개별 근거를 우선 검토하며, 동일 시스템에서 두 기술을 결합했을 때의
효과는 공개 실측 자료가 확인되지 않는 한 가설로 분리한다.

## 2. 기술 선정

데이터센터/클라우드 도메인을 먼저 확정한 뒤, 동일한 KV cache 메모리 병목에
서로 다른 시스템 계층에서 접근하는 기술을 각각 선정했다.

| 접근 | 기술 | 선정 이유 |
|---|---|---|
| KV cache 데이터 축소 | TurboQuant | KV cache 양자화를 통해 저장량과 메모리 사용량을 줄이는 접근 |
| 메모리 계층 확장 | ITME | CXL-Hybrid 기반 계층적 메모리 확장으로 용량과 데이터 이동 문제에 접근 |

두 기술은 상호 배타적인 대안으로 가정하지 않는다. 결합 효과는 §5.4의 가설로만
다룬다.

## 3. 기술 개요

{state['research']['text']}

## 4. 관점별 평가

### 4.1 TRL (기술성숙도)

{state['maturity']['text']}

### 4.2 시장성

{state['market']['text']}

### 4.3 이해관계자

{state['stakeholder']['text']}

### 4.4 도메인 적용 ({state['domain']})

{state['domain_assessment']['text']}

## 5. 시사점

### 5.1 관점 간 일치

{_bullets(synthesis.get('agreements') or [])}

### 5.2 관점 간 차이 및 상충

{conflicts}

### 5.3 근거 공백 (gaps)

{_bullets(synthesis.get('gaps') or [])}

### 5.4 결합 가설 (추론 — 실측 근거 아님)

{synthesis.get('combination_hypothesis', '해당 없음')}

## 6. 한계

- 본 평가는 공개된 논문·백서·사례 자료를 기반으로 하며 자체 실측 벤치마크가 아니다.
- TRL·시장성·이해관계자 평가는 공개 정보 기반 추정이므로 실제 최신 상용 배치 현황과 차이가 있을 수 있다.
- TurboQuant와 ITME의 결합 효과는 동일 시스템에서 함께 측정된 공개 자료가 확인되지 않는 한 실측 결과가 아닌 가설로 다룬다.
- GPU 벤치마크 수치는 원 논문의 보고값이며 본 프로젝트가 직접 측정한 결과가 아니다.
- 자동 검증은 인용 ID의 존재 여부, 실행 범위, 허용 자료 범위 등을 결정적으로 검사한다.
- Claim과 인용 근거의 내용적 적합성은 별도 내용 검토 워크시트에서 사람이 확인한다.
- 확증 편향을 줄이기 위해 기술별 검색량을 균형 있게 유지하고, 성능 향상뿐 아니라 잔여 비용과 근거 공백도 함께 기록한다.

{reference.build(
    manifest=state.get("sources", []),
    web_sources=state.get("web_sources", []),
    state=state,
)}
"""

    return {
        "report": md,
        "trace": [
            {
                "node": "report",
                "chars": len(md),
                "deterministic": True,
            }
        ],
    }