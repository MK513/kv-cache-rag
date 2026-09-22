"""기술 조사 — 담당: R4 | papers_core RAG | bind_document_search 바인딩

- 계약 준수: State 14필드 중 'research' 및 'trace' 단독 갱신
- 스키마 규격: src.schema.Assessment (Claim, Evidence, Source, Gap 체계)
- 컬렉션 바인딩: bind_document_search("research") -> papers_core (scope=direct 전용 자동 강제)
- 핵심 임무: TurboQuant(SW 압축) 및 ITME(HW 메모리 접근) 시스템 계층, 적용 범위, 한계 도출
- 설계서 2절 성능 수치 해석 가드레일 반영 (3.5비트, 8배, 1.80배, 35.7%, 1.81배)
- 인터페이스 계약 보완:
    - 인용 사슬 닫힘성 보장: 본문에 실제 유효하게 인용된 청크만 Evidence/Source로 등록
    - status="failed" 판정 시 claims=[] 강제
    - 허위 인용(invalid_citations) 발생 시 실패 판정 및 trace 기록
    - TurboQuant / ITME 기술별 Claim 분리 및 유효 evidence_ids 매핑
    - Gap 객체의 technology(TurboQuant/ITME/both) 분기 처리
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from src.agents.common import run_node
from src.schema import Assessment, Claim, Evidence, Gap, Source
from src.tools.docs import bind_document_search, format_chunks

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
- scope=comparison 문서는 계열 비교에만 사용하며, 선정 기술의 성능 주장 근거로 대체하지 마십시오.

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
        tech_matched = "both"
        lower_item = item.lower()
        if "turboquant" in lower_item and "itme" not in lower_item:
            tech_matched = "TurboQuant"
        elif "itme" in lower_item and "turboquant" not in lower_item:
            tech_matched = "ITME"

        gap_objects.append(
            Gap(
                role="research",
                technology=tech_matched,
                item=item,
                reason="논문 본문 추출 미확인 영역",
            )
        )
    return gap_objects


def _split_technology_sections(text: str) -> Tuple[str, str]:
    """본문에서 TurboQuant 서술부와 ITME 서술부를 분리."""
    tq_pattern = r"(?:^|\n)(?:1\.\s*TurboQuant|###\s*1\.\s*TurboQuant|TurboQuant\s*(?:접근|조사|기술))"
    itme_pattern = r"(?:^|\n)(?:2\.\s*ITME|###\s*2\.\s*ITME|ITME\s*(?:접근|조사|기술))"

    tq_match = re.search(tq_pattern, text, re.IGNORECASE)
    itme_match = re.search(itme_pattern, text, re.IGNORECASE)

    if tq_match and itme_match:
        tq_start = tq_match.start()
        itme_start = itme_match.start()
        if tq_start < itme_start:
            tq_text = text[tq_start:itme_start].strip()
            itme_text = text[itme_start:].strip()
            return tq_text, itme_text

    # 패턴 매칭이 실패한 경우 전체를 통짜로 반환
    return text.strip(), ""


def research(state: Dict[str, Any]) -> Dict[str, Any]:
    """papers_core에서 양 기술 1차 근거를 수집하고 schema.Assessment를 구성하여 반환한다.

    State 14필드 규약:
      - 입력: run_id, run_config, domain (선택)
      - 반환: research (Assessment dict), trace (Event dict list)
    """
    run_id = state.get("run_id", "default_run")
    domain = state.get(
        "domain",
        state.get("run_config", {}).get("domain", "데이터센터/클라우드 추론 인프라 (대규모 동시성, 비용 민감)"),
    )

    # 1. R3 계약에 따른 research 전용 문서 검색기 바인딩 (papers_core, direct 자동 강제)
    doc_search = bind_document_search("research")

    accumulated_chunks: List[Dict[str, Any]] = []
    chunk_index_map: Dict[str, Dict[str, Any]] = {}

    def _execute_search(query_list: List[str]):
        for tech in TECHS:
            for q in query_list:
                hits = doc_search.invoke({"query": q, "technology": tech, "top_k": 5})
                for c in hits:
                    cid = c.get("chunk_id")
                    if cid and cid not in chunk_index_map:
                        chunk_index_map[cid] = c
                        accumulated_chunks.append(c)

    # 2. 1차 기본 검색
    _execute_search(QUERIES)

    # direct 근거 확보 여부 확인
    covered_techs = {
        tech
        for c in accumulated_chunks
        if c.get("scope") == "direct"
        for tech in c.get("applies_to", [])
    }

    # 3. 내부 루프 보완 검색 (미확보 기술 존재 시 WIDEN 질의로 1회 보완)
    supplemental_searches = 0
    if len(covered_techs) < len(TECHS):
        supplemental_queries = [q + WIDEN for q in QUERIES]
        _execute_search(supplemental_queries)
        supplemental_searches = len(supplemental_queries)

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

        # Source 등록 (필수: source_id, run_id, collection, allowed_uses)
        if sid not in sources_map:
            sources_map[sid] = Source(
                source_id=sid,
                run_id=run_id,
                collection=collection_name,
                allowed_uses=["research", "maturity", "domain"],
                title=c.get("title", sid),
            )

        # Evidence 등록 (인용 사슬 형성용)
        evidence_map[cid] = Evidence(
            evidence_id=cid,
            source_id=sid,
            run_id=run_id,
            collection=collection_name,
            allowed_uses=["research"],
            quote=c.get("text", "")[:300],
            location=f"p.{c.get('page', 1)}",
        )

    # 6-2. 상태 판정 (completed / partial / failed)
    # 실제 본문에 유효하게 인용된 scope=direct 청크 기준으로 대상 기술 충족 여부 확인
    cited_covered_techs = {
        tech
        for cid in cited_ids_set
        for tech in chunk_index_map[cid].get("applies_to", [])
        if chunk_index_map[cid].get("scope") == "direct"
    }

    if len(cited_covered_techs) == len(TECHS) and valid_citations and not invalid_citations:
        status = "completed"
    elif valid_citations and not invalid_citations:
        status = "partial"
    else:
        status = "failed"

    # 6-3. Claim 구성: status=failed인 경우 규약에 따라 반드시 claims=[] 강제
    claims: List[Claim] = []
    unbacked_gaps: List[Gap] = []
    if status != "failed":
        tq_text, itme_text = _split_technology_sections(generated_text)

        if tq_text and itme_text:
            # 기술별 Claim 분리 생성
            tq_cits = [cid for cid in HEX_CITATION_RE.findall(tq_text) if cid in cited_ids_set]
            itme_cits = [cid for cid in HEX_CITATION_RE.findall(itme_text) if cid in cited_ids_set]

            # §6 — 근거가 없는 항목은 Claim 이 아니라 Gap 이다.
            if tq_cits:
                claims.append(
                    Claim(
                        claim_id=f"claim_research_tq_{run_id[:8]}",
                        text=tq_text,
                        technology="TurboQuant",
                        kind="fact",
                        evidence_ids=list(dict.fromkeys(tq_cits)),
                    )
                )
            else:
                unbacked_gaps.append(
                    Gap(
                        role="research",
                        technology="TurboQuant",
                        item="TurboQuant 절 인용 근거 미확보",
                        reason="본문 해당 절에 유효한 인용 ID 가 없다",
                    )
                )
            # §6 — 근거가 없는 항목은 Claim 이 아니라 Gap 이다.
            if itme_cits:
                claims.append(
                    Claim(
                        claim_id=f"claim_research_itme_{run_id[:8]}",
                        text=itme_text,
                        technology="ITME",
                        kind="fact",
                        evidence_ids=list(dict.fromkeys(itme_cits)),
                    )
                )
            else:
                unbacked_gaps.append(
                    Gap(
                        role="research",
                        technology="ITME",
                        item="ITME 절 인용 근거 미확보",
                        reason="본문 해당 절에 유효한 인용 ID 가 없다",
                    )
                )
        else:
            # 섹션 분리가 안 된 경우 fallback 단일 Claim
            claims.append(
                Claim(
                    claim_id=f"claim_research_all_{run_id[:8]}",
                    text=generated_text,
                    technology="both",
                    kind="fact",
                    evidence_ids=list(dict.fromkeys(valid_citations)),
                )
            )

    # 6-4. Gap 구성
    gap_objects = _extract_gaps_from_text(generated_text) + unbacked_gaps

    # 원문 미확보 기술에 대한 명시적 Gap 기록
    for tech in TECHS:
        if tech not in cited_covered_techs:
            gap_objects.append(
                Gap(
                    role="research",
                    technology=tech,
                    item=f"{tech} 1차 원문(direct) 실증 근거 인용 미확보",
                    reason="papers_core 색인 내 scope=direct 청크 인용 부재",
                )
            )

    # 6-5. Assessment 객체 조립
    assessment = Assessment(
        status=status,
        claims=claims,
        sources=list(sources_map.values()),
        evidence=list(evidence_map.values()),
        gaps=gap_objects,
    )

    # 7. trace 이벤트 생성 (Event 스키마 호환 dict)
    trace_event = {
        "node": "research",
        "status": status,
        "attempt": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chunks": len(accumulated_chunks),
        "citations": len(valid_citations),
        "invalid_citations": len(invalid_citations),
        "supplemental_searches": supplemental_searches,
        "covered_techs": sorted(list(cited_covered_techs)),
    }

    # 8. State 14필드 단독 쓰기 반환
    return {
        "research": assessment.model_dump(),
        "trace": [trace_event],
    }