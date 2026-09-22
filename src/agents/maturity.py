"""TRL 평가 — 담당: R4 | papers_core RAG (동일 인덱스, 다른 쿼리)"""

from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

# 판정 재현성을 위한 규칙표. 사람이 같은 근거로 같은 결론에 도달할 수 있어야 한다.
TRL_RULES = """TRL 판정 규칙표 — 아래 근거가 확인될 때만 해당 구간을 부여한다.
  TRL 1-2  개념·원리 제시. 수식/이론만 존재
  TRL 3    개념 검증 실험. 제한된 조건의 실험 결과 존재
  TRL 4    실험실 환경 통합 검증. 연구 코드 공개, 재현 가능한 벤치마크
  TRL 5-6  운영 유사 환경 검증. 실제 워크로드·다중 노드·장기 구동 근거
  TRL 7-8  운영환경 실증. 상용 시스템 통합, 수율/안정성 데이터
  TRL 9    상용 운영. 제품 출시 및 배포 확인

적용 원칙:
- 논문 발표 사실만으로 TRL 4 이상을 부여하지 않는다.
- 근거가 없는 구간(특히 TRL 4-6 의 수율·실운영 성능)은 "근거 공백" 으로 남긴다.
- 상한만 적지 말고 "확인된 최고 구간 / 그 근거 / 그 위로 올라가지 못한 이유" 를 함께 쓴다."""

INSTRUCTION = f"""너는 TRL(기술성숙도) 평가 담당이다. 기술별로 규칙표에 따라 판정한다.

{TRL_RULES}

ITME 는 세 가지 성숙도를 **분리해서** 판정한다. 하나로 뭉치면 안 된다.
  ① CXL 표준 자체의 성숙도
  ② CXL 기반 상용 제품·PoC 의 성숙도
  ③ ITME 가 제안한 구체 아키텍처의 성숙도  ← 평가 대상은 이것
TurboQuant 도 "양자화 기법 일반" 과 "TurboQuant 구현" 을 분리한다."""

QUERIES = ["실험 환경 하드웨어 구현 공개 범위", "실제 배포 운영 사례 프로토타입 시뮬레이션"]


def maturity(state) -> dict:
    chunks, seen = [], set()
    for tech in ["TurboQuant", "ITME"]:
        for q in QUERIES:
            for c in search_source_documents.invoke(
                {"query": q, "collection": "papers_core", "technology": tech, "top_k": 4}
            ):
                if c["chunk_id"] not in seen:
                    seen.add(c["chunk_id"])
                    chunks.append(c)

    text = run_node(INSTRUCTION, state["domain"], format_chunks(chunks))
    # 조회 결과를 evidence 에 쓰지 않는다 — 단일 쓰기 주체 원칙(§7).
    return {"maturity": perspective(text, seen),
            "trace": [{"node": "maturity", "chunks": len(chunks)}]}
