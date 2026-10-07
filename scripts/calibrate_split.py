"""사전 조사 임계값 τ·T 보정 — 담당 A (계획서 §4-1, A3)

원문과 색인이 있는 환경에서 돌린다(`scripts/prepare_sources.py` 이후).

    uv run python -m scripts.calibrate_split
    uv run python -m scripts.calibrate_split --tau 0.80 0.82 0.84 --T 2 3 4

후보 τ·T 조합마다 두 도메인에서 칸별 청크 수와 Task 수를 출력한다. 고르는 기준:
- 두 도메인에서 Task 수가 달라지는 조합 (Dynamic Fan-out 실증)
- 4~7 범위 안이고, 한 기술 청크 0 칸이 지나치게 많지 않은 조합
고른 값을 config/settings.yaml 의 orchestrator.split 에 적고, 출력은
docs/agent/lane-a-calibration.md 에 붙인다.
"""

import argparse

from src.orchestrator import planner

DOMAINS = ["데이터센터/클라우드 (대규모 동시성, 비용 민감)", "온디바이스/엣지 추론 (메모리 제약, 저전력)"]


def scores(domain: str) -> dict:
    """칸별 cosine_score 목록. τ 를 바꿔 가며 다시 검색하지 않도록 한 번만 모은다."""
    from src.rag.retrieve import ROLE_COLLECTIONS, search

    found = {}
    for perspective, role in planner.PROBE_ROLE.items():
        for tech in ("TurboQuant", "ITME"):
            query = planner.probe_query(perspective, tech, domain)
            best = {}
            for collection in ROLE_COLLECTIONS[role]:
                for hit in search(query, collection=collection, technology=tech,
                                  top_k=planner.PROBE_TOP_K, perspective=role):
                    best[hit["chunk_id"]] = max(best.get(hit["chunk_id"], -1), hit["cosine_score"])
            found[(perspective, tech)] = sorted(best.values(), reverse=True)
    return found


def counts_at(found: dict, tau: float) -> dict:
    return {cell: {"chunks": sum(s >= tau for s in values), "groups": 0, "queries": []}
            for cell, values in found.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tau", type=float, nargs="+", default=[0.78, 0.80, 0.82, 0.84, 0.86])
    parser.add_argument("--T", type=int, nargs="+", default=[2, 3, 4])
    args = parser.parse_args()

    collected = {domain: scores(domain) for domain in DOMAINS}
    print("| τ | T | " + " | ".join(f"Task 수 ({d.split()[0]})" for d in DOMAINS) + " | 다름 |")
    print("|---|---|" + "---|" * len(DOMAINS) + "---|")
    for tau in args.tau:
        for threshold in args.T:
            totals = []
            for domain in DOMAINS:
                found = counts_at(collected[domain], tau)
                original = planner._cfg
                planner._cfg = lambda: {"split": {"tau": tau, "T": threshold}}
                try:
                    specs, _ = planner.task_specs(found)
                finally:
                    planner._cfg = original
                totals.append(len(specs))
            print(f"| {tau} | {threshold} | " + " | ".join(map(str, totals))
                  + f" | {'예' if len(set(totals)) > 1 else ''} |")

    print("\n칸별 상위 점수 (도메인별 상위 5개)")
    for domain, found in collected.items():
        print(f"\n## {domain}")
        for (perspective, tech), values in found.items():
            print(f"- {perspective}:{tech} " + ", ".join(f"{v:.3f}" for v in values[:5]))


if __name__ == "__main__":
    main()
