"""실행 진입점 — 담당: R1

    uv run python app.py
    uv run python app.py --domain "데이터센터/클라우드 (대규모 동시성, 비용 민감)"
    uv run python app.py --resume 20260922-141233-a1b2c3   # 검토 대기 초안 재개

실행 하나가 `runs/<run_id>/` 하나를 소유한다. R3 의 웹 스냅샷도 그 아래에 쌓이므로
run_id 를 발급하는 것이 이 파일의 첫 번째 책임이다.

**R4·R5 가 새 Assessment 계약으로 이행하기 전에는 끝까지 돌지 않는다.** research 노드에서
NotImplementedError 로 멈추고 실패를 기록한다. 전 경로 실행은 `scripts/r1_smoke.py` 로 한다.
"""

import argparse
import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

# 설정·매니페스트·출력 경로가 전부 저장소 루트 기준 상대경로라 작업 디렉토리를 옮긴다.
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)


from dotenv import load_dotenv

from src.graph import MergeConflict, build_graph, invoke, resume_state, run_dir
from src.settings import settings
from src.tools.web_store import save_json, utcnow


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def start_run(state) -> Path:
    """실행 디렉토리를 만들고 running 을 먼저 기록한다. 중간에 죽어도 흔적이 남는다."""
    directory = run_dir(state)
    directory.mkdir(parents=True, exist_ok=True)
    save_json(directory / "run.json", {
        "run_id": state["run_id"], "run_config": state["run_config"],
        "run_status": "running", "started_at": utcnow(),
    })
    return directory


def finish(state, status, errors=()) -> str:
    """실행 결과를 runs/<run_id>/ 에 남긴다. 실패해도 남긴다 — 그게 증빙이다."""
    directory = run_dir(state)
    started = directory / "run.json"
    save_json(started, json.loads(started.read_text(encoding="utf-8")) | {
        "run_id": state["run_id"], "run_config": state.get("run_config", {}),
        "run_status": status, "ended_at": utcnow(), "errors": list(errors),
    })
    save_json(directory / "state.json", {k: v for k, v in state.items() if k != "trace"})
    with (directory / "trace.jsonl").open("a", encoding="utf-8") as f:   # 재개 시 이어 쓴다
        for row in state.get("trace", []):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--domain", default="데이터센터/클라우드 (대규모 동시성, 비용 민감)")
    p.add_argument("--resume", metavar="RUN_ID", help="검토 대기로 멈춘 실행의 draft.json 을 이어서 돌린다")
    p.add_argument("--run-id", help="run_id 를 직접 지정 (재현용)")
    p.add_argument("--carry-review", metavar="RUN_ID",
                   help="이전 실행의 검토 판정을 가져온다. 주장 문장과 근거가 완전히 같은 Claim 만 옮긴다")
    args = p.parse_args()

    load_dotenv(override=True)
    config = settings()["run"]

    if args.resume:
        # 부록 A 의 `검토 결과 반영 후 재개` — 내용 검토부터 이어 간다. 평가·종합은 다시 돌리지 않는다.
        state = resume_state(args.resume, config["runs_dir"])
        start = "review"
    else:
        state = {"run_id": args.run_id or new_run_id(), "run_status": "running", "trace": [],
                 "run_config": {"domain": args.domain, "model": settings()["llm"],
                                "limits": settings()["limits"], "started_at": utcnow(),
                                "carry_review_from": args.carry_review, **config}}
        start = "setup"

    directory = start_run(state)
    print(f"[run] {state['run_id']} · {directory}")

    try:
        final = invoke(build_graph(start=start), state)
    except MergeConflict as exc:
        # 근거가 서로 어긋났다. merge-errors.json 은 collect_evidence 가 이미 남겼다.
        print(f"실패: 근거 병합 오류 — {exc}")
        return finish(state, "failed", [str(exc)])
    except Exception as exc:
        finish(state, "failed", [repr(exc)])
        raise                       # 스택 트레이스를 삼키지 않는다

    status = finish(final, final.get("run_status", "failed"))
    pending = (final.get("validation") or {}).get("pending_claims") or []

    carried = (final.get("validation") or {}).get("carried_claims") or []
    if carried:
        print(f"이전 실행에서 판정을 가져온 Claim {len(carried)}건 "
              f"(주장·근거가 완전히 같은 것만)")

    if final.get("review_status") == "pending":
        # §8 — ID 대조만으로 자동 통과시키지 않는다. 사람이 주장과 근거를 나란히 본다.
        print(f"검토 대기 — Claim {len(pending)}건. {directory/'review.csv'} 의 "
              f"review_result(확인|부결)·reviewer 를 채운 뒤\n"
              f"  uv run python app.py --resume {state['run_id']}")
    else:
        print({
            "completed": f"완료: {final.get('report_paths') or directory}",
            "partial": f"부분 완료 — 평가 보류 항목이 남았다. 보고서: {final.get('report_paths') or directory}",
            "failed": f"실패: {directory/'run.json'} 확인",
        }[status])
    return status


if __name__ == "__main__":
    main()
