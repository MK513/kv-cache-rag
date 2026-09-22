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


PERSPECTIVE_NODES = ["research", "maturity", "market", "stakeholder", "domain_assessment"]
KIND_LABEL = {"fact": "사실", "inference": "추론", "hypothesis": "가설"}


def _held(state: dict) -> str:
    """평가 보류를 남긴 관점이 있으면 머리말에 경고를 단다(§5 — 자료가 없는 항목은 평가 보류)."""
    held = [node for node in PERSPECTIVE_NODES
            if (state.get(node) or {}).get("status") in {"partial", "failed"}]
    if not held:
        return ""
    return ("\n> ⚠️ 근거를 확보하지 못해 **평가 보류** 항목을 남긴 관점: "
            f"{', '.join(held)}\n")


def _approved(state: dict) -> dict:
    """내용 검토에서 부결된 Claim 을 뺀 Assessment 를 만든다.

    §8 — 근거를 확보하지 못한 주장은 평가 보류 항목으로 옮기고 사실 서술에서 제외한다.
    원본 State 를 고치지 않는다. Assessment 의 단독 쓰기 주체는 각 평가 노드다.
    """
    rejected = set((state.get("validation") or {}).get("rejected_claims") or [])
    approved = {}
    for node in PERSPECTIVE_NODES:
        assessment = dict(state.get(node) or {})
        if assessment.get("claims"):
            assessment["claims"] = [claim for claim in assessment["claims"]
                                    if claim.get("claim_id") not in rejected]
        approved[node] = assessment
    return approved


def _claims(assessment: dict) -> str:
    """Claim 을 종류·대상 기술과 함께 본문으로 편다(§6 — 사실과 추론을 구분한다)."""
    claims = assessment.get("claims") or []
    if not claims:
        return "- 승인된 주장 없음. 근거 공백은 §6 을 참고한다."

    blocks = []
    for claim in claims:
        kind = KIND_LABEL.get(claim.get("kind", ""), claim.get("kind", ""))
        head = f"**[{kind} · {claim.get('technology', '')}]**"
        body = (claim.get("text") or "").strip()
        block = f"{head}\n{body}"
        if claim.get("explanation"):
            block += f"\n> 전제: {claim['explanation'].strip()}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _gap_lines(state: dict) -> list[str]:
    """§6 한계에 실을 근거 공백. 검토 부결 항목도 여기로 옮긴다(§8)."""
    lines = [f"{gap.get('technology', '')} {gap.get('item', '')}: {gap.get('reason', '')}".strip()
             for gap in (state.get("gaps") or [])]

    validation = state.get("validation") or {}
    verdicts = validation.get("claim_verdicts") or {}
    for claim_id in validation.get("rejected_claims") or []:
        verdict = verdicts.get(claim_id) or {}
        reviewer = verdict.get("reviewer") or "검토자 미상"
        comment = verdict.get("comment") or "사유 미기재"
        lines.append(f"검토 부결: {claim_id} (검토자: {reviewer} — {comment})")
    return lines


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
    """검증된 State 를 §9 목차에 맞춰 Markdown 으로 조립한다. 판단 LLM 을 부르지 않는다(§6)."""
    synthesis = state["synthesis"]
    config = state.get("run_config") or {}
    domain = config.get("domain", "데이터센터/클라우드")
    approved = _approved(state)

    summary = _summary(synthesis)
    conflicts = _conflicts(synthesis.get("conflicts") or [])

    md = f"""# KV cache 최적화 기술 다관점 평가

**대상 기술** TurboQuant (SW 압축) · ITME (HW 메모리 확장)
**적용 도메인** {domain}
**생성 모델** {model_name()}
{_held(state)}
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

두 기술은 상호 배타적인 대안으로 가정하지 않는다. 결합 효과는 §5.3의 가설로만
다룬다.

## 3. 기술 개요

{_claims(approved["research"])}

## 4. 관점별 평가

### 4.1 TRL (기술성숙도)

{_claims(approved["maturity"])}

### 4.2 시장성

{_claims(approved["market"])}

### 4.3 이해관계자

{_claims(approved["stakeholder"])}

### 4.4 도메인 적용 ({domain})

{_claims(approved["domain_assessment"])}

## 5. 시사점

### 5.1 관점 간 일치

{_bullets(synthesis.get('agreements') or [])}

### 5.2 관점 간 차이 및 상충

{conflicts}

### 5.3 결합 가설 (추론 — 실측 근거 아님)

{synthesis.get('combination_hypothesis', '해당 없음')}

## 6. 한계

**확인된 근거 공백**

{_bullets(_gap_lines(state))}

**조사 시점과 검토 범위**

- 조사 수행 시점: {config.get('started_at', '미상')}
- 근거 범위: 지정 Doc Pool 색인(papers_core·ecosystem·context)과 이번 실행에서 본문을
  확보한 웹 자료로 한정한다. 후보로만 조회한 자료는 근거로 쓰지 않는다.

**분석의 한계**

- 본 평가는 공개된 논문·백서·사례 자료를 기반으로 하며 자체 실측 벤치마크가 아니다.
- TRL·시장성·이해관계자 평가는 공개 정보 기반 추정이므로 실제 최신 상용 배치 현황과 차이가 있을 수 있다.
- TurboQuant와 ITME의 결합 효과는 동일 시스템에서 함께 측정된 공개 자료가 확인되지 않는 한 실측 결과가 아닌 가설로 다룬다.
- GPU 벤치마크 수치는 원 논문의 보고값이며 본 프로젝트가 직접 측정한 결과가 아니다.
- 논문마다 평가 모델·하드웨어·부하 조건이 다르므로 성능 개선 배수만으로 기술 간 우열을 단정하지 않는다.
- 자동 검증은 인용 ID의 존재 여부, 실행 범위, 허용 자료 범위 등을 결정적으로 검사한다.
- Claim과 인용 근거의 내용적 적합성은 내용 검토 워크시트에서 사람이 확인한다.
- 확증 편향을 줄이기 위해 기술별 검색량을 균형 있게 유지하고, 성능 향상뿐 아니라 잔여 비용과 근거 공백도 함께 기록한다.

{reference.build(state=approved)}
"""

    return {
        "report": md,
        "trace": [{
            "node": "report",
            "status": "ok",
            "attempt": 1,
            "chars": len(md),
            "claims": sum(len(a.get("claims") or []) for a in approved.values()),
            "deterministic": True,
        }],
    }
