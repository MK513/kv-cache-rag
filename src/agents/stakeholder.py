"""이해관계자 평가 — 담당: R3 | 웹 검색 (색인하지 않음)"""

from src.agents.common import extract_gaps, run_node
from src.tools.web_search import format_signals, search_market_signals

INSTRUCTION = """너는 이해관계자 평가 담당이다. 네 주체를 각각 별도 절로 쓴다.
① 경쟁 기술 진영 — 경쟁사 반응, 대응 기술
② 도입 기업 — 채택 의견, 도입 시 장벽
③ 개발자 — 통합 난이도에 대한 실무 평가
④ 투자 업계 — 투자 동향, 애널리스트·미디어 평가

각 절에서 "확인 가능한 직접 반응" 과 "기술 조건에서 추론한 도입 장벽" 을
소제목으로 분리한다. 반응을 상상해 채우지 않는다.
공개 자료가 없는 절은 "미확인" 으로 남기고 근거 공백에 적는다.
인용은 <url> 을 그대로 문장 끝에 쓴다."""

QUERIES = [
    "KV cache compression vendor response competing approach",
    "CXL disaggregated memory adoption barrier enterprise deployment",
    "LLM serving KV cache quantization integration difficulty developer",
    "AI memory infrastructure investment analyst outlook",
]


def stakeholder(state) -> dict:
    signals, seen = [], set()
    for q in QUERIES:
        for s in search_market_signals.invoke({"query": q, "technology": "both", "top_k": 5}):
            if s["url"] not in seen:
                seen.add(s["url"])
                signals.append(s)

    text = run_node(INSTRUCTION, state["domain"], format_signals(signals))
    return {
        # 웹 인용은 chunk_id 가 아니라 url 이라 인용 ID 검증 대상이 아니다.
        "stakeholder": {"text": text, "citations": [s["url"] for s in signals],
                        "gaps": extract_gaps(text), "bad_citations": []},
        "web_sources": signals,
        "trace": [{"node": "stakeholder", "signals": len(signals)}],
    }
