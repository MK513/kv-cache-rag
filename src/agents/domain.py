"""도메인 적용 평가 — 담당: R4 | papers_core RAG + context(배경용)

- 컬렉션 바인딩:
    - papers_core: TurboQuant 및 ITME의 직접 실증 벤치마크 (성능, 배치, 지연)
    - context: 데이터센터 인프라 및 비용 구조 배경 설명용 (성능 주장 인용 엄격 금지)
- 핵심 임무: 대규모 동시성 및 비용 민감 환경에서의 단독 적용성 및 결합 가설 평가
- 단일 쓰기 주체 원칙: domain_assessment 및 trace 키만 갱신 (evidence 쓰기 절대 금지)
- 가드레일 반영:
    1) TurboQuant 처리량 수치 부재 시 '처리량 근거 미확인' 공백 명시
    2) 서로 다른 실험 환경의 성능 배수 직접 비교 금지
    3) 결합 상호작용은 본문과 분리하여 '결합 가설' 절에 추론으로 명시
"""

from typing import Any, Dict, List, Set
from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

# 도메인 적용 평가 시스템 프롬프트 및 가드레일
INSTRUCTION = """당신은 데이터센터 및 클라우드 AI 추론 인프라 관점의 도메인 적용성 수석 평가관(R4)입니다.
대규모 동시성(High Concurrency)과 비용 민감도(Cost Efficiency)를 기준으로 기술 적용성을 평가하십시오.

[본문 필수 구성 (두 항목)]
1. TurboQuant 단독 적용성:
   - 알고리즘 압축을 통한 GPU 메모리 절감 및 서빙 배치 크기(Batch Size) 확장 효과
   - 양자화 비트 수 축소에 따른 정확도(Perplexity/Task Accuracy) 손실 폭
   - ★ [핵심 가드레일] TurboQuant 논문에 전체 추론 처리량(Throughput) 수치가 명시되지 않은 경우, 절대로 숫자를 지어내지 말고 "처리량 근거 미확인"으로 기술하고 공백에 남기십시오.

2. ITME 단독 적용성:
   - CXL 메모리 풀링/오프로드를 통한 동시 세션(Concurrent Sessions) 수용 한계 확장
   - 호스트-디바이스 간 데이터 전송 지연(Latency) 및 TTFT(첫 토큰 지연) 영향도

[출처 및 인용 가드레일]
- 모든 성능 벤치마크는 반드시 모델명, 하드웨어 사양, 동시성 부하 조건을 병기하여 인용하십시오.
- 서로 다른 전제와 조건에서 측정된 배수 수치를 직접 비교(예: TurboQuant 8배 vs ITME 1.8배)하지 마십시오.
- collection=context 청크(서베이/배경 자료)는 인프라 배경 설명에만 사용하며, 기술의 성능 주장 근거로 인용하지 마십시오.
- scope=comparison 문서(InfiniGen, PIM 등)는 계열 비교에만 사용하고 선정 기술의 주장 근거로 대체하지 마십시오.
- 사실(fact)은 반드시 검색된 청크의 12자리 hex ID인 `[12자리hex]` 형식으로 인용하십시오.

[결합 상호작용 표기 규칙]
- 두 기술의 결합(SW 압축 + HW 확장) 시너지는 본문 1·2절에 절대 섞지 마십시오.
- 본문 하단에 반드시 별도의 `### [결합 가설]` 문단으로 분리하고, 이는 1·2절로부터 도출한 '실측 근거가 없는 추론/가설'임을 명시하십시오.

[출력 마지막 줄 형식 강제]
본문의 맨 마지막 줄은 반드시 아래 규격 중 하나로 작성하십시오:
근거 공백: <공백 내용 1> | <공백 내용 2>
(공백이 없는 경우) 근거 공백: 없음"""

# 기술별 실증 질의 (papers_core용)
QUERIES = [
    "처리량 배치 크기 동시 요청 처리 serving batch throughput concurrency",
    "지연 정확도 손실 비용 오버헤드 latency accuracy degradation TTFT",
]


def domain_assessment(state: Dict[str, Any]) -> Dict[str, Any]:
    """papers_core와 context 문서를 검색하여 도메인 적용성 평가를 수행한다.
    
    State 입력:
      - domain: 대상 평가 도메인 (기본: 데이터센터/클라우드 추론 인프라)
      
    State 단독 갱신 반환:
      - domain_assessment: 관점 dict {text, citations, gaps, bad_citations}
      - trace: 실행 로그 리스트 (누적 reducer 연동)
    """
    chunks: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    # 1. papers_core 컬렉션 검색 (TurboQuant, ITME 실증 근거)
    for tech in ["TurboQuant", "ITME"]:
        for q in QUERIES:
            results = search_source_documents.invoke({
                "query": q,
                "collection": "papers_core",
                "technology": tech,
                "perspective": "domain",
                "top_k": 4,
            })
            for c in results:
                cid = c.get("chunk_id")
                if cid and cid not in seen:
                    seen.add(cid)
                    chunks.append(c)

    # 2. context 컬렉션 검색 (인프라 배경 및 비용 구조용)
    context_results = search_source_documents.invoke({
        "query": "데이터센터 추론 동시성 메모리 비용 구조 TCO",
        "collection": "context",
        "perspective": "domain",
        "top_k": 3,
    })
    for c in context_results:
        cid = c.get("chunk_id")
        if cid and cid not in seen:
            seen.add(cid)
            chunks.append(c)

    # 3. LLM 평가 실행 및 표준 관점 dict 생성
    domain = state.get("domain", "데이터센터/클라우드 추론 인프라 (대규모 동시성, 비용 민감)")
    text = run_node(INSTRUCTION, domain, format_chunks(chunks))
    p = perspective(text, seen)  # 계약 ② 표준 규격 생성

    # 4. 계약 ① State 17키 규격 반환 (evidence 쓰지 않고 domain_assessment, trace만 반환)
    return {
        "domain_assessment": p,
        "trace": [
            {
                "node": "domain_assessment",
                "chunks": len(chunks),
                "citations_count": len(p.get("citations", [])),
                "gaps_count": len(p.get("gaps", [])),
            }
        ],
    }