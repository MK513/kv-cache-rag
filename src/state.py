"""State 14필드 — 담당: R1 (설계서 §7)

**각 필드는 단독 쓰기 주체를 갖는다.** 병렬 fan-out 에서 두 노드가 같은 필드에 쓰면
LangGraph 가 `InvalidUpdateError` 를 낸다. 누적 reducer 는 `trace` 하나뿐이다.

네 평가 노드는 `registry` 에 쓰지 않는다 — Source·Evidence 병합은 `collect_evidence`
단독 책임이다. 그래서 동시 쓰기가 구조적으로 불가능하다.

값은 전부 dict/list 다. 형식은 `src/schema.py` 가 정의하고 `collect_evidence` 가 한 번
검증한다. 노드마다 model_validate 를 부르지 않는다.
"""

import operator
from typing import Annotated, TypedDict


class ReportState(TypedDict, total=False):
    # ── 실행 식별 · 설정 (app.py 초기화) ──
    run_id: Annotated[str, "실행 식별자. 저장 루트 runs/<run_id>/ 와 웹 근거 소유자를 결정"]
    run_config: Annotated[dict, "domain / runs_dir / web_top_k / web_context_chars / web{한도}"]

    # ── 적재 ──
    sources_manifest: Annotated[list[dict], "색인 매니페스트 — setup 단독 쓰기"]

    # ── 기술 조사 + 병렬 평가: 각 노드가 자기 필드에만 쓴다 (전부 Assessment) ──
    research: Annotated[dict, "기술 조사 — papers_core"]
    maturity: Annotated[dict, "TRL — papers_core"]
    market: Annotated[dict, "시장성 — ecosystem"]
    stakeholder: Annotated[dict, "이해관계자 — 웹 (R3)"]
    domain_assessment: Annotated[dict, "도메인 적용 — papers_core + context"]

    # ── 병합 · 종합 · 검토 ──
    registry: Annotated[dict, "{sources: {id: Source}, evidence: {id: Evidence}, gaps: [Gap]}"
                              " — collect_evidence 단독 쓰기"]
    synthesis: Annotated[dict, "일치/상충/gaps/결합가설 — synthesis 단독 쓰기"]
    review: Annotated[dict, "{errors: [...], review_status, round} — 내용검토(R5) 단독 쓰기"]

    # ── 출력 ──
    report: Annotated[str, "최종 Markdown — report 단독 쓰기"]
    run_status: Annotated[str, "running | partial | completed | failed — final_check 판정"]

    # ── 누적 ──
    trace: Annotated[list[dict], operator.add]
