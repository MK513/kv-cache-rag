"""그래프 배선 — 담당 A (Orchestrator-Workers, 계획서 §4)

    setup → research → orchestrator ─(dispatch: Send × len(plan))→ worker ─→ collect_evidence
          → synthesis → report → quality_eval ─(route_after_eval)→ orchestrator | synthesis
                                                                  | report | publish → END

- **Worker 목록이 코드에 없다.** orchestrator 가 research 결과로 조사 계획(`plan`)을 세우고,
  `dispatch` 가 Task 마다 `Send("worker", ...)` 로 Worker 를 띄운다. Worker 수 = 계획의 길이.
- `worker` 노드 하나가 Task 의 관점으로 기존 에이전트를 고른다. 예외는 Worker 가 잡아 실패
  결과로 바꾼다 — 병렬 Worker 하나의 예외가 그래프를 멈추지 않는다(그래프 레벨 Fallback).
- `Send` 로 띄운 Worker 가 모두 끝나면 `collect_evidence` 가 한 번 실행된다(Fan-in).
  기존 병합 로직(`_merge_one`)은 그대로 쓰고, 입력만 누적된 Worker 결과에서 고른다.
- 품질 평가(`quality_eval`) 뒤 다음 노드는 `quality_eval["next"]` 가 정한다(담당 C).
- 계획이 비면(재계획할 대상 없음) `dispatch` 가 바로 `publish` 로 보낸다.

병합 오류는 분기가 아니라 `collect_evidence` 의 예외다. 같은 ID 에 다른 내용이 들어오면
어느 원문을 가리키는지 고를 근거가 없다(설계서 §7). 관점 결과가 비는 것은 오류가 아니라
근거 공백이다 — Worker 실패는 execution_gap 으로 남고 품질 평가가 재계획을 정한다.
"""

import hashlib
import json
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.agents.common import event
from src.agents.report import report
from src.agents.research import research
from src.agents.synthesis import synthesis
from src.agents.worker import make_worker
from src.orchestrator.dispatch import dispatch
from src.orchestrator.evaluator import quality_eval, route_after_eval
from src.orchestrator.planner import orchestrator
from src.orchestrator.selection import select_active_worker_results
from src.output import validate
from src.output.pdf import publish
from src.schema import ASSESSMENT_ROLES, PERSPECTIVES, Assessment
from src.settings import settings
from src.state import ReportState, run_dir
from src.tools.web_store import save_json


class MergeConflict(Exception):
    """같은 ID 에 다른 내용이 들어왔다. 인용이 어느 원문을 가리키는지 알 수 없어 실행을 끝낸다."""


# ── R1 소유 노드 ────────────────────────────────────────────────────────────

def verify_corpus(manifest: dict) -> list[str]:
    """적재 전에 원문 SHA-256 을 다시 확인한다(설계서 §3).

    원문이 바뀌면 청크가 바뀌고 chunk_id 가 전부 달라진다. 조용히 넘어가면 이전 보고서의
    인용이 가리키던 원문이 사라지고, 두 실행이 왜 다른지 알 수 없게 된다.
    """
    problems = []
    for entry in manifest.get("sources", []):
        local, expected = entry.get("local_path"), entry.get("sha256")
        if not local or not expected:
            continue
        path = Path(local)
        if not path.exists():
            problems.append(f"{entry['id']}: 원문이 없다 ({local})")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"{entry['id']}: SHA-256 불일치 ({local})\n"
                            f"    매니페스트 {expected}\n    실제       {actual}")
    return problems


def setup(state) -> dict:
    """설정 확인과 논문 적재. 적재한 자료 버전을 run_config 에 남긴다."""
    if not state.get("run_id"):
        raise ValueError("run_id 가 없다. app.py 가 실행마다 발급한다")
    config = state.get("run_config") or {}
    for key in ("domain", "technologies"):
        if not config.get(key):
            raise ValueError(f"run_config.{key} 가 없다")
    run_dir(state).mkdir(parents=True, exist_ok=True)

    from src.rag.index import build, load_manifest   # 임베딩을 끌고 오므로 호출 시점에 import

    problems = verify_corpus(load_manifest())
    if problems:
        for line in problems:
            print(f"[setup] ⚠ {line}")
        raise ValueError(
            f"원문 {len(problems)}건이 매니페스트와 다르다. 의도한 갱신이면 "
            "`uv run python scripts/prepare_sources.py --refresh` 로 매니페스트를 다시 만들고 "
            "goldenset 재라벨링을 검토할 것")

    manifest = build()["manifest"]
    return {"run_config": config | {"sources": manifest},
            "trace": [event("setup", sources=len(manifest), run_id=state["run_id"])]}


def _merge_one(store: dict, item: dict, id_field: str, node: str) -> list[str]:
    """ID 하나를 레지스트리에 합친다. 같은 ID 에 다른 내용이 오면 병합 오류다(§7).

    `allowed_uses` 만 예외로 합집합을 취한다. §7 은 ID 를 원문 URL·버전 또는 본문
    해시·위치로 만들라고 한다 — 즉 같은 ID 면 같은 원문이다. 반면 허용 용도는 원문의
    속성이 아니라 **사용 권한**이라, 같은 청크를 TRL 노드와 시장성 노드가 각각 인용하면
    자기 역할만 적어 온다. 이걸 내용 불일치로 보면 정상 실행이 전부 병합 오류가 된다.
    노드별 권한 검사는 각 Assessment 안에서 이미 끝났다(§8).
    """
    ident = item[id_field]
    kept = store.get(ident)
    if kept is None:
        store[ident] = dict(item)
        return []

    without_uses = {k: v for k, v in item.items() if k != "allowed_uses"}
    if {k: v for k, v in kept.items() if k != "allowed_uses"} != without_uses:
        return [f"{node}: {ident} 가 기존 내용과 다르다"]

    kept["allowed_uses"] = sorted({*(kept.get("allowed_uses") or []),
                                   *(item.get("allowed_uses") or [])})
    return []


