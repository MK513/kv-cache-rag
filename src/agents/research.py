"""기술 조사 — 담당: R4 | papers_core RAG | evidence 단일 쓰기 주체"""

from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

TECHS = ["TurboQuant", "ITME"]

INSTRUCTION = """너는 기술 조사 담당이다. 선정 기술 2건(TurboQuant, ITME)의 원문에서 추출한다.
- 접근 방식: 어느 시스템 계층에서 KV cache 병목을 다루는가
- 적용 범위: 어떤 모델/하드웨어/부하 조건에서 검증되었는가
- 한계: 원문이 스스로 밝힌 제약과 미해결 항목
기술별로 절을 나눈다. role=reference 문서(KIVI, InfiniGen)는 계열 비교에만 쓰고,
선정 기술의 주장 근거로 대체하지 않는다."""

QUERIES = ["KV 캐시 메모리 병목 접근 방식과 핵심 아이디어", "실험 설정, 한계, 적용 조건"]
WIDEN = " 벤치마크 처리량 정확도 손실 오버헤드"


def research(state) -> dict:
    """양 기술 근거를 모아 evidence 를 채운다. 보완 라운드에서는 질의를 넓힌다."""
    rnd = state.get("retrieval_round", 0)
    queries = QUERIES if rnd == 0 else [q + WIDEN for q in QUERIES]

    chunks, seen = [], set()
    for tech in TECHS:
        for q in queries:
            for c in search_source_documents.invoke(
                {"query": q, "collection": "papers_core", "technology": tech, "top_k": 5}
            ):
                if c["chunk_id"] not in seen:
                    seen.add(c["chunk_id"])
                    chunks.append(c)

    text = run_node(INSTRUCTION, state["domain"], format_chunks(chunks))
    p = perspective(text, seen)

    # 선정 기술(primary)의 근거가 있는지로 평가 보류를 판정한다.
    covered = {c["technology"] for c in chunks}
    status = {t: ("ok" if t in covered else "평가 보류") for t in TECHS}
    gaps = p["gaps"] + [f"{t}: 원문 근거 미확보" for t, s in status.items() if s != "ok"]

    return {
        "evidence": chunks,
        "research": {**p, "gaps": gaps},
        "tech_status": status,
        "gaps": gaps,
        "retrieval_round": rnd + 1,
        "trace": [{"node": "research", "round": rnd, "chunks": len(chunks), "status": status}],
    }
