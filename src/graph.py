"""그래프 배선 — 담당: R1 (설계서 §8, v13)

  research ──(근거 공백? 최대 1회 질의 보완)──> research
      └─> maturity ‖ market ‖ stakeholder ‖ domain_assessment
              └─(fan-in)─> validate_eval ──(인용 오류)──> 평가 재실행
                              └──> synthesis ──> validate_final
                                        ↑                │
                              (1회 재작성)────────────────┘
                                                         └──> report ──> END

validate 를 두 지점에 건다. 앞쪽이 없으면 평가 노드가 만든 가짜 인용 ID 가
§4 본문에 그대로 실린다 — synthesis 재작성만으로는 고쳐지지 않는다.
"""

from langgraph.graph import END, START, StateGraph

from src.agents.domain import domain_assessment
from src.agents.market import market
from src.agents.maturity import maturity
from src.agents.report import report
from src.agents.research import research
from src.agents.stakeholder import stakeholder
from src.agents.synthesis import synthesis
from src.output import validate as validator
from src.settings import settings
from src.state import ReportState

EVAL_NODES = ["maturity", "market", "stakeholder", "domain_assessment"]


def route_after_research(state) -> list[str] | str:
    """양 기술 근거가 없으면 1회만 질의를 보완한다. 무한 반복하지 않는다."""
    limit = settings()["limits"]["retrieval_round"]
    holding = [t for t, v in (state.get("tech_status") or {}).items() if v != "ok"]
    if holding and state.get("retrieval_round", 0) < limit:
        return "research"
    # 보완 후에도 없으면 tech_status 에 '평가 보류' 로 남긴 채 평가로 넘어간다.
    return EVAL_NODES


def route_after_eval(state) -> list[str] | str:
    """평가 단계 인용 오류는 평가 노드 재실행으로만 고칠 수 있다."""
    if not state.get("validation_errors"):
        return "synthesis"
    if state.get("validation_round", 0) >= 1:   # 평가 재실행은 1회
        return "synthesis"                       # 오류를 State 에 남긴 채 진행
    # ponytail: 문제 노드만 고르지 않고 평가 4종을 통째로 재실행한다.
    #           LLM 4회 비용으로 분기 로직을 없앴다. 비용이 문제되면 선별 재실행으로 교체.
    return EVAL_NODES


def route_after_final(state) -> str:
    """종합 단계 인용 오류는 1회 재작성. 재실패하면 오류·trace 를 남기고 종료한다."""
    limit = settings()["limits"]["validation_round"]
    if not state.get("validation_errors"):
        return "report"
    if state.get("validation_round", 0) >= limit:
        return "abort"
    return "synthesis"


def abort(state) -> dict:
    """재시도 한도 초과. 실패 사실을 보고서 자리에 남기고 끝낸다(조용히 통과시키지 않는다)."""
    errors = state.get("validation_errors", [])
    return {
        "report": "# 생성 실패\n\n인용 검증이 재작성 후에도 통과하지 못했다.\n\n"
                  + "\n".join(f"- {e}" for e in errors),
        "trace": [{"node": "abort", "errors": errors}],
    }


def build_graph():
    b = StateGraph(ReportState)

    b.add_node("research", research)
    b.add_node("maturity", maturity)
    b.add_node("market", market)
    b.add_node("stakeholder", stakeholder)
    b.add_node("domain_assessment", domain_assessment)
    b.add_node("validate_eval", lambda s: validator.check(s, "post_eval"))
    b.add_node("synthesis", synthesis)
    b.add_node("validate_final", lambda s: validator.check(s, "post_synthesis"))
    b.add_node("report", report)
    b.add_node("abort", abort)

    b.add_edge(START, "research")
    b.add_conditional_edges("research", route_after_research, ["research"] + EVAL_NODES)
    b.add_edge(EVAL_NODES, "validate_eval")          # 네 노드가 모두 끝나야 합류
    b.add_conditional_edges("validate_eval", route_after_eval, EVAL_NODES + ["synthesis"])
    b.add_edge("synthesis", "validate_final")
    b.add_conditional_edges("validate_final", route_after_final,
                            ["synthesis", "report", "abort"])
    b.add_edge("report", END)
    b.add_edge("abort", END)

    return b.compile()