def collect_evidence(state) -> dict:
    """Fan-in. 누적된 Worker 결과에서 지금 쓸 결과를 골라 관점별 Assessment 로 조립하고,
    출처와 근거를 한 번에 병합한다. **검증은 여기서 한 번만.**

    조립한 관점별 Assessment 는 State 의 `maturity`·`market`·`stakeholder`·`domain_assessment`
    키에 쓴다 — synthesis·report·reference·validate 가 지금처럼 `state[역할]` 로 읽는다.
    같은 ID 에 다른 내용이 오면 병합 오류다(§7). 관점이 비는 것은 Gap 이다.
    """
    selected = select_active_worker_results(state.get("worker_results") or [],
                                            state.get("claim_flags"))
    assessments = {"research": state.get("research")} | selected["assessments"]

    sources, evidence, gaps, errors = {}, {}, [], []
    for name in ASSESSMENT_ROLES:
        raw = assessments.get(name)
        if not raw:
            if name == "research":
                errors.append("research: Assessment 가 없다")   # 계획의 입력이라 빠지면 버그다
            continue
        try:
            assessment = Assessment.model_validate(raw).model_dump()
        except ValidationError as exc:
            errors.append(f"{name}: 구조 오류 — {exc.error_count()}건")
            continue
        for key, store, id_field in (("sources", sources, "source_id"),
                                     ("evidence", evidence, "evidence_id")):
            for item in assessment[key]:
                errors.extend(_merge_one(store, item, id_field, name))
        gaps.extend(assessment["gaps"])
    gaps.extend(selected["cell_gaps"])

    if errors:
        save_json(run_dir(state) / "merge-errors.json", {"errors": errors})
        raise MergeConflict("; ".join(errors))

    perspectives = {name: selected["assessments"][name]
                    for name in PERSPECTIVES if name in selected["assessments"]}
    # synthesis·report 가 아직 validation 을 읽는다(담당 C 가 옮기기 전까지 유지).
    checked = validate.check(state | perspectives)
    failed = selected["failed"]
    last_error = "; ".join(f"{r['task']['task_id']}: {r['error']}" for r in failed)
    return perspectives | {
        "source_registry": sources, "evidence_registry": evidence, "gaps": gaps,
        "validation": checked["validation"], "last_error": last_error,
        "trace": [event("collect_evidence", sources=len(sources), evidence=len(evidence),
                        gaps=len(gaps), failed_workers=len(failed))]}


DEFAULT_NODES = {
    "setup": setup,                      # A
    "research": research,                # B — 계획의 입력이 되는 선행 조사
    "orchestrator": orchestrator,        # A — 계획
    "collect_evidence": collect_evidence,     # A — Fan-in
    "synthesis": synthesis,              # C
    "report": report,                    # C
    "quality_eval": quality_eval,        # C — 품질 평가, 다음 노드 결정
    "publish": publish,                  # C
}


def build_graph(workers: dict | None = None, **overrides):
    """노드 함수를 이름으로 갈아끼운다. `workers` 는 Worker 가 부를 에이전트를 관점별로 바꾼다."""
    nodes = DEFAULT_NODES | {"worker": make_worker(workers)} | overrides
    b = StateGraph(ReportState)
    for name, fn in nodes.items():
        b.add_node(name, fn)

    b.add_edge(START, "setup")
    b.add_edge("setup", "research")
    b.add_edge("research", "orchestrator")
    b.add_conditional_edges("orchestrator", dispatch, ["worker", "publish"])
    b.add_edge("worker", "collect_evidence")      # Send 로 띄운 worker 가 전부 끝나야 실행된다
    b.add_edge("collect_evidence", "synthesis")
    b.add_edge("synthesis", "report")
    b.add_edge("report", "quality_eval")
    b.add_conditional_edges("quality_eval", route_after_eval,
                            ["orchestrator", "synthesis", "report", "publish"])
    b.add_edge("publish", END)
    return b.compile(checkpointer=InMemorySaver())


def invoke(graph, state):
    """실행 하나가 스레드 하나다. run_id 가 thread_id·LangSmith metadata 를 잇는 상관 키다."""
    run_id = state.get("run_id") or "unknown"
    return graph.invoke(state, config={
        "configurable": {"thread_id": run_id},
        "recursion_limit": settings()["orchestrator"]["recursion_limit"],
        "run_name": "kv-cache-orchestrator-workers",
        "tags": ["orchestrator-workers"],
        "metadata": {"run_id": run_id},
    })


def resume_state(run_id, runs_dir="runs") -> dict:
    """save_draft 가 남긴 초안을 초기 State 로 되돌린다.

    사람은 이 draft.json 의 `validation` · `review_status` 를 직접 고쳐 검토 결과를 넣는다.
    그게 재개의 입력이다. `review_status` 가 pending 이면 다시 초안 저장에서 멈춘다.
    """
    draft = json.loads((Path(runs_dir) / run_id / "draft.json").read_text(encoding="utf-8"))
    return draft | {"trace": []}
