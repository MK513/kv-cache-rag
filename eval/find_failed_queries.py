"""실패한 질의 패턴 확인 — 담당: R2

eval/retrieval_metrics.py(R4)는 집계 수치만 보여준다. 설계서 10절이 요구하는
"실패한 질의 패턴 목록"을 뽑으려면 어떤 문항이 top-5 안에서도 정답을 못 찾았는지
개별적으로 봐야 해서 별도 스크립트로 뺐다. eval/retrieval_metrics.py는 건드리지 않는다
(R4 소유 파일이라 병합 충돌을 피하기 위함).

    uv run python -m eval.find_failed_queries
"""

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from src.rag.retrieve import search

GOLDEN = Path("eval/goldenset.json")


def main():
    items = json.loads(GOLDEN.read_text(encoding="utf-8"))["items"]
    items = [i for i in items if not str(i.get("question", "")).startswith("TODO")]

    failed = []
    for it in items:
        hits = search(it["question"], collection="papers_core",
                      technology=it["technology"], top_k=5, mode="dense")
        found = any(h["source"] == it["gold_source"] and h["page"] == it["gold_page"] for h in hits)
        if not found:
            top_sources = [(h["source"], h["page"]) for h in hits[:3]]
            failed.append((it, top_sources))

    print(f"평가셋 {len(items)}문항 중 top-5 실패 {len(failed)}건\n")
    for it, top_sources in failed:
        print(f"[{it['id']}] ({it['lang']}/{it['technology']}) {it['question']}")
        print(f"  정답: {it['gold_source']} p{it['gold_page']}")
        print(f"  top-3 반환: {top_sources}")
        print()

    if not failed:
        print("top-5 실패 없음.")


if __name__ == "__main__":
    main()
