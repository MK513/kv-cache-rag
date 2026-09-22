"""도메인 적용 평가 — 담당: R4 | papers_core RAG + context(배경용) | bind_document_search 바인딩

- 계약 준수: State 14필드 중 'domain_assessment' 및 'trace' 단독 갱신
- 스키마 규격: src.schema.Assessment (Claim, Evidence, Source, Gap 체계)
- 컬렉션 바인딩:
    - bind_document_search("domain") -> papers_core (직접 실증 벤치마크)
    - bind_document_search("domain", "context") -> context (배경 설명용, scope=comparison/secondary)
- 핵심 임무: 대규모 동시성 및 비용 민감 환경에서의 단독 적용성 및 결합 가설 평가
- 가드레일 반영:
    1) TurboQuant 처리량 수치 부재 시 '처리량 근거 미확인' 공백 명시
    2) 서로 다른 실험 환경의 성능 배수 직접 비교 금지
    3) 결합 상호작용은 본문과 분리하여 '결합 가설' 절에 추론/가설로 명시
- 인터페이스 계약 보완:
    - 인용 사슬 닫힘성 보장 (실제 인용된 청크만 Evidence/Source로 등록)
    - status="failed" 시 claims=[] 강제
    - 사실(fact)과 결합 가설(hypothesis)의 Claim 분리 및 evidence 매핑
    - 기술별(TurboQuant/ITME) Gap 객체 분기
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from src.agents.common import run_node
from src.schema import Assessment, Claim, Evidence, Gap, Source
from src.tools.docs import bind_document_search, format_chunks

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

TECHS = ["TurboQuant", "ITME"]

QUERIES = [
    "처리량 배치 크기 동시 요청 처리 serving batch throughput concurrency",
    "지연 정확도 손실 비용 오버헤드 latency accuracy degradation TTFT",
]

HEX_CITATION_RE = re.compile(r"\[([0-9a-f]{12})\]")


def _extract_gaps_from_text(text: str) -> List[Gap]:
    """본문 마지막 줄의 '근거 공백: ...' 패턴을 파싱하여 Gap 객체 목록으로 변환."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return []
    last_line = lines[-1]
    if not last_line.startswith("근거 공백:"):
        return []

    payload = last_line.replace("근거 공백:", "").strip()
    if payload in ("", "없음", "None"):
        return []

    raw_items = [g.strip() for g in payload.split("|") if g.strip()]
    gap_objects: List[Gap] = []
    for item in raw_items:
        # 기술명 식별에 따른 tech 할당 (R5 관점 상충 및 갭 분석 정밀화)
        tech_matched = "both"
        lower_item = item.lower()
        if "turboquant" in lower_item and "itme" not in lower_item:
            tech_matched = "TurboQuant"
        elif "itme" in lower_item and "turboquant" not in lower_item:
            tech_matched = "ITME"

        gap_objects.append(
            Gap(
                role="domain",
                technology=tech_matched,
                item=item,
                reason="도메인 인프라 실증 또는 결합 워크로드 실측 근거 미확인",
            )
        )
    return gap_objects


def _split_body_and_hypothesis(text: str) -> Tuple[str, str]:
    """본문에서 실증 사실 서술부와 결합 가설부를 분리."""
    hypothesis_marker = "### [결합 가설]"
    if hypothesis_marker in text:
        parts = text.split(hypothesis_marker, 1)
        facts_part = parts[0].strip()
        hypothesis_part = (hypothesis_marker + "\n" + parts[1]).strip()
        return facts_part, hypothesis_part
    return text.strip(), ""


