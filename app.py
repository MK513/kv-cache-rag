"""CLI 진입점 — 담당: R1

    uv run python app.py
    uv run python app.py --domain "데이터센터/클라우드 (대규모 동시성, 비용 민감)"
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from src.graph import build_graph
from src.rag import retrieve
from src.rag.index import build


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--domain", default="데이터센터/클라우드 (대규모 동시성, 비용 민감)")
    p.add_argument("--out", default="output")
    args = p.parse_args()

    load_dotenv(override=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    idx = build()          # 인덱스를 먼저 만들어 매니페스트를 State 에 넣는다
    final = build_graph().invoke({
        "domain": args.domain,
        "sources": idx["manifest"],
        "trace": [],
    })

    (out / "report.md").write_text(final["report"], encoding="utf-8")
    trace = final["trace"] + retrieve.TRACE   # 키워드 변환 기록 회수
    (out / "trace.jsonl").write_text(
        "\n".join(json.dumps(t, ensure_ascii=False) for t in trace), encoding="utf-8")

    if final.get("validation_errors"):
        print(f"⚠️ 인용 검증 잔여 오류 {len(final['validation_errors'])}건 — trace.jsonl 확인")
    print(f"완료: {out/'report.md'}  (PDF: uv run python -m src.output.pdf)")


if __name__ == "__main__":
    main()
