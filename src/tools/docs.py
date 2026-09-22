"""색인 검색 도구 — 담당: R2 (설계서 §3 도구 스펙)"""

from langchain_core.tools import tool

from src.llm import get_llm
from src.rag.index import build
from src.rag.retrieve import search


@tool
def search_source_documents(query: str, collection: str, technology: str = "both",
                            top_k: int = 5) -> list[dict]:
    """색인된 컬렉션에서 근거 청크를 검색한다.

    Args:
        query: 검색 질의 (한국어 가능 — BM25 축은 자동으로 영어 키워드 변환)
        collection: papers_core | ecosystem | context  (필수)
        technology: TurboQuant | ITME | both
        top_k: 반환 개수
    """
    return search(query, collection=collection, technology=technology, top_k=top_k)


@tool
def summarize_evidence(chunk_ids: list[str]) -> dict:
    """주어진 chunk_id 원문만으로 요약한다. 원문에 없는 내용을 채우지 않는다."""
    chunks = build()["chunks"]
    hits = [chunks[c] for c in chunk_ids if c in chunks]
    if not hits:
        return {"summary": "", "citations": []}

    body = "\n\n".join(f"[{d.metadata['chunk_id']}] {d.page_content}" for d in hits)
    summary = get_llm().invoke(
        f"다음 원문 발췌만 사용해 사실을 요약하라. 원문에 없는 내용을 추가하지 마라.\n\n{body}"
    ).content
    return {"summary": summary,
            "citations": [d.metadata["chunk_id"] for d in hits]}


def format_chunks(chunks: list[dict]) -> str:
    """인용 ID 가 보이는 XML 로 근거를 만든다."""
    return "\n".join(
        f"<document><id>{c['chunk_id']}</id><collection>{c['collection']}</collection>"
        f"<source>{c['source']}</source><page>{c['page']}</page>"
        f"<technology>{c['technology']}</technology><content>{c['text']}</content></document>"
        for c in chunks
    )