def domain_assessment(state: Dict[str, Any]) -> Dict[str, Any]:
    """papers_core와 context 문서를 검색하여 도메인 적용성 schema.Assessment를 구성하여 반환한다.

    State 14필드 규약:
      - 입력: run_id, run_config, domain (선택)
      - 반환: domain_assessment (Assessment dict), trace (Event dict list)
    """
    run_id = state.get("run_id", "default_run")
    domain = state.get(
        "domain",
        state.get("run_config", {}).get("domain", "데이터센터/클라우드 추론 인프라 (대규모 동시성, 비용 민감)"),
    )

    # 1. R3 계약에 따른 역할별 전용 검색 도구 바인딩
    core_search = bind_document_search("domain")               # papers_core (direct/comparison)
    context_search = bind_document_search("domain", "context") # context (comparison/secondary)

    accumulated_chunks: List[Dict[str, Any]] = []
    chunk_index_map: Dict[str, Dict[str, Any]] = {}

    # 2. papers_core 컬렉션 검색 (실증 벤치마크)
    for tech in TECHS:
        for q in QUERIES:
            hits = core_search.invoke({"query": q, "technology": tech, "top_k": 4})
            for c in hits:
                cid = c.get("chunk_id")
                if cid and cid not in chunk_index_map:
                    chunk_index_map[cid] = c
                    accumulated_chunks.append(c)

    # 3. context 컬렉션 검색 (인프라 배경 및 비용 구조)
    context_hits = context_search.invoke({
        "query": "데이터센터 추론 동시성 메모리 비용 구조 TCO",
        "technology": "both",
        "top_k": 3,
    })
    for c in context_hits:
        cid = c.get("chunk_id")
        if cid and cid not in chunk_index_map:
            chunk_index_map[cid] = c
            accumulated_chunks.append(c)

    # 4. LLM 실행
    prompt_context = format_chunks(accumulated_chunks)
    generated_text = run_node(INSTRUCTION, domain, prompt_context)

    # 5. 본문 인용 파싱 및 검증
    all_citations = HEX_CITATION_RE.findall(generated_text)
    valid_citations = [cid for cid in all_citations if cid in chunk_index_map]
    invalid_citations = [cid for cid in all_citations if cid not in chunk_index_map]
    cited_ids_set = set(valid_citations)

    # 6. schema.Assessment 구성
    # 6-1. 인용 사슬 닫힘성 보장: 본문에 실제로 인용된 청크만 Source 및 Evidence로 등록
    sources_map: Dict[str, Source] = {}
    evidence_map: Dict[str, Evidence] = {}

    for cid in cited_ids_set:
        c = chunk_index_map[cid]
        sid = c.get("source_id") or c.get("source", "unknown_source")
        collection_name = c.get("collection", "papers_core")

        if sid not in sources_map:
            sources_map[sid] = Source(
                source_id=sid,
                run_id=run_id,
                collection=collection_name,
                allowed_uses=["domain"],
                title=c.get("title", sid),
            )

        evidence_map[cid] = Evidence(
            evidence_id=cid,
            source_id=sid,
            run_id=run_id,
            collection=collection_name,
            allowed_uses=["domain"],
            quote=c.get("text", "")[:300],
            location=f"p.{c.get('page', 1)}",
        )

    # 6-2. 사실 및 결합 가설 분리하여 Claim 생성
    facts_text, hypothesis_text = _split_body_and_hypothesis(generated_text)

    # 6-3. 상태 판정 (completed / partial / failed)
    # 인용된 청크 중 papers_core에서 커버된 기술 계산
    covered_techs = {
        t
        for cid in cited_ids_set
        for t in chunk_index_map[cid].get("applies_to", [])
        if chunk_index_map[cid].get("collection") == "papers_core"
    }

    if len(covered_techs) == len(TECHS) and valid_citations and not invalid_citations:
        status = "completed"
    elif valid_citations and not invalid_citations:
        status = "partial"
    else:
        status = "failed"

    # 6-4. Claim 구성: status=failed인 경우 규약에 따라 반드시 빈 리스트로 초기화
    claims: List[Claim] = []
    gap_objects_extra: List[Gap] = []
    if status != "failed":
        # 1) 실증 분석 부문 (fact: 유효 인용 ID 바인딩)
        fact_citations = [cid for cid in HEX_CITATION_RE.findall(facts_text or "") if cid in cited_ids_set]
        if facts_text and fact_citations:
            claims.append(
                Claim(
                    claim_id=f"claim_domain_fact_{run_id[:8]}",
                    text=facts_text,
                    technology="both",
                    kind="fact",
                    evidence_ids=list(dict.fromkeys(fact_citations)),
                )
            )

        # 2) 결합 가설 부문
        # §6 — 추론과 가설에도 전제가 된 근거와 설명을 연결한다. 전제가 없으면
        # Claim 이 아니라 Gap 이다(§5 — 결합 효과는 결합 실험이 없으면 가설로만 적는다).
        hypothesis_premise = list(dict.fromkeys(valid_citations))
        if hypothesis_text and hypothesis_premise:
            claims.append(
                Claim(
                    claim_id=f"claim_domain_hypo_{run_id[:8]}",
                    text=hypothesis_text,
                    technology="both",
                    kind="hypothesis",
                    evidence_ids=hypothesis_premise,
                    explanation="두 기술의 개별 근거에서 도출한 결합 가설이며 결합 실측 근거가 아니다.",
                )
            )
        elif hypothesis_text:
            gap_objects_extra.append(
                Gap(
                    role="domain",
                    technology="both",
                    item="결합 효과",
                    reason="전제로 인용할 근거가 본문에 없다",
                )
            )

    # 6-5. Gap 객체 생성
    gap_objects = _extract_gaps_from_text(generated_text) + gap_objects_extra

    # 6-6. Assessment 최종 조립
    assessment = Assessment(
        status=status,
        claims=claims,
        sources=list(sources_map.values()),
        evidence=list(evidence_map.values()),
        gaps=gap_objects,
    )

    # 7. trace 이벤트 생성 (Event 스키마 호환 dict, 누적 리듀서 등록)
    trace_event = {
        "node": "domain_assessment",
        "status": status,
        "attempt": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chunks": len(accumulated_chunks),
        "citations": len(valid_citations),
        "invalid_citations": len(invalid_citations),
        "covered_techs": sorted(list(covered_techs)),
    }

    # 8. State 14필드 단독 쓰기 반환
    return {
        "domain_assessment": assessment.model_dump(),
        "trace": [trace_event],
    }