"""그래프 배선 — 담당: R1 (설계서 §8)

    START → setup → research → [maturity ‖ market ‖ stakeholder ‖ domain_assessment]
          → collect_evidence → synthesis → review → final_check
                                                       ├ 통과      → report    → END
                                                       ├ 검토대기  → save_draft → END
                                                       └ 미해결오류 → fail      → END

**조건부 엣지는 `final_check` 하나뿐이다.** 근거 보완 재조사와 인용 수정 재작성은 노드
내부 루프로 내린다(R3 `stakeholder` 가 이미 그렇게 한다). 그래프에 되돌아오는 엣지를 두면
경로가 곱해져 4분기 재현이 불가능해진다.

병합 오류는 네 번째 경로이고, 그래프 분기가 아니라 `collect_evidence` 의 예외다.
근거가 서로 어긋난 채로 종합·검토에 LLM 을 태울 이유가 없다. `app.py` 가 받아서
run_status=failed 로 기록한다.
"""

import json
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.agents.stakeholder import stakeholder
from src.schema import Assessment
from src.state import ReportState
from src.tools.web_store import save_json, utcnow

FANOUT = ["maturity", "market", "stakeholder", "domain_assessment"]


class MergeConflict(Exception):
    """같은 ID 에 다른 내용이 왔다. 인용이 어느 원문을 가리키는지 알 수 없으므로 실행을 끝낸다."""


def run_dir(state) -> Path:
    """실행 저장 루트. R3 의 웹 스냅샷도 이 아래(runs/<run_id>/web/)에 쌓인다."""
    config = state.get("run_config") or {}
    return Path(config.get("runs_dir", "runs")) / state["run_id"]


def event(node, status="ok", **fields) -> dict:
    return dict(node=node, status=status, attempt=1, timestamp=utcnow(), **fields)


# ── R1 소유 노드 ────────────────────────────────────────────────────────────

def setup(state) -> dict:
    """설정확인/적재. run_id·run_config 를 확인하고 색인 매니페스트를 State 에 넣는다."""
    if not state.get("run_id"):
        raise ValueError("run_id 가 없다. app.py 가 실행마다 발급한다")
    config = state.get("run_config") or {}
    if not config.get("domain"):
        raise ValueError("run_config.domain 이 없다")
    run_dir(state).mkdir(parents=True, exist_ok=True)

    from src.rag.index import build   # 임베딩을 끌고 오므로 호출 시점에 import 한다
    manifest = build()["manifest"]
    return {"sources_manifest": manifest,
            "trace": [event("setup", sources=len(manifest), run_id=state["run_id"])]}


def collect_evidence(state) -> dict:
    """네 Assessment 의 Source·Evidence 를 ID 기준으로 병합한다. **검증은 여기서 한 번만.**

    같은 ID 에 다른 내용이 오면 병합 오류다. 어느 쪽이 맞는지 고를 근거가 없고,
    조용히 덮어쓰면 R5 의 원문 대조가 엉뚱한 출처를 가리킨다.
    """
    sources, evidence, gaps, errors = {}, {}, [], []
    for name in FANOUT:
        raw = state.get(name)
        if not raw:
            errors.append(f"{name}: Assessment 가 없다")
            continue
        try:
            assessment = Assessment.model_validate(raw).model_dump()
        except ValidationError as exc:
            errors.append(f"{name}: 구조 오류 — {exc.error_count()}건")
            continue
        for key, store, id_field in (("sources", sources, "source_id"),
                                     ("evidence", evidence, "evidence_id")):
            for item in assessment[key]:
                kept = store.setdefault(item[id_field], item)
                if kept != item:
                    errors.append(f"{name}: {item[id_field]} 가 기존 내용과 다르다")
        gaps.extend(assessment["gaps"])

    if errors:
        save_json(run_dir(state) / "merge-errors.json", {"errors": errors})
        raise MergeConflict("; ".join(errors))

    # ponytail: research 의 Assessment 는 병합하지 않는다(설계서 §8 은 fan-out 4개만 센다).
    #           research 인용을 R5 가 역참조해야 하면 FANOUT 앞에 "research" 를 붙인다.
    return {"registry": {"sources": sources, "evidence": evidence, "gaps": gaps},
            "trace": [event("collect_evidence", sources=len(sources),
                            evidence=len(evidence), gaps=len(gaps))]}


