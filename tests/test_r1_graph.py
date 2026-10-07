"""그래프 경로 — 담당 A (Orchestrator-Workers, 계획서 §4)

전 노드를 mock 으로 두고 그래프가 끝까지 도는지, 경로마다 trace·runs/<run_id>/ 가 남는지 본다.
실제 LLM·검색은 타지 않는다. Worker 동작(Fan-out·예외·ID)은 test_orchestrator_graph.py 가 본다.

사람 검토 대기·재개 경로는 Orchestrator-Workers 그래프에서 선택 단계(--human-review)로
다시 붙인다(A4). 그때 이 파일에 테스트를 새로 쓴다.
"""

import json

import pytest

from src.graph import MergeConflict, build_graph, invoke, setup as real_setup
from tests import mock_nodes

CONFIG = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"]}


@pytest.fixture
def run(tmp_path):
    """mock 노드로 그래프를 돌리고 최종 State 와 실행 디렉토리를 돌려준다."""
    def go(*, state=None, **mock_kw):
        initial = state or {"run_id": "r1-test", "trace": [],
                            "run_config": CONFIG | {"runs_dir": str(tmp_path)}}
        final = invoke(build_graph(**mock_nodes.all_nodes(**mock_kw)), initial)
        return final, tmp_path / initial["run_id"]
    return go


def test_completed_path(run):
    final, directory = run()
    assert final["report"].startswith("# 합성 보고서")
    assert final["report_paths"] == [str(directory / "report.md")]
    assert {"setup", "research", "worker", "collect_evidence", "synthesis", "report",
            "quality_eval", "publish"} == {t["node"] for t in final["trace"]}
    assert directory.is_dir()
    assert (directory / "decisions.jsonl").exists()          # 계획 결정은 외부 로그로


def test_five_assessments_merge_into_two_registries(run):
    """기술 조사 근거도 병합한다 — 참고문헌을 실제 인용에서 역으로 만들어야 한다(§9)."""
    final, _ = run()
    assert list(final["source_registry"]) == ["web-mock-1"]
    assert list(final["evidence_registry"]) == ["e-web-mock-1"]
    assert [g["role"] for g in final["gaps"]] == ["synthesis"]   # 종합 후 순차 병합


def test_perspective_assessments_are_written_back_to_state(run):
    """출력 계층은 state[역할] 로 읽는다. collect_evidence 가 Worker 결과를 그 키에 조립한다."""
    final, _ = run()
    for name in ("maturity", "market", "stakeholder", "domain_assessment"):
        claims = final[name]["claims"]
        assert {c["technology"] for c in claims} == {"TurboQuant", "ITME"}
        assert all(c["claim_id"].startswith(f"r0-{name}-both:") for c in claims)


def test_gaps_merge_sequentially(run):
    final, _ = run(statuses={"market": "partial"})
    assert [g["role"] for g in final["gaps"]] == ["market", "synthesis"]


def test_merge_conflict_fails_the_run(run, tmp_path):
    """같은 ID 에 다른 내용이 오면 종합으로 넘기지 않는다(§7)."""
    with pytest.raises(MergeConflict, match="e-web-mock-1"):
        run(market={"quote": "다른 인용문"})
    errors = json.loads((tmp_path / "r1-test" / "merge-errors.json").read_text())["errors"]
    assert errors and "market" in errors[0]


def test_perspective_without_evidence_is_a_gap_not_a_failure(run):
    """관점이 근거를 못 만들어도 실행은 이어진다 — 품질 평가가 재계획을 정한다."""
    final, _ = run(statuses={"stakeholder": "failed"})
    assert final["report"]
    kinds = {(g["role"], g["technology"], g.get("kind")) for g in final["gaps"]}
    assert ("stakeholder", "ITME", "evidence_gap") in kinds
    assert final["stakeholder"]["status"] == "partial"


