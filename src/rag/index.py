"""컬렉션별 인덱스 구축 — 담당: R2 (설계서 §3 §4)

papers_core / ecosystem / context 세 컬렉션을 각각 dense(FAISS) + BM25 로 만든다.
200쪽 이하 소규모라 벡터 DB 서버 없이 매 실행 재구축한다(수십 초).
"""

from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PDFPlumberLoader

from src.rag import chunk as chunker
from src.rag.embed import get_embeddings
from src.rag.fetch import COLLECTIONS, fetch, load_config, manifest_row, web_pages
from src.settings import settings

_cache: dict = {}


def _pdf_pages(path) -> list[tuple[int, str, list]]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return [(i + 1, p.extract_text() or "", p.extract_tables()) for i, p in enumerate(pdf.pages)]


def build() -> dict:
    if _cache:
        return _cache

    cfg, max_pages = load_config(), settings()["limits"]["max_pages"]
    docs_by_col, manifest, total = {c: [] for c in COLLECTIONS}, [], 0
    pending: list[str] = []

    for collection in COLLECTIONS:
        for item in cfg[collection]:
            # url 미확정 항목은 건너뛴다 — R3 의 자료 선별을 기다리는 동안에도
            # R2 가 papers_core 파이프라인을 독립적으로 검증할 수 있어야 한다.
            if not item.get("url"):
                pending.append(f"{collection}/{item['id']}")
                continue
            path, sha = fetch(item, collection)

            if item.get("kind", "pdf") == "pdf":
                pages = _pdf_pages(path)
                n_pages = len(pages)
            else:
                text = path.read_text(encoding="utf-8")
                pages, n_pages = [(1, text, [])], web_pages(text)

            total += n_pages
            if total > max_pages:
                raise ValueError(f"색인 {total}쪽 > 상한 {max_pages}쪽. 적재 중단.")

            meta = {
                "collection": collection,
                "source": item["id"],
                "technology": item.get("technology", ""),
                "role": item.get("role", ""),
            }
            chunks = chunker.split(pages, meta)
            docs_by_col[collection] += chunks
            manifest.append(manifest_row(item, collection, sha, n_pages, len(chunks)))
            print(f"[index] {collection}/{item['id']}: {n_pages}쪽 / 청크 {len(chunks)}개")

    embeddings, top_k = get_embeddings(), settings()["retrieval"]["top_k"]
    stores = {}
    for collection, docs in docs_by_col.items():
        if not docs:
            continue
        bm25 = BM25Retriever.from_documents(docs)
        bm25.k = top_k * 2  # RRF 융합 전이라 넉넉히 뽑는다
        stores[collection] = {
            "dense": FAISS.from_documents(docs, embeddings),
            "bm25": bm25,
            "docs": docs,
        }

    print(f"[index] 합계 {total}쪽 (상한 {max_pages}쪽) / 청크 {sum(len(d) for d in docs_by_col.values())}개")
    if pending:
        print(f"[index] ⚠ url 미확정으로 건너뜀: {', '.join(pending)}")

    _cache.update(stores=stores, manifest=manifest, pending=pending,
                  chunks={d.metadata["chunk_id"]: d for docs in docs_by_col.values() for d in docs})
    return _cache
