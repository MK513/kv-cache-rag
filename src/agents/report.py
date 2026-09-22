"""보고서 생성 — 담당: R5 | 결정적 포맷터

SUMMARY 만 LLM 으로 요약하고 나머지는 State 를 목차에 끼워 넣는다.
인용 검증은 output/validate.py 가 이미 끝낸 상태로 들어온다(판단 LLM 재호출 없음).
결합 가설은 §4 본문이 아니라 §5 시사점에만 배치한다.
"""

from langchain_core.output_parsers import StrOutputParser

from src.llm import get_llm, model_name
from src.output import reference

SUMMARY_PROMPT = """아래 종합 결과를 1/2쪽 이내로 요약하라.
개요가 아니라 **핵심 평가 결과** 를 쓴다. 우열을 판정하지 않고, 관점에 따라
평가가 어떻게 갈리는지를 중심으로 쓴다.

일치: {agreements}
상충: {conflicts}
공백: {gaps}
"""


def _bullets(items) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- 해당 없음"


def _status(tech_status: dict) -> str:
    if not tech_status or all(v == "ok" for v in tech_status.values()):
        return ""
    held = [t for t, v in tech_status.items() if v != "ok"]
    return f"\n> ⚠️ 원문 근거를 확보하지 못해 **평가 보류** 로 남긴 기술: {', '.join(held)}\n"


def report(state) -> dict:
    s = state["synthesis"]
    summary = (get_llm() | StrOutputParser()).invoke(SUMMARY_PROMPT.format(
        agreements=s["agreements"],
        conflicts=[f"{c['perspective']}: {c['why']}" for c in s["conflicts"]],
        gaps=s["gaps"],
    ))

    conflicts = "\n".join(
        f"- **{c['perspective']}** — 근거가 상대적으로 유리하게 읽히는 쪽: {c['favors']}\n  {c['why']}"
        for c in s["conflicts"]
    )

    md = f"""# KV cache 최적화 기술 다관점 평가

**대상 기술** TurboQuant (SW 압축) · ITME (HW 메모리 확장)
**적용 도메인** {state['domain']}
**생성 모델** {model_name()}
{_status(state.get('tech_status', {}))}
## SUMMARY

{summary}

## 1. 분석 배경

KV cache 는 재계산 낭비를 없애는 장치이지만, 문맥이 길어질수록 Key·Value 텐서가
토큰 수에 비례해 증가해 가속기 HBM 을 소진시킨다. 연산 병목이 메모리 병목으로 옮겨간 것이다.
SW 진영은 데이터를 작게 만들고(양자화·압축), HW 진영은 담을 공간을 넓힌다(메모리 계층 확장).
본 보고서는 두 접근이 TRL·시장성·이해관계자·도메인 네 관점에서 어떻게 다르게
평가되는지, 관점 간 근거가 어디서 일치하고 어디서 상충하는지를 정리한다.

## 2. 기술 선정

데이터센터/클라우드(대규모 동시성·비용 민감) 도메인을 먼저 확정한 뒤, 이 도메인을
원 논문이 직접 타깃으로 명시한 기술을 SW·HW 각 1건씩 Doc Pool 안에서 선정했다.

| 진영 | 기술 | 선정 이유 |
|---|---|---|
| SW | TurboQuant | KV cache 양자화로 저장량을 줄여 메모리 용량 제약 완화 가능성 평가 |
| HW | ITME | CXL-Hybrid 계층적 메모리 확장으로 용량·데이터 이동 문제 접근 |

두 기술은 상호 배타적 대안이 아니라 서로 다른 시스템 계층에서 동일 병목에
접근하는 기술로 다룬다. 결합 효과는 §5.4 의 가설로만 다룬다.

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

{_bullets(s['agreements'])}

### 5.2 관점 간 상충

{conflicts}

### 5.3 근거 공백 (gaps)

{_bullets(s['gaps'])}

### 5.4 결합 가설 (추론 — 실측 근거 아님)

{s.get('combination_hypothesis', '해당 없음')}

## 6. 한계

- 공개된 논문·백서·사례 자료에 기반하며 자체 실측 벤치마크가 아니다.
- TRL·시장성·이해관계자 평가는 논문 발표 시점과 실제 채택 시점의 시차를 반영하지
  못한 공개 정보 기반 추정이며, 최신 상용 배치 현황과 다를 수 있다.
- TurboQuant 와 ITME 의 결합 효과는 추론이며, 동일 시스템에서 함께 측정된 공개
  자료가 확인되지 않는 한 §5.3 의 gaps 로 남긴다.
- ITME 는 2026-06 공개된 최신 연구로 TRL 근거의 절대적 수준이 낮을 수 있다.
  CXL 표준 자체 / CXL 상용 제품 / ITME 구체 아키텍처의 성숙도를 분리해 다루었다.
- GPU 벤치마크 수치는 원 논문의 보고값이며 본 프로젝트가 직접 측정한 성능이 아니다.
- 인용 검증은 인용 ID 가 색인 원문에 실재하는지에 대한 결정적 검사이며, 문장 내용이
  그 근거에서 따라 나오는지까지 증명하지 않는다.
- 확증 편향 방지 조치: 기술별 검색량 균형, 잔여 비용·근거 공백 동시 보고,
  시장 근거를 papers_core 가 아닌 ecosystem 컬렉션에서만 조회, 결합 가설 본문 분리.

{reference.build(state.get('sources', []), state.get('web_sources', []))}
"""
    return {"report": md, "trace": [{"node": "report", "chars": len(md)}]}
