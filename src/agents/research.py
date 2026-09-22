"""기술 조사 — 담당: R4 | papers_core RAG | evidence 단일 쓰기 주체

- 컬렉션 바인딩: papers_core 전용 (1차 논문 원문 근거 기반)
- 핵심 임무: TurboQuant(SW 압축) 및 ITME(HW 메모리 접근) 접근 방식, 적용 범위, 한계 도출
- 설계서 2절 성능 수치 해석 가드레일 반영 (3.5비트, 8배, 1.80배, 35.7%, 1.81배)
- 단독 갱신 키: evidence, research, tech_status, gaps, retrieval_round, trace
- 상태 판정(tech_status): 1차 미확보 시 '근거 부족'(재조사), 2차 미확보 시 '평가 보류'로 파이프라인 지속
"""

from typing import Any, Dict, List, Set
from src.agents.common import perspective, run_node
from src.tools.docs import format_chunks, search_source_documents

TECHS = ["TurboQuant", "ITME"]

INSTRUCTION = """당신은 데이터센터 및 클라우드 인프라 관점의 기술 조사 수석 연구원(R4)입니다.
선정 기술 2건(TurboQuant, ITME)의 논문 원문에서 다음 핵심 항목을 추출하여 객관적으로 기술하십시오.

[필수 분석 항목]
1. 접근 방식: 어느 시스템 계층(SW 알고리즘/컴파일러 vs HW CXL/메모리 컨트롤러)에서 KV cache 병목을 해결하는가
2. 적용 범위: 어떤 모델 아키텍처, 하드웨어 스펙, 동시성 부하 조건에서 검증되었는가
3. 기술적 한계: 원문이 스스로 밝힌 제약 사항, 런타임 오버헤드, 미해결 과제

[작성 및 인용 규칙]
- 기술별로 절을 명확히 나누어 균형 있게 서술하십시오.
- 우열 판정(어느 기술이 더 우월하다는 승자 판정)은 엄격히 금지합니다. 두 접근법의 본질적 차이를 기술하십시오.
- 사실(fact) 주장은 반드시 본문 내에 검색된 청크의 12자리 hex ID를 `[12자리hex]` 형식으로 인용하십시오.
- scope=comparison 문서(InfiniGen, PIM/CXL 계열 등)는 계열 비교에만 사용하며, 선정 기술의 성능 주장 근거로 대체하지 마십시오.

[설계서 2절 성능 수치 해석 가드레일 (위반 시 검증 탈락)]
① 3.5비트: TurboQuant의 3.5비트 양자화는 특정 모델 및 과제에 국한된 벤치마크 결과임을 명시하십시오. (일반 양자화 달성 단정 금지)
② 8배 가속: H100 GPU 환경의 어텐션 로짓 연산에 국한된 가속이며, E2E 전체 추론 처리량과 반드시 엄격히 분리하십시오.
③ 1.80배: ITME 논문 서론의 NVMe-oF 대비 처리량 향상 주장임을 명시하고, 로컬 기준선과의 일반적 우위로 단정하지 마십시오.
④ 35.7% 개선: CPU 오프로드 대비 256개 동시 대화에서 호스트 메모리 128GB 소진 후(21턴 이후 구간) 지표임을 명시하십시오.
⑤ 1.81배 가속: GPU 재계산 대비 128개 대화 기준 '5턴 시점의 TTFT(첫 토큰 지연)' 지표로 한정하십시오.

[출력 마지막 줄 형식 강제]
본문의 맨 마지막 줄은 반드시 아래 규격 중 하나로 작성하십시오:
근거 공백: <공백 내용 1> | <공백 내용 2>
(공백이 없을 경우) 근거 공백: 없음"""

QUERIES = [
    "KV 캐시 메모리 병목 접근 방식과 핵심 알고리즘",
    "실험 설정, 검증 모델, 적용 범위 및 하드웨어 환경",
    "성능 한계, 오버헤드, 양자화 손실 및 미해결 제약사항",
]
WIDEN = " 벤치마크 처리량 지연시간 정확도 오버헤드 theoretical bound"


