"""Human Review 선택 단계 — 담당 A (계획서 §9)

- 켜면 품질 평가를 통과한 뒤 `human_review` 가 worksheet 를 만들고 그 뒤에서 멈춘다.
- 재개는 멈출 때 남긴 State(JSON)로 새 그래프에서 `apply_review` 부터 잇는다 — 프로세스가 바뀌어도 된다.
- 부결이 있으면 재종합 후 다시 검토에서 멈추고, 없으면 발행한다.
"""

import csv
import json

from src.graph import awaiting_review, build_graph, invoke, resume
from tests import mock_nodes

CONFIG = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"]}
RUN_ID = "review-test"


def graph():
    # 품질 평가 대역이 사람 검토로 보낸다(실제 quality_eval 은 run_config.human_review 를 보고 정한다).
    return build_graph(**mock_nodes.all_nodes(next_node="human_review"))


def paused(tmp_path):
    g = graph()
    state = {"run_id": RUN_ID, "trace": [],
             "run_config": CONFIG | {"runs_dir": str(tmp_path), "human_review": True}}
    return g, invoke(g, state)


def saved(state):
    """app.finish 가 state.json 에 남기는 것과 같은 모양 — JSON 왕복, trace 제외."""
    return json.loads(json.dumps({k: v for k, v in state.items() if k != "trace"})) | {"trace": []}


def fill(path, verdict_for):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for row in rows:
        row["review_result"], row["reviewer"] = verdict_for(row["claim_id"]), "검토자"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return [row["claim_id"] for row in rows]


def test_graph_pauses_after_human_review_with_a_worksheet(tmp_path):
    g, state = paused(tmp_path)

    assert awaiting_review(g, RUN_ID)
    assert "report_paths" not in state                            # 아직 발행하지 않았다
    assert [t["node"] for t in state["trace"]][-1] == "human_review"
    assert (tmp_path / RUN_ID / "review.csv").exists()
    last = json.loads((tmp_path / RUN_ID / "decisions.jsonl").read_text().splitlines()[-1])
    assert (last["node"], last["decision"]) == ("human_review", "apply_review")


def test_resume_without_rejection_publishes(tmp_path):
    _, state = paused(tmp_path)
    fill(tmp_path / RUN_ID / "review.csv", lambda cid: "확인")

    g = graph()                                                   # 새 프로세스처럼 새 그래프
    final = resume(g, saved(state))

    assert [t["node"] for t in final["trace"]] == ["apply_review", "publish"]
    assert final["report_paths"] and not awaiting_review(g, RUN_ID)


def test_rejection_resynthesizes_and_pauses_again(tmp_path):
    _, state = paused(tmp_path)
    claims = fill(tmp_path / RUN_ID / "review.csv",
                  lambda cid: "부결" if cid.startswith("r0-market-") else "확인")
    rejected = sorted({cid for cid in claims if cid.startswith("r0-market-")})
    assert rejected

    g = graph()
    final = resume(g, saved(state))

    assert [t["node"] for t in final["trace"]] == ["apply_review", "synthesis", "report",
                                                   "quality_eval", "human_review"]
    assert awaiting_review(g, RUN_ID)                             # 새 보고서로 다시 검토
    assert final["report_version"] == state["report_version"] + 1
    flags = {cid: f for cid, f in final["claim_flags"].items() if f["by"] == "human_review"}
    assert sorted(flags) == rejected and all(f["status"] == "invalid" for f in flags.values())


def test_resume_accepts_only_runs_awaiting_review(tmp_path, monkeypatch):
    import pytest

    import app

    monkeypatch.setitem(app.settings()["run"], "runs_dir", str(tmp_path / "runs"))
    directory = tmp_path / "runs" / RUN_ID
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")

    (directory / "run.json").write_text(json.dumps({"run_status": "completed"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        app.paused_state(RUN_ID)

    (directory / "run.json").write_text(json.dumps({"run_status": app.AWAITING}), encoding="utf-8")
    assert app.paused_state(RUN_ID) == {"run_id": RUN_ID, "trace": []}
