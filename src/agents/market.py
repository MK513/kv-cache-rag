"""시장성 평가 — 담당: R3 | ecosystem RAG (v13 변경: 웹 → 색인)

시장 근거는 논문 본문에 없다. 그래서 v7 은 이 노드를 RAG 에서 뺐지만,
v13 은 **근거를 ecosystem 컬렉션으로 색인해서** RAG 로 찾는다.
papers_core 를 조회하면 의미적으로 가까운 기술 서술이 시장 근거 자리에 놓이므로
이 노드는 collection="ecosystem" 만 조회한다.
"""

from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

INSTRUCTION = """너는 시장성 평가 담당이다. 세 항목을 각각 별도 절로 쓴다.

① 시장 규모/성장성 — 정량 리포트가 있으면 그대로 인용, 없으면 "미확인"
② 상용화/채택 현황 — **"발표됨" 과 "배포 확인됨" 을 반드시 구분한다.**
   발표 자료만 있으면 "발표 단계, 배포 확인 미확인" 으로 적는다.
③ 생태계 지지 — 지원 프레임워크, 표준화 동향
   **CXL 제품·PoC 근거와 ITME 채택 근거를 분리한다.** 삼성·SK하이닉스의 CXL
   제품이 존재한다는 사실은 ITME 아키텍처가 채택되었다는 근거가 아니다.
   해당 근거가 LLM 추론 워크로드 특화인지 개별 확인해 적고, 불명이면
   "일반 CXL 근거이며 추론 특화 여부 미확인" 으로 쓴다.

논문 성능 수치와 시장 매출을 섞지 않는다."""

QUERIES = [
    ("시장 규모 성장성 추론 메모리 비용", "both"),
    ("상용화 제품 출시 발표 실제 배포 채택", "both"),
    ("서빙 프레임워크 지원 현황 표준화 동향", "both"),
]


def market(state) -> dict:
    chunks, seen = [], set()
    for q, tech in QUERIES:
        for c in search_source_documents.invoke(
            {"query": q, "collection": "ecosystem", "technology": tech,
             "perspective": "market", "top_k": 5}
        ):
            if c["chunk_id"] not in seen:
                seen.add(c["chunk_id"])
                chunks.append(c)

    if not chunks:
        raise ValueError(
            "ecosystem 컬렉션에 근거가 없다. config/sources.yaml 의 ecosystem url 을 채울 것 "
            "(R3). 근거 0건으로 시장성을 생성하면 §5 '자료가 없으면 미확인' 원칙이 무너진다."
        )

    text = run_node(INSTRUCTION, state["domain"], format_chunks(chunks))
    return {"market": perspective(text, seen),
            "trace": [{"node": "market", "chunks": len(chunks)}]}
