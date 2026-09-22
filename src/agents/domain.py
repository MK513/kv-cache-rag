"""도메인 적용 평가 — 담당: R4 | papers_core RAG + context(배경용)"""

from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

INSTRUCTION = """너는 데이터센터/클라우드(대규모 동시성, 비용 민감) 적용성 평가 담당이다.

본문은 두 항목으로만 구성한다.
① TurboQuant 단독: 압축을 통한 배치 크기 확장, 정확도 손실 폭
② ITME 단독: 메모리 계층 확장을 통한 동시 세션 수용량, 전송 지연

각 벤치마크는 모델·하드웨어·부하 조건을 함께 인용한다. 서로 다른 실험의 배수를
직접 비교하지 않는다. **TurboQuant 의 처리량 수치가 근거에 없으면 만들지 말고
"처리량 근거 미확인" 으로 근거 공백에 적는다.**

collection=context 청크(scope=secondary, 서베이)는 배경 서술에만 인용한다.
성능 주장이나 벤치마크의 근거로 쓰지 않는다.
scope=comparison 문서(InfiniGen, PIM/CXL)는 계열 비교 맥락에만 쓰고
선정 기술의 성능 근거로 대체하지 않는다.

③ 두 기술의 결합 상호작용은 **본문에 쓰지 않는다.** 마지막에 "결합 가설" 문단으로
분리하고, ①②로부터 도출한 추론임을 명시한다. 실측 근거가 없으면 그렇게 적는다."""

QUERIES = ["처리량 배치 크기 동시 요청 처리", "지연 정확도 손실 비용 오버헤드"]


def domain_assessment(state) -> dict:
    chunks, seen = [], set()
    for tech in ["TurboQuant", "ITME"]:
        for q in QUERIES:
            for c in search_source_documents.invoke(
                {"query": q, "collection": "papers_core", "technology": tech,
                 "perspective": "domain", "top_k": 4}
            ):
                if c["chunk_id"] not in seen:
                    seen.add(c["chunk_id"])
                    chunks.append(c)

    for c in search_source_documents.invoke(
        {"query": "데이터센터 추론 동시성 비용 구조", "collection": "context",
         "perspective": "domain", "top_k": 3}
    ):
        if c["chunk_id"] not in seen:
            seen.add(c["chunk_id"])
            chunks.append(c)

    text = run_node(INSTRUCTION, state["domain"], format_chunks(chunks))
    return {"domain_assessment": perspective(text, seen),
            "trace": [{"node": "domain_assessment", "chunks": len(chunks)}]}