def test_partial_assessment_still_reports(run):
    """근거 공백은 실패가 아니다. 보고서는 낸다(§7)."""
    final, _ = run(statuses={"market": "partial"})
    assert final["report"]


def test_setup_requires_run_id_domain_and_technologies(tmp_path):
    graph = build_graph(**mock_nodes.all_nodes() | {"setup": real_setup})
    base = {"trace": [], "run_config": CONFIG | {"runs_dir": str(tmp_path)}}
    with pytest.raises(ValueError, match="run_id"):
        invoke(graph, base | {"run_id": ""})
    with pytest.raises(ValueError, match="technologies"):
        invoke(graph, base | {"run_id": "r1-test",
                              "run_config": {"domain": "x", "runs_dir": str(tmp_path)}})


def test_corpus_hash_mismatch_stops_the_run(tmp_path):
    """설계서 §3 — 적재할 때 SHA-256 을 다시 확인한다. 조용히 넘어가면 안 된다."""
    import hashlib

    from src.graph import verify_corpus

    original = tmp_path / "paper.pdf"
    original.write_bytes(b"original")
    entry = {"id": "itme-paper", "local_path": str(original),
             "sha256": hashlib.sha256(b"original").hexdigest()}

    assert verify_corpus({"sources": [entry]}) == []

    original.write_bytes(b"upstream changed the file")
    assert "SHA-256 불일치" in verify_corpus({"sources": [entry]})[0]

    original.unlink()
    assert "원문이 없다" in verify_corpus({"sources": [entry]})[0]


def test_llm_cache_counts_hits_and_misses(tmp_path, monkeypatch):
    """재현성은 모델이 아니라 캐시가 담당한다. 적중 수를 세지 못하면 비용 기록이 거짓이 된다."""
    from langchain_core.outputs import Generation

    from src import llm

    monkeypatch.setattr(llm, "_cache", None)
    cache = llm.enable_cache(str(tmp_path / "llm.sqlite"))

    assert cache.lookup("같은 프롬프트", "모델") is None        # miss
    cache.update("같은 프롬프트", "모델", [Generation(text="응답")])
    assert cache.lookup("같은 프롬프트", "모델")[0].text == "응답"   # hit

    assert (cache.hits, cache.misses) == (1, 1)
    assert llm.llm_report() | {"cache_hits": 1, "cache_misses": 1} == llm.llm_report()


def test_run_records_token_usage_and_call_counts():
    """§6 — 실제 비용은 토큰 사용량과 실행 기록으로 확인한다."""
    from types import SimpleNamespace

    import app

    state = {"trace": [
        {"node": "research", "status": "ok", "attempt": 1},
        {"node": "stakeholder", "action": "draft_validation", "status": "failed", "attempt": 1},
        {"node": "stakeholder", "action": "draft_validation", "status": "ok", "attempt": 2},
        {"tool": "search_market_signals", "node": "stakeholder", "attempt": 1},
        {"node": "synthesis", "status": "ok", "attempt": 1},
        {"node": "report", "status": "ok", "attempt": 1},
    ]}
    usage = SimpleNamespace(successful_requests=6, prompt_tokens=41000,
                            completion_tokens=7120, total_tokens=48120, total_cost=0.3141)

    record = app._accounting(state, usage)

    assert record["node_events"] == {"research": 1, "stakeholder": 2, "synthesis": 1}
    assert record["tool_calls"] == 1          # tool 이 붙은 이벤트만 센다
    assert record["retries"] == 1             # attempt > 1
    assert record["llm_calls"] == 6 and record["total_tokens"] == 48120
    assert record["cost_usd"] == 0.3141
    assert "report" not in record["node_events"]   # 생성 단계가 아니다


def test_accounting_without_a_callback_still_records_the_trace():
    import app

    record = app._accounting({"trace": [{"node": "market", "attempt": 1}]}, None)
    assert record["node_events"] == {"market": 1} and "llm_calls" not in record
