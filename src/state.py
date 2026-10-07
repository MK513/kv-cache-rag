"""State — 담당: R1 (설계서 §7 표 그대로)

§7 표는 14행이고 그중 세 행이 묶음 표기(`run_id / run_config`,
`validation / review_status`, `report / report_paths`)다. Python 필드로 풀어 17개다.

**병렬 구간에서는 각 평가 노드가 자기 결과 키만 쓴다.** 웹 자료는 전역에 쌓지 않고 각
Assessment 에 넣는다. 네 결과가 모두 도착하면 `collect_evidence` 가 출처와 근거를 한 번에
병합한다. 병렬 노드는 공용 gaps 나 레지스트리를 직접 수정하지 않는다.

`trace` 만 append reducer 를 쓴다. `gaps` 는 합류 후 수집하고 종합 후 순차 병합한다 —
병렬이 아니라 순서가 정해져 있어 reducer 가 필요 없다.

값은 전부 dict/list 다. 형식은 `src/schema.py` 가 정의하고 `collect_evidence` 가 검증한다.
"""

import operator
from pathlib import Path
from typing import Annotated, TypedDict


class ReportState(TypedDict, total=False):
    # ── 초기화 ──
    run_id: Annotated[str, "실행 식별자. runs/<run_id>/ 아래에 원문 스냅샷·결과를 남긴다"]
    run_config: Annotated[dict, "기술·모델·자료 버전·호출 한도. app.py 가 만들고 setup 이 자료 버전을 채운다"]

    # ── 기술 조사 + 병렬 평가: 각 노드가 자기 키에만 쓴다 ──
    research: Annotated[dict, "Assessment — 기술 조사 결과와 근거"]
    maturity: Annotated[dict, "Assessment — TRL 평가 결과"]
    market: Annotated[dict, "Assessment — 시장성 평가 결과"]
    stakeholder: Annotated[dict, "Assessment — 이해관계자 평가 결과"]
    domain_assessment: Annotated[dict, "Assessment — 도메인 평가 결과"]

    # ── 합류 ──
    source_registry: Annotated[dict, "{source_id: Source} — collect_evidence. 서지정보와 저장 위치"]
    evidence_registry: Annotated[dict, "{evidence_id: Evidence} — collect_evidence. 인용 구절과 원문 위치"]
    gaps: Annotated[list[dict], "[Gap] — 합류 후 수집, 종합 후 순차 병합"]

    # ── 종합 · 검증 ──
    synthesis: Annotated[dict, "Synthesis — 일치·차이·가설과 주장별 근거 연결"]
    validation: Annotated[dict, "검증 단계. 오류와 내용 검토 결과"]
    review_status: Annotated[str, "검증 단계. pending 이면 검토용 초안만 저장한다"]

    # ── 제어 · 출력 ──
    run_status: Annotated[str, "제어 단계. running / partial / completed / failed"]
    report: Annotated[str, "출력 단계. Markdown 본문"]
    report_paths: Annotated[list[str], "출력 단계. Markdown·PDF 저장 경로"]

    # ── 누적 ──
    trace: Annotated[list[dict], operator.add]

    # ══ Orchestrator-Workers 계약 (계획서 §6) ══════════════════════════════════
    # 0단계 계약 커밋에서 필드만 정의한다. 위의 기존 필드(validation·review_status 등)는
    # 각 레인이 새 흐름으로 옮긴 뒤 정리한다.
    #
    # 쓰기 담당 (계획서 §6-1). 병렬로 실행되는 것은 worker 뿐이고, worker 는
    # worker_results·trace 에만 쓴다(둘 다 operator.add).

    # ── 제어: 계획·라우팅·종료·재개에 필요한 최소치 ──
    plan: Annotated[list[dict], "현재 라운드 Task 목록 (schema.Task). orchestrator"]
    retry_count: Annotated[int, "재계획 라운드 수 = 다음 Task 의 round. 상한 orchestrator.max_retry. orchestrator"]
    repair_count: Annotated[int, "synthesis·report 저비용 수리 횟수. 상한 orchestrator.max_repairs. quality_eval"]
    step_count: Annotated[int, "orchestrator·quality_eval 결정마다 +1. 상한 orchestrator.max_steps"]
    claim_flags: Annotated[dict, "{claim_id: {status: invalid|recheck, reason, by, at_report_version}}. quality_eval·apply_review"]
    last_decision: Annotated[dict, "최근 결정·사유 요약. 전문은 runs/<run_id>/decisions.jsonl"]
    last_error: Annotated[str, "가장 최근 실패 요약 (Worker 실패·Judge 실패·정합성 오류). collect_evidence"]
    stop_reason: Annotated[str, "정상 통과면 빈 값. 상한 도달·Judge 실패 등"]

    # ── Send 입력 전용 ──
    task: Annotated[dict, "Send(\"worker\", state | {\"task\": t}) 로 worker 에만 전달. 그래프 노드는 이 키를 반환하지 않는다"]

    # ── 페이로드: 작업 결과 ──
    worker_results: Annotated[list[dict], operator.add]   # schema.WorkerResult 누적. worker
    report_manifest: Annotated[dict, "schema.ReportManifest — 보고서에 실제로 표시된 것. report"]
    report_version: Annotated[int, "report 노드만 올린다"]
    quality_eval: Annotated[dict, "schema.QualityEval. quality_eval"]


def run_dir(state) -> Path:
    """실행 저장 루트 `runs/<run_id>/`. R3 의 웹 스냅샷도 이 아래에 쌓인다.

    State 에서 바로 나오는 값이라 여기 둔다. graph 에 두면 노드가 graph 를 import 하게 돼
    순환이 생긴다.
    """
    config = state.get("run_config") or {}
    return Path(config.get("runs_dir", "runs")) / state["run_id"]
