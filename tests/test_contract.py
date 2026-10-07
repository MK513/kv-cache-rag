"""0단계 계약 — 담당 A. 세 레인이 공유하는 정의가 서로 맞물리는지 확인한다.

계약 픽스처(tests/fixtures/contract/)는 읽기 전용이다. 각 레인은 이 형식을 기준으로
자기 테스트를 만든다. 계약이 바뀌면 이 테스트와 픽스처를 함께 고친다(담당 A, 계약 PR).
"""

import json
from pathlib import Path

import pytest

from src.agents.common import CITATION_RE, event, gaps_from_text
from src.observability import log_decision
from src.orchestrator import evaluator, versions
from src.schema import (ASSESSMENT_ROLES, PERSPECTIVES, TECHS, Assessment, Gap, GroundedSynthesis,
                        QualityEval, ReportManifest, Task, WorkerResult)
from src.settings import settings
from src.state import ReportState

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_role_constants():
    assert TECHS == ["TurboQuant", "ITME"]
    assert ASSESSMENT_ROLES == ["research"] + PERSPECTIVES
    # 관점별 Assessment 는 State 키로 읽는다(출력 계층이 state[역할] 로 읽음)
    for role in ASSESSMENT_ROLES:
        assert role in ReportState.__annotations__


def test_state_has_contract_fields():
    for key in ("plan", "retry_count", "repair_count", "step_count", "claim_flags", "last_decision",
                "last_error", "stop_reason", "task", "worker_results", "report_manifest",
                "report_version", "quality_eval", "trace"):
        assert key in ReportState.__annotations__, key


@pytest.mark.parametrize("name", ["worker_result_ok.json", "worker_result_failed.json"])
def test_worker_result_fixtures(name):
    result = WorkerResult.model_validate(load(name))
    assert result.round == result.task.round
    if result.assessment is not None:
        Assessment.model_validate(result.assessment)


def test_task_fixture():
    Task.model_validate(load("task.json"))


def test_worker_result_rules():
    task = load("task.json")
    with pytest.raises(ValueError):          # 실패 결과에 assessment 가 남으면 안 된다
        WorkerResult.model_validate({"task": task, "round": 0, "status": "failed",
                                     "assessment": {"status": "completed"}, "error_kind": "transient"})
    with pytest.raises(ValueError):          # round 는 task.round 와 같아야 한다
        WorkerResult.model_validate({"task": task, "round": 1, "status": "failed",
                                     "error_kind": "transient"})


def test_state_after_collect_fixture():
    state = load("state_after_collect.json")
    for role in ASSESSMENT_ROLES:
        Assessment.model_validate(state[role])
    for gap in state["gaps"]:
        assert Gap.model_validate(gap).kind in ("evidence_gap", "execution_gap", "invalid_evidence")
    for item in state["worker_results"]:
        WorkerResult.model_validate(item)


def test_state_after_report_fixture():
    state = load("state_after_report.json")
    synthesis = GroundedSynthesis.model_validate(state["synthesis"])
    manifest = ReportManifest.model_validate(state["report_manifest"])
    quality = QualityEval.model_validate(state["quality_eval"])
    assert manifest.based_on_synthesis_version == synthesis.synthesis_version
    assert quality.evaluated_report_version == state["report_version"]
    assert evaluator.route_after_eval(state) == quality.next


def test_gap_defaults_keep_existing_callers():
    gap = Gap(role="market", technology="ITME", item="x", reason="y")
    assert gap.kind == "evidence_gap" and gap.attempted_queries == []


def test_common_helpers():
    assert CITATION_RE.findall("a [0123456789ab] b") == ["0123456789ab"]
    gaps = gaps_from_text("본문\n근거 공백: ITME 채택 사례 | 두 기술 비교", role="market", reason="r")
    assert [(g.technology, g.item) for g in gaps] == [("ITME", "ITME 채택 사례"), ("both", "두 기술 비교")]
    assert gaps_from_text("근거 공백: 없음", role="market", reason="r") == []
    row = event("worker", "failed", task_id="t")
    assert row["node"] == "worker" and row["status"] == "failed" and row["task_id"] == "t"


def test_log_decision(tmp_path):
    state = {"run_id": "r", "run_config": {"runs_dir": str(tmp_path)}}
    summary = log_decision(state, "orchestrator", "dispatch", "최초 계획", tasks=3)
    line = json.loads((tmp_path / "r" / "decisions.jsonl").read_text(encoding="utf-8"))
    assert line["decision"] == "dispatch" and line["tasks"] == 3
    assert summary["reason"] == "최초 계획"


def test_lane_stubs_are_explicit():
    # C 레인(evaluator·versions)은 agent/ow-quality 에서 구현됐다. 남은 스텁은 A 의 worker 다.
    from src.agents.worker import worker
    with pytest.raises(NotImplementedError):
        worker({})
    assert versions.check_publish_guard(load("state_after_report.json")) == []


def test_settings_contract_sections():
    cfg = settings()
    assert cfg["llm"]["model"] == "gpt-4.1-mini"
    orch = cfg["orchestrator"]
    assert (orch["max_retry"], orch["max_workers"]) == (2, 8)
    for section in ("report", "quality"):
        assert section in cfg
