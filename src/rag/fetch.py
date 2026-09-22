"""수집 + 매니페스트 — 담당: R2 (설계서 §3)

papers_core(PDF) · ecosystem/context(웹 스냅샷)를 내려받아
SHA-256 · 수집일 · 쪽수 · 청크 수를 기록한다. 웹은 3,200자 = 1쪽으로 환산한다.
3 컬렉션 쪽수 합계가 상한을 넘으면 적재를 중단한다.
"""

import hashlib
import re
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen, urlretrieve

import yaml

from src.settings import settings

DATA_DIR = Path("data/docs")
COLLECTIONS = ("papers_core", "ecosystem", "context")
UA = {"User-Agent": "Mozilla/5.0 (kv-cache-rag; research use)"}


def load_config(path: str = "config/sources.yaml") -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {c: cfg.get(c) or [] for c in COLLECTIONS}


def _strip_html(raw: str) -> str:
    """탐색·스크립트 요소를 제거하고 본문 텍스트만 남긴다."""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", raw)).strip()


def fetch(item: dict, collection: str) -> tuple[Path, str]:
    """문서를 내려받아 (로컬 경로, SHA-256) 을 돌려준다. 이미 있으면 재사용한다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_pdf = item.get("kind", "pdf") == "pdf"
    path = DATA_DIR / f"{collection}__{item['id']}{'.pdf' if is_pdf else '.txt'}"

    if not path.exists():
        if not item.get("url"):
            raise ValueError(f"{item['id']}: url 미확정 — config/sources.yaml 을 먼저 채울 것")
        print(f"[fetch] {collection}/{item['id']}")
        if is_pdf:
            urlretrieve(item["url"], path)
        else:
            with urlopen(Request(item["url"], headers=UA), timeout=30) as r:
                path.write_text(_strip_html(r.read().decode("utf-8", "ignore")), encoding="utf-8")

    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def web_pages(text: str) -> int:
    """웹 문서 쪽수 환산. 200쪽 가드에 함께 들어간다."""
    per = settings()["limits"]["web_chars_per_page"]
    return max(1, -(-len(text) // per))


def manifest_row(item: dict, collection: str, sha: str, pages: int, chunks: int) -> dict:
    return {
        "id": item["id"],
        "collection": collection,
        "title": item.get("title") or item.get("topic", ""),
        "venue": item.get("venue", "웹 자료"),
        "url": item.get("url", ""),
        "published": item.get("published", "미확인"),
        "retrieved_at": date.today().isoformat(),
        "sha256": sha,
        "pages": pages,
        "chunks": chunks,
        "role": item.get("role", ""),
    }
