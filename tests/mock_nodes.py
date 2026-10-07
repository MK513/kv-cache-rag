"""그래프를 끝까지 돌리기 위한 mock 노드 — 담당 A

형식은 `src/schema.py` 계약 그대로다. LLM·검색을 타지 않으므로 실행 경로 검증에만 쓴다.
여기서 나오는 값은 합성 데이터이며 평가 결과가 아니다.

관점 에이전트 4개는 그래프 노드가 아니라 Worker 가 부르는 함수다 — `workers()` 로 만들어
`build_graph(workers=...)` 에 넘긴다. 품질 평가는 담당 C 가 구현하기 전까지 계약 형식의
대역(`quality_eval`)을 쓴다.
"""


def assessment(node, run_id, *, status="completed", source_id="web-mock-1", quote="합성 인용"):
    source = {"source_id": source_id, "run_id": run_id, "collection": "web",
              "allowed_uses": ["stakeholder"], "title": f"{source_id} 합성 출처",
              "url": f"https://example.org/{source_id}"}
    evidence = {"evidence_id": f"e-{source_id}", "source_id": source_id, "run_id": run_id,
                "collection": "web", "quote": quote, "location": "paragraph:1",
                "allowed_uses": ["stakeholder"]}
    claims = [{"claim_id": f"claim-{node}-{tech.lower()}", "text": f"{node} {tech} 합성 주장",
               "technology": tech, "kind": "fact", "evidence_ids": [evidence["evidence_id"]]}
              for tech in ("TurboQuant", "ITME")]
    gaps = [] if status == "completed" else [
        {"role": "market" if node == "market" else "stakeholder", "technology": "TurboQuant",
         "item": "합성 공백", "reason": "합성 데이터라 근거가 없다"}]
    return {"claims": [] if status == "failed" else claims,
            "sources": [source], "evidence": [evidence], "gaps": gaps, "status": status}


def node(name, **kw):
    """자기 결과 키 하나와 trace 만 쓰는 평가 노드."""
    def fn(state):
        return {name: assessment(name, state["run_id"], **kw),
                "trace": [{"node": name, "status": "ok"}]}
    return fn


def setup(state):
    from src.graph import run_dir
    run_dir(state).mkdir(parents=True, exist_ok=True)   # 실물 setup 과 같은 책임
    return {"run_config": state["run_config"] | {"sources": [{"id": "mock-paper"}]},
            "trace": [{"node": "setup", "status": "ok"}]}


def synthesis(state):
    """종합. gaps 는 합류분에 자기 공백을 순차 병합한다(설계서 §7)."""
    version = ((state.get("synthesis") or {}).get("synthesis_version") or 0) + 1
    return {"synthesis": {"agreements": ["합성 일치"],
                          "conflicts": [{"perspective": "TRL", "why": "합성"}],
                          "gaps": [g["item"] for g in state["gaps"]],
                          "combination_hypothesis": "합성 가설",
                          "synthesis_version": version},
            "gaps": state["gaps"] + [{"role": "synthesis", "technology": "ITME",
                                      "item": "결합 실측", "reason": "공개된 결합 실험 없음"}],
            "trace": [{"node": "synthesis", "status": "ok"}]}


def quality_eval(next_node="publish", passed=True):
    """품질 평가 대역 — schema.QualityEval 형식. 담당 C 구현 전까지 그래프 경로 검증용."""
    def fn(state):
        return {"quality_eval": {"passed": passed, "next": next_node, "verdicts": {},
                                 "evaluated_report_version": state.get("report_version") or 1},
                "trace": [{"node": "quality_eval", "status": "ok"}]}
    return fn


def report(state):
    """보고서. 발행 전 버전 검사(versions.check_publish_guard)가 보는 버전 필드를 함께 쓴다."""
    version = (state.get("report_version") or 0) + 1
    return {"report": f"# 합성 보고서\n\n인용 {len(state['evidence_registry'])}건",
            "report_version": version,
            "report_manifest": {"report_version": version,
                                "based_on_synthesis_version": state["synthesis"]["synthesis_version"]},
            "trace": [{"node": "report", "status": "ok"}]}


def publish(state):
    from src.graph import run_dir
    path = run_dir(state) / "report.md"
    path.write_text(state["report"], encoding="utf-8")
    return {"report_paths": [str(path)], "trace": [{"node": "publish", "status": "ok"}]}


def workers(*, statuses=None, **node_kw):
    """Worker 가 부를 관점 에이전트 4개."""
    statuses = statuses or {}
    return {name: node(name, status=statuses.get(name, "completed"), **node_kw.get(name, {}))
            for name in ("maturity", "market", "stakeholder", "domain_assessment")}


def all_nodes(*, statuses=None, next_node="publish", **node_kw):
    """`build_graph(**all_nodes())` — 그래프 노드 mock 과 Worker 에이전트 mock 을 함께 만든다."""
    statuses = statuses or {}
    return {"setup": setup, "synthesis": synthesis, "report": report, "publish": publish,
            "quality_eval": quality_eval(next_node),
            "research": node("research", status=statuses.get("research", "completed")),
            "workers": workers(statuses=statuses, **node_kw)}