def final_check(state) -> dict:
    """run_status 를 판정한다. 라우팅은 이 값과 review 만 본다."""
    statuses = {state.get(name, {}).get("status") for name in FANOUT}
    review = state.get("review") or {}
    if "failed" in statuses or review.get("errors"):
        status = "failed"
    elif review.get("review_status") == "pending" or "partial" in statuses:
        status = "partial"      # 근거 공백이 있어도 보고서는 낸다. 검토 대기만 초안으로 멈춘다.
    else:
        status = "completed"
    return {"run_status": status,
            "trace": [event("final_check", status, review=review.get("review_status", ""))]}


def route_after_final(state) -> str:
    if state["run_status"] == "failed":
        return "fail"
    if (state.get("review") or {}).get("review_status") == "pending":
        return "save_draft"
    return "report"


def save_draft(state) -> dict:
    """검토 대기. State 를 통째로 남기고 멈춘다. 같은 run_id 로 `app.py --resume` 하면 재개한다."""
    draft = {k: v for k, v in state.items() if k != "trace"}
    save_json(run_dir(state) / "draft.json", draft)
    return {"trace": [event("save_draft", "partial", path=str(run_dir(state) / "draft.json"))]}


def fail(state) -> dict:
    """미해결 오류. report 를 쓰지 않는다 — 실패한 실행이 보고서 자리를 차지하면 안 된다."""
    errors = (state.get("review") or {}).get("errors", [])
    return {"trace": [event("fail", "failed", errors=errors)]}


# ── 아직 새 계약으로 이행하지 않은 노드 ─────────────────────────────────────

def _pending(name, owner):
    """구버전 관점 dict 를 반환하는 노드. 실물 대신 세워 두고 mock 으로 갈아끼운다."""
    def node(state):
        raise NotImplementedError(
            f"{name} 는 {owner} 가 schema.Assessment 계약으로 이행해야 한다. "
            f"build_graph({name}=...) 로 mock 을 주입해 실행한다")
    return node


DEFAULT_NODES = {
    "setup": setup,
    "research": _pending("research", "R4"),
    "maturity": _pending("maturity", "R4"),
    "market": _pending("market", "R4"),
    "stakeholder": stakeholder,          # R3 이행 완료
    "domain_assessment": _pending("domain_assessment", "R4"),
    "collect_evidence": collect_evidence,
    "synthesis": _pending("synthesis", "R5"),
    "review": _pending("review", "R5"),
    "final_check": final_check,
    "save_draft": save_draft,
    "fail": fail,
    "report": _pending("report", "R5"),
}


def build_graph(start="setup", **overrides):
    """노드 함수를 이름으로 갈아끼운다. R4·R5 이행 전에는 mock 을 주입해 전 경로를 돌린다.

    `start="final_check"` 는 재개용이다. 사람이 draft.json 의 `review` 를 고쳐 놓았으므로
    평가·종합을 다시 돌리지 않는다 — checkpointer 없이 재개 비용을 없애는 방법이다.
    """
    nodes = DEFAULT_NODES | overrides
    b = StateGraph(ReportState)
    for name, fn in nodes.items():
        b.add_node(name, fn)

    b.add_edge(START, start)
    b.add_edge("setup", "research")
    for name in FANOUT:
        b.add_edge("research", name)          # fan-out
    b.add_edge(FANOUT, "collect_evidence")    # 넷이 모두 끝나야 합류
    b.add_edge("collect_evidence", "synthesis")
    b.add_edge("synthesis", "review")
    b.add_edge("review", "final_check")
    b.add_conditional_edges("final_check", route_after_final,
                            ["report", "save_draft", "fail"])
    for name in ("report", "save_draft", "fail"):
        b.add_edge(name, END)
    return b.compile()


def resume_state(run_id, runs_dir="runs") -> dict:
    """save_draft 가 남긴 초안을 초기 State 로 되돌린다.

    사람은 이 draft.json 의 `review` 를 직접 고쳐 검토 결과를 넣는다
    (`review_status: "passed"` 또는 `errors` 추가). 그게 재개의 입력이다.
    """
    draft = json.loads((Path(runs_dir) / run_id / "draft.json").read_text(encoding="utf-8"))
    return draft | {"trace": []}
