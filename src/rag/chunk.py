"""청킹 — 담당: R2 (설계서 §3)

토큰 400 / overlap 80, 임베딩 입력 한도 512 상한.
표는 행 단위로 쪼개되 매 조각에 헤더 행을 반복해 붙인다 — 표를 통째로 자르면
행이 어느 열에 속하는지 잃어버려 벤치마크 수치 인용이 망가진다.
"""

import hashlib

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from transformers import AutoTokenizer

from src.rag.embed import EMBED_MODEL
from src.settings import settings

ROWS_PER_TABLE_CHUNK = 12


def chunk_id(source: str, page: int, text: str) -> str:
    """(출처, 페이지, 본문 해시). 재실행해도 인용 ID 가 변하지 않아야 한다."""
    return hashlib.sha1(f"{source}|{page}|{text}".encode()).hexdigest()[:12]


def _splitter():
    lim = settings()["limits"]
    return RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        AutoTokenizer.from_pretrained(EMBED_MODEL),
        chunk_size=lim["chunk_tokens"],
        chunk_overlap=lim["chunk_overlap"],
    )


def table_chunks(tables: list, page: int) -> list[str]:
    """표를 헤더 반복 + 행 분할로 Markdown 조각 목록으로 만든다."""
    out = []
    for tbl in tables or []:
        rows = [[(c or "").strip() for c in r] for r in tbl if any(r)]
        if len(rows) < 2:
            continue
        header = "| " + " | ".join(rows[0]) + " |"
        sep = "|" + "---|" * len(rows[0])
        for i in range(1, len(rows), ROWS_PER_TABLE_CHUNK):
            body = "\n".join("| " + " | ".join(r) + " |" for r in rows[i : i + ROWS_PER_TABLE_CHUNK])
            out.append(f"(p{page} 표)\n{header}\n{sep}\n{body}")
    return out


def split(texts_by_page: list[tuple[int, str, list]], meta: dict) -> list[Document]:
    """[(페이지, 본문, 표)] 를 청크 Document 목록으로 만든다."""
    splitter, docs = _splitter(), []

    for page, body, tables in texts_by_page:
        pieces = [d.page_content for d in splitter.split_documents(
            [Document(page_content=body, metadata={})]
        )] if body.strip() else []
        pieces += table_chunks(tables, page)

        for text in pieces:
            docs.append(Document(
                page_content=text,
                metadata={**meta, "page": page, "chunk_id": chunk_id(meta["source"], page, text)},
            ))
    return docs