def research(state: Dict[str, Any]) -> Dict[str, Any]:
    """양 기술 근거를 papers_core에서 수집해 evidence 풀을 채우고 기술 조사 관점을 생성한다.
    
    State 입력:
      - evidence: 이전 회차 수집 청크 리스트 (없으면 [])
      - retrieval_round: 현재까지의 검색 회차 (기본값 0)
      - domain: 대상 도메인 (기본값 제공)
      
    State 단독 갱신 반환:
      - evidence: 누적된 모든 유효 청크 리스트
      - research: 관점 dict {text, citations, gaps, bad_citations}
      - tech_status: 기술별 상태 dict (예: {"TurboQuant": "ok", "ITME": "근거 부족"})
      - gaps: 본문 공백 및 미확보 기술 목록
      - retrieval_round: 증가된 회차 (int)
      - trace: 실행 기록 리스트 (누적)
    """
    rnd = state.get("retrieval_round", 0)

    # 1. 기존 누적 evidence 복원 (재조사 라운드 시 이전 청크 보존)
    accumulated_chunks: List[Dict[str, Any]] = list(state.get("evidence", []))
    seen: Set[str] = {c["chunk_id"] for c in accumulated_chunks if "chunk_id" in c}

    # 2. 질의 구성 (1차는 기본 질의, 2차 이상 재조사 시에는 WIDEN 키워드 확장)
    queries = QUERIES if rnd == 0 else [q + WIDEN for q in QUERIES]

    # 3. papers_core 전용 검색 실행
    for tech in TECHS:
        for q in queries:
            results = search_source_documents.invoke({
                "query": q,
                "collection": "papers_core",
                "technology": tech,
                "perspective": "research",
                "top_k": 5,
            })
            for c in results:
                cid = c.get("chunk_id")
                if cid and cid not in seen:
                    seen.add(cid)
                    accumulated_chunks.append(c)

    # 4. LLM 생성 및 표준 관점 dict 생성
    domain = state.get("domain", "데이터센터/클라우드 추론 인프라 (대규모 동시성, 비용 민감)")
    text = run_node(INSTRUCTION, domain, format_chunks(accumulated_chunks))
    p = perspective(text, seen)  # 계약 ② 표준 {text, citations, gaps, bad_citations}

    # 5. primary 1차 근거(scope == "direct") 확보 여부 검증
    covered_techs = {
        tech
        for c in accumulated_chunks
        if c.get("scope") == "direct"
        for tech in c.get("applies_to", [])
    }

    # 6. 라운드별 tech_status 판정
    # - 1차(rnd == 0) 미확보: '근거 부족' -> LangGraph 조건부 엣지가 재조사(research) 유도
    # - 2차(rnd >= 1) 미확보: '평가 보류' -> 무한 루프 방지 및 파이프라인 지속
    status: Dict[str, str] = {}
    for tech in TECHS:
        if tech in covered_techs:
            status[tech] = "ok"
        else:
            status[tech] = "근거 부족" if rnd == 0 else "평가 보류"

    # 7. gaps 병합 (본문 파싱 gaps + 상태 미확보 안내)
    missing_notes = [f"{tech}: 원문 1차 근거(direct) 미확보" for tech, s in status.items() if s != "ok"]
    merged_gaps = list(dict.fromkeys(p.get("gaps", []) + missing_notes))

    # 8. 계약 ① State 17키 단독 갱신 반환
    return {
        "evidence": accumulated_chunks,
        "research": {
            **p,
            "gaps": merged_gaps,
        },
        "tech_status": status,
        "gaps": merged_gaps,
        "retrieval_round": rnd + 1,
        "trace": [
            {
                "node": "research",
                "round": rnd + 1,
                "chunks": len(accumulated_chunks),
                "direct_covered": sorted(list(covered_techs)),
                "status": status,
            }
        ],
    }