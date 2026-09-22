"""하이브리드 검색 — 담당: R2 (설계서 §4)

dense 는 **원문 질의**, BM25 는 **영어 키워드 변환 질의** 를 쓴다.
두 retriever 에 서로 다른 질의를 넣어야 해서 RRF 를 직접 융합한다
(EnsembleRetriever 는 같은 질의를 양쪽에 넘긴다).

기술별 검색을 분리할 수 있게 technology 필터를 둔다 — 한 기술에만 근거가
몰리는 확증 편향을 막는다(설계서 §5).
"""

from src.rag.index import build
from src.rag.query_kw import translated
from src.settings import settings

TRACE: list[dict] = []   # 키워드 변환 기록. graph 가 회수해 State.trace 에 넣는다.


def _rrf(ranked_lists: list[tuple[list, float]], k: int) -> list:
    """Reciprocal Rank Fusion. score(d) = Σ weight / (k + rank)"""
    scores, seen = {}, {}
    for docs, weight in ranked_lists:
        for rank, d in enumerate(docs, 1):
            cid = d.metadata["chunk_id"]
            seen[cid] = d
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank)
    return [seen[c] for c in sorted(scores, key=scores.get, reverse=True)]


def search(query: str, collection: str, technology: str = "both",
           top_k: int | None = None, mode: str = "rrf") -> list[dict]:
    """mode: 'rrf'(기본) | 'dense'  — eval 에서 두 모드를 분리 비교한다."""
    cfg = settings()["retrieval"]
    top_k = top_k or cfg["top_k"]
    store = build()["stores"].get(collection)
    if not store:
        return []

    pool = top_k * 4
    dense_docs = store["dense"].similarity_search(query, k=pool)

    if mode == "dense":
        ranked = dense_docs
    else:
        kw, changed = translated(query)
        if changed:
            TRACE.append({"tool": "query_kw", "ko": query, "en": kw})
        ranked = _rrf(
            [(dense_docs, cfg["dense_weight"]), (store["bm25"].invoke(kw), cfg["sparse_weight"])],
            cfg["rrf_k"],
        )

    if technology != "both":
        ranked = [d for d in ranked if d.metadata.get("technology") in (technology, "")]

    return [
        {
            "chunk_id": d.metadata["chunk_id"],
            "collection": d.metadata["collection"],
            "source": d.metadata["source"],
            "technology": d.metadata.get("technology", ""),
            "page": d.metadata["page"],
            "text": d.page_content,
        }
        for d in ranked[:top_k]
    ]
