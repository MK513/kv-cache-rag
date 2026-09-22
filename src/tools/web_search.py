"""웹 조사 도구 — 담당: R3 (설계서 §3 §6)

**이해관계자 평가 전용.** 시장성은 v13 에서 ecosystem 컬렉션 RAG 로 옮겨갔다.
조회 결과는 색인하지 않으므로 200쪽 가드 계산 대상이 아니다.
질의 해시로 스냅샷을 캐시해 커밋한다 — 실행마다 결과가 흔들리면 재현이 안 된다.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from langchain_core.tools import tool
from langchain_tavily import TavilySearch

CACHE_DIR = Path("data/web_cache")

_RULES = [
    (("arxiv.org", "usenix.org", "acm.org", "ieee"), "논문"),
    (("cxlconsortium", "jedec", "standard", "spec"), "표준"),
    (("newsroom", "/press", "/blog", "developer."), "제품발표"),
    (("gartner", "idc.com", "report", "outlook"), "리포트"),
    (("github.com", "issues", "discussions"), "개발자논의"),
]


def _classify(url: str, title: str) -> str:
    blob = f"{url} {title}".lower()
    for keys, label in _RULES:
        if any(k in blob for k in keys):
            return label
    return "뉴스" if any(k in blob for k in ("news", "times", "post")) else "미확인"


@tool
def search_market_signals(query: str, technology: str = "both", top_k: int = 5) -> list[dict]:
    """이해관계자 반응을 웹에서 조회한다(색인 대상 아님).

    Args:
        query: 검색 질의
        technology: TurboQuant | ITME | both
        top_k: 반환 개수
    """
    q = query if technology == "both" else f"{technology} {query}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{hashlib.sha1(f'{q}|{top_k}'.encode()).hexdigest()[:12]}.json"

    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    raw = TavilySearch(max_results=top_k).invoke({"query": q}).get("results", [])
    out = [{
        "url": r.get("url", ""),
        "title": r.get("title", ""),
        "published_at": r.get("published_date") or "미확인",
        "retrieved_at": date.today().isoformat(),
        "snippet": r.get("content", ""),
        "source_type": _classify(r.get("url", ""), r.get("title", "")),
    } for r in raw]
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def format_signals(signals: list[dict]) -> str:
    return "\n".join(
        f"<document><url>{s['url']}</url><title>{s['title']}</title>"
        f"<published_at>{s['published_at']}</published_at>"
        f"<source_type>{s['source_type']}</source_type>"
        f"<content>{s['snippet']}</content></document>"
        for s in signals
    )
