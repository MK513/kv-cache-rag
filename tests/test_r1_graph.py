"""그래프 4분기 경로 — 담당: R1

전 노드를 mock 으로 두고 그래프가 끝까지 도는지, 경로마다 run_status·trace·
runs/<run_id>/ 가 남는지 본다. 실제 LLM·검색은 타지 않는다.
"""

import json

import pytest

from src.graph import MergeConflict, build_graph, resume_state, setup as real_setup
from tests import mock_nodes


@pytest.fixture
def run(tmp_path):
    """mock 노드로 그래프를 돌리고 최종 State 와 실행 디렉토리를 돌려준다."""
    def go(*, start="setup", state=None, **mock_kw):
        initial = state or {"run_id": "r1-test", "trace": [],
                            "run_config": {"domain": "데이터센터/클라우드",
                                           "runs_dir": str(tmp_path)}}
        final = build_graph(start=start, **mock_nodes.all_nodes(**mock_kw)).invoke(initial)
        return final, tmp_path / initial["run_id"]
    return go


def test_completed_path(run):
    final, directory = run()
    assert final["run_status"] == "completed"
    assert final["report"].startswith("# 합성 보고서")
    assert [t["node"] for t in final["trace"]].count("collect_evidence") == 1
    assert {"setup", "research", "maturity", "market", "stakeholder",
            "domain_assessment", "final_check", "report"} <= {t["node"] for t in final["trace"]}
    assert directory.is_dir()


def test_four_nodes_citing_one_source_merge_into_one_entry(run):
    final, _ = run()
    registry = final["registry"]
    assert list(registry["sources"]) == ["web-mock-1"]
    assert list(registry["evidence"]) == ["e-web-mock-1"]


def test_merge_conflict_fails_the_run(run, tmp_path):
    """같은 evidence_id 에 다른 인용문이 오면 종합·검토로 넘기지 않는다."""
    with pytest.raises(MergeConflict, match="e-web-mock-1"):
        run(market={"quote": "다른 인용문"})
    errors = json.loads((tmp_path / "r1-test" / "merge-errors.json").read_text())["errors"]
    assert errors and "market" in errors[0]


def test_review_pending_saves_draft_and_stops(run):
    final, directory = run(review_status="pending")
    assert final["run_status"] == "partial"
    assert "report" not in final
    draft = json.loads((directory / "draft.json").read_text())
    assert draft["registry"]["sources"] and draft["review"]["review_status"] == "pending"


def test_resume_from_reviewed_draft(run, tmp_path):
    _, directory = run(review_status="pending")
    draft = resume_state("r1-test", tmp_path)
    draft["review"] = {"errors": [], "review_status": "passed", "round": 1}

    final, _ = run(start="final_check", state=draft)
    assert final["run_status"] == "completed" and final["report"]
    assert [t["node"] for t in final["trace"]] == ["final_check", "report"]


def test_unresolved_review_errors_fail(run):
    final, directory = run(review_errors=["인용 ID 없음"])
    assert final["run_status"] == "failed"
    assert "report" not in final
    assert directory.is_dir()


def test_failed_assessment_fails_the_run(run):
    final, _ = run(statuses={"stakeholder": "failed"})
    assert final["run_status"] == "failed"


def test_partial_assessment_still_reports(run):
    """근거 공백은 실패가 아니다. 보고서는 내고 run_status 로 표시한다."""
    final, _ = run(statuses={"market": "partial"})
    assert final["run_status"] == "partial" and final["report"]


def test_setup_requires_run_id_and_domain(tmp_path):
    graph = build_graph(**mock_nodes.all_nodes() | {"setup": real_setup})
    with pytest.raises(ValueError, match="run_id"):
        graph.invoke({"trace": [], "run_config": {"domain": "x", "runs_dir": str(tmp_path)}})
    with pytest.raises(ValueError, match="domain"):
        graph.invoke({"run_id": "r1-test", "trace": [], "run_config": {"runs_dir": str(tmp_path)}})
