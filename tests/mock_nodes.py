"""R4·R5 이행 전에 그래프를 끝까지 돌리기 위한 mock 노드 — 담당: R1

형식은 `src/schema.py` 계약 그대로다. LLM·검색을 타지 않으므로 실행 경로 검증에만 쓴다.
여기서 나오는 값은 합성 데이터이며 평가 결과가 아니다.
"""


def assessment(node, run_id, *, status="completed", source_id="web-mock-1", quote="합성 인용"):
    source = {"source_id": source_id, "run_id": run_id, "collection": "web",
              "allowed_uses": ["stakeholder"], "title": f"{source_id} 합성 출처",
              "url": f"https://example.org/{source_id}"}
    evidence = {"evidence_id": f"e-{source_id}", "source_id": source_id, "run_id": run_id,
                "collection": "web", "quote": quote, "location": "paragraph:1",
                "allowed_uses": ["stakeholder"]}
    claim = {"claim_id": f"claim-{node}", "text": f"{node} 합성 주장", "technology": "ITME",
             "kind": "fact", "evidence_ids": [evidence["evidence_id"]]}
    return {"claims": [] if status == "failed" else [claim],
            "sources": [source], "evidence": [evidence], "gaps": [], "status": status}


def node(name, **kw):
    """자기 필드 하나와 trace 만 쓰는 평가 노드."""
    def fn(state):
        return {name: assessment(name, state["run_id"], **kw),
                "trace": [{"node": name, "status": "ok"}]}
    return fn


def setup(state):
    from src.graph import run_dir
    run_dir(state).mkdir(parents=True, exist_ok=True)   # 실물 setup 과 같은 책임
    return {"sources_manifest": [{"id": "mock-paper", "collection": "papers_core"}],
            "trace": [{"node": "setup", "status": "ok"}]}


def synthesis(state):
    return {"synthesis": {"agreements": ["합성 일치"],
                          "conflicts": [{"perspective": "TRL", "favors": "판단보류", "why": "합성"}],
                          "gaps": [g["item"] for g in state["registry"]["gaps"]],
                          "combination_hypothesis": "합성 가설"},
            "trace": [{"node": "synthesis", "status": "ok"}]}


def review(status="passed", errors=()):
    def fn(state):
        return {"review": {"errors": list(errors), "review_status": status, "round": 1},
                "trace": [{"node": "review", "status": "ok"}]}
    return fn


def report(state):
    return {"report": f"# 합성 보고서\n\n인용 {len(state['registry']['evidence'])}건",
            "trace": [{"node": "report", "status": "ok"}]}


def all_nodes(*, statuses=None, review_status="passed", review_errors=(), **node_kw):
    """네 평가 노드 + 나머지를 한꺼번에 mock 으로 만든다."""
    statuses = statuses or {}
    nodes = {"setup": setup, "research": node("research"), "synthesis": synthesis,
             "review": review(review_status, review_errors), "report": report}
    for name in ("maturity", "market", "stakeholder", "domain_assessment"):
        nodes[name] = node(name, status=statuses.get(name, "completed"), **node_kw.get(name, {}))
    return nodes
