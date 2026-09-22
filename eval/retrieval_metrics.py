"""검색 품질 실측 — 담당: R4 (설계서 §4)

설계서 §4 의 "측정 예정(구현 단계)" 칸을 이 출력으로 채운다.
언어별(ko/en) × 모드별(dense/rrf) 로 분리 보고하고, 같은 사실의 한/영 쌍이
모두 회수된 비율을 '완전 회수율' 로 따로 낸다.

    uv run python -m eval.retrieval_metrics
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from src.rag.retrieve import search

GOLDEN = Path("eval/goldenset.json")
KS = (1, 3, 5)
MIN_N = 12          # 셀당 표본이 이보다 적으면 경향만 본다는 경고를 낸다
MODES = ("dense", "rrf")


def rank_of(item: dict, mode: str) -> int | None:
    hits = search(item["question"], collection="papers_core",
                  technology=item["technology"], top_k=max(KS), mode=mode)
    for i, h in enumerate(hits, 1):
        if h["source"] == item["gold_source"] and h["page"] == item["gold_page"]:
            return i
    return None


def score(ranks: list[int | None]) -> dict:
    n = len(ranks) or 1
    out = {}
    for k in KS:
        out[f"Hit@{k}"] = sum(r is not None and r <= k for r in ranks) / n
        out[f"MRR@{k}"] = sum(1 / r for r in ranks if r is not None and r <= k) / n
    return out


def main():
    if not GOLDEN.exists():
        sys.exit(f"{GOLDEN} 없음")
    items = [i for i in json.loads(GOLDEN.read_text(encoding="utf-8"))["items"]
             if not i["question"].startswith("TODO")]
    if not items:
        sys.exit(f"{GOLDEN} 의 items 가 아직 TODO 다. 원문에서 페이지를 확인해 먼저 채울 것.")

    print(f"평가셋 {len(items)}문항 "
          f"(ko {sum(i['lang']=='ko' for i in items)} / en {sum(i['lang']=='en' for i in items)}, "
          f"TurboQuant {sum(i['technology']=='TurboQuant' for i in items)} / "
          f"ITME {sum(i['technology']=='ITME' for i in items)})")

    for mode in MODES:
        ranks = {i["id"]: rank_of(i, mode) for i in items}
        print(f"\n── mode={mode} ──")

        for lang in ("전체", "ko", "en"):
            subset = [r for i, r in ((x, ranks[x["id"]]) for x in items)
                      if lang == "전체" or i["lang"] == lang]
            flag = "  ⚠ 표본 부족(경향만)" if len(subset) < MIN_N else ""
            m = score(subset)
            print(f"  {lang:4s} n={len(subset):3d}  "
                  + "  ".join(f"{k}={v:.3f}" for k, v in m.items()) + flag)

        # 완전 회수율: 같은 fact 의 ko/en 문항이 모두 top_k 안에 들어온 비율
        by_fact = defaultdict(list)
        for i in items:
            by_fact[i["fact_id"]].append(ranks[i["id"]])
        for k in KS:
            full = sum(all(r is not None and r <= k for r in rs) for rs in by_fact.values())
            print(f"  완전 회수율@{k} = {full/len(by_fact):.3f}  (fact {len(by_fact)}건)")


if __name__ == "__main__":
    main()
