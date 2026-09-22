"""TRL(기술성숙도) 평가 노드 — 담당: R4 | papers_core RAG (bind_document_search 바인딩)

- 계약 준수: State 14필드 중 'maturity' 및 'trace' 단독 갱신
- 스키마 규격: src.schema.Assessment (Claim, Evidence, Source, Gap 체계)
- 컬렉션 바인딩: bind_document_search("maturity") -> papers_core (scope=direct|comparison 자동 강제)
- TRL 6밴드 판정 규칙 엄격 적용 (확인된 최고 구간 / 그 근거 / 상위 미도달 이유)
- 4대 가드레일 반영:
    1) CXL 표준/상용 제품과 ITME 구체 아키텍처 성숙도 3단계 엄격 분리
    2) TurboQuant(SW)에 반도체 하드웨어 '수율' 평가 기준 적용 금지
    3) 발표 시기(최신 논문 등) 기반의 TRL 자의적 가감 금지
    4) 양자화 일반 이론과 TurboQuant 실제 구현체 분리
- 인터페이스 계약 보완:
    - 인용 사슬 닫힘성 보장: 본문에 실제 인용된 청크만 Evidence/Source로 등록
    - status="failed" 시 claims=[] 강제
    - 허위 인용(invalid_citations) 발생 시 실패 판정 및 trace 기록
    - TurboQuant / ITME 기술별 Claim 분리 및 유효 evidence_ids 매핑
    - Gap 객체의 technology(TurboQuant/ITME/both) 분기 처리
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from langchain_core.language_models.chat_models import BaseChatModel

from src.agents.common import run_node
from src.llm import get_llm
from src.schema import Assessment, Claim, Evidence, Gap, Source
from src.tools.docs import bind_document_search, format_chunks

# ============================================================================
# 1. TRL 판정 규칙표 및 시스템 지침 (4대 가드레일)
# ============================================================================
TRL_RULES = """[TRL 판정 규칙표] — 아래 명시적 근거가 문서에서 확인될 때만 해당 구간을 부여한다.
  - TRL 1-2: 개념·원리 제시. 수식, 이론적 모델링만 존재
  - TRL 3  : 개념 검증(PoC) 실험. 제한된 합성 워크로드/시뮬레이션 결과 존재
  - TRL 4  : 실험실 환경 통합 검증. 연구 코드 공개, 재현 가능한 오프라인 벤치마크
  - TRL 5-6: 운영 유사 환경 검증. 실제 서빙 워크로드, 다중 노드, 장기 구동 신뢰성 근거
  - TRL 7-8: 운영 환경 실증. 상용 시스템 통합 완료, 하드웨어 수율/안정성 데이터 확보
  - TRL 9  : 상용 운영. 양산 제품 출시 및 실제 프로덕션 클라우드 배포 확인

[TRL 평가 핵심 원칙]
1. 논문 발표 사실만으로 TRL 4 이상을 부여하지 않는다.
2. 상한 구간만 단정하지 말고, 반드시 아래 3요소를 세트로 서술한다:
   - ① 확인된 최고 구간 (TRL X)
   - ② 그 구간을 뒷받침하는 직접 논문 근거 [12자리hex]
   - ③ 그 위 구간으로 올라가지 못한 구체적 이유 (상위 미도달 사유)
3. 근거가 없는 구간(실제 운영 데이터, 장기 안정성, 상용 배포 등)은 절대로 추측하여 Claim으로 만들지 말고 '근거 공백(gaps)'으로 남긴다.

[4대 필수 가드레일 (위반 시 검증 탈락)]
① ITME 성숙도 3계층 분리:
   - 1계층: CXL 표준 자체의 성숙도 (CXL 컨소시엄 표준화 수준)
   - 2계층: CXL 기반 상용 부품/PoC 제품의 성숙도 (CMM-D 등 메모리 모듈)
   - 3계층: ITME가 제안한 구체 아키텍처의 성숙도 (Near-Memory 가속 구조)
   ★ 실제 평가 대상은 '3계층(ITME 아키텍처)'이다. 1·2계층의 성숙도를 ITME 자체 성숙도로 전용하지 말 것.
② SW 기술(TurboQuant)에 '반도체 수율' 잣대 적용 금지:
   - TurboQuant는 알고리즘/소프트웨어 기술이다. 하드웨어 생산 지표인 '수율(yield)' 미비 등을 이유로 감점하거나 상위 미도달 사유로 기재하지 말 것.
③ 발표 시기 기반 TRL 가감 금지:
   - "2025/2026년 최신 연구이므로 TRL이 낮다"거나 "발표된 지 얼마 안 되어 상용화 전이다"와 같은 연도/시기 기반 감점을 금지한다. 오직 논문 본문의 실증 데이터 수준으로만 판정한다.
④ TurboQuant 이론과 구현 분리:
   - 극좌표 양자화(Polar Quantization) 수학적 이론 일반과 실제 제공된 오픈소스 라이브러리/커널 구현의 성숙도를 분리하여 평가한다."""

MATURITY_SYSTEM_PROMPT = f"""당신은 데이터센터 인프라 기술의 실용화 가능성을 검증하는 기술성숙도(TRL) 수석 평가관입니다.
제공된 논문 근거를 바탕으로 TurboQuant와 ITME의 TRL을 객관적으로 판정하십시오.

{TRL_RULES}

### [TRL 보고서 작성 형식]
본문은 반드시 다음 순서로 기술하십시오:
1. TurboQuant TRL 평가:
   - 양자화 이론 일반과 TurboQuant 구현 분리 서술
   - 확인된 최고 구간 (TRL X) 및 논문 실증 근거 [12자리hex]
   - 상위 구간 미도달 구체 사유 (SW에 수율 언급 금지)
2. ITME TRL 평가:
   - CXL 표준 / 상용 CMM-D 제품 / ITME 아키텍처 성숙도 3단계 분리 서술
   - ITME 아키텍처 기준 확인된 최고 구간 (TRL X) 및 프로토타입/시뮬레이션 근거 [12자리hex]
   - 상위 구간 미도달 구체 사유
3. 발표 시기(연도)에 의한 가감 없이 순수 실증 근거에 기반한 요약

[출력 마지막 줄 형식 강제]
본문의 맨 마지막 줄은 반드시 아래 규격 중 하나로 작성하십시오:
근거 공백: <공백 내용 1> | <공백 내용 2>
(공백이 없을 경우) 근거 공백: 없음"""

TECHS = ["TurboQuant", "ITME"]

BASE_QUERIES = [
    "실험 환경 하드웨어 구현 공개 범위 prototype testbed code repository",
    "실제 배포 운영 사례 프로토타입 시뮬레이션 production workload evaluation trace",
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
        tech_matched = "both"
        lower_item = item.lower()
        if "turboquant" in lower_item and "itme" not in lower_item:
            tech_matched = "TurboQuant"
        elif "itme" in lower_item and "turboquant" not in lower_item:
            tech_matched = "ITME"

        gap_objects.append(
            Gap(
                role="maturity",
                technology=tech_matched,
                item=item,
                reason="TRL 상위 구간 미도달 또는 실증 데이터 미확인",
            )
        )
    return gap_objects


def _split_technology_sections(text: str) -> Tuple[str, str]:
    """본문에서 TurboQuant 서술부와 ITME 서술부를 분리."""
    tq_pattern = r"(?:^|\n)(?:1\.\s*TurboQuant|###\s*1\.\s*TurboQuant|TurboQuant\s*TRL)"
    itme_pattern = r"(?:^|\n)(?:2\.\s*ITME|###\s*2\.\s*ITME|ITME\s*TRL)"

    tq_match = re.search(tq_pattern, text, re.IGNORECASE)
    itme_match = re.search(itme_pattern, text, re.IGNORECASE)

    if tq_match and itme_match:
        tq_start = tq_match.start()
        itme_start = itme_match.start()
        if tq_start < itme_start:
            tq_text = text[tq_start:itme_start].strip()
            itme_text = text[itme_start:].strip()
            return tq_text, itme_text

    # 패턴 매칭 실패 시 fallback (전체 본문 반환)
    return text.strip(), ""


# ============================================================================
# 2. LangGraph Maturity 노드 함수
# ============================================================================
def maturity(state: Dict[str, Any], llm: Optional[BaseChatModel] = None) -> Dict[str, Any]:
    """papers_core에서 TRL 실증 근거를 수집하고 schema.Assessment를 구성하여 반환한다.

    State 14필드 규약:
      - 입력: run_id, run_config, domain (선택)
      - 반환: maturity (Assessment dict), trace (Event dict list)
    """
    if llm is None:
        llm = get_llm()

    run_id = state.get("run_id", "default_run")
    domain = state.get(
        "domain",
        state.get("run_config", {}).get("domain", "데이터센터/클라우드 추론 인프라"),
    )

    # 1. R3 계약에 따른 maturity 전용 검색 도구 바인딩 (papers_core, direct/comparison 자동 강제)
    doc_search = bind_document_search("maturity")

    accumulated_chunks: List[Dict[str, Any]] = []
    chunk_index_map: Dict[str, Dict[str, Any]] = {}

    def _execute_search(query_list: List[str], tech: str):
        for q in query_list:
            hits = doc_search.invoke({"query": q, "technology": tech, "top_k": 4})
            for c in hits:
                cid = c.get("chunk_id")
                if cid and cid not in chunk_index_map:
                    chunk_index_map[cid] = c
                    accumulated_chunks.append(c)

    # 2. 1차 기본 검색 수행
    for tech in TECHS:
        _execute_search(BASE_QUERIES, tech)

    # 3. 보완 검색 (노드 내부 1회 루프): 기술별 코드/테스트베드 근거 부족 시 추가 탐색
    supplemental_searches = 0
    for tech in TECHS:
        has_tech_chunks = any(
            tech in c.get("applies_to", [])
            for c in accumulated_chunks
        )
        if not has_tech_chunks:
            supp_q = (
                "TurboQuant open-source code benchmark reproduce github HuggingFace kernel"
                if tech == "TurboQuant"
                else "ITME FPGA ASIC emulation hardware testbed gem5 simulation prototype"
            )
            _execute_search([supp_q], tech)
            supplemental_searches += 1

    # 4. LLM 실행
    prompt_context = format_chunks(accumulated_chunks)
    generated_text = run_node(MATURITY_SYSTEM_PROMPT, domain, prompt_context)

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
                allowed_uses=["maturity", "research"],
                title=c.get("title", sid),
            )

        evidence_map[cid] = Evidence(
            evidence_id=cid,
            source_id=sid,
            run_id=run_id,
            collection=collection_name,
            allowed_uses=["maturity"],
            quote=c.get("text", "")[:300],
            location=f"p.{c.get('page', 1)}",
        )

    # 6-2. 상태 판정 (completed / partial / failed)
    # 가짜 인용 ID가 발생했거나, 유효 인용이 전혀 없으면 즉시 failed
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

    # 6-3. Claim 구성: status=failed인 경우 규약에 따라 반드시 claims=[] 강제
    claims: List[Claim] = []
    if status != "failed":
        tq_text, itme_text = _split_technology_sections(generated_text)

        if tq_text and itme_text:
            # 기술별 Claim 분리 생성
            tq_cits = [cid for cid in HEX_CITATION_RE.findall(tq_text) if cid in cited_ids_set]
            itme_cits = [cid for cid in HEX_CITATION_RE.findall(itme_text) if cid in cited_ids_set]

            claims.append(
                Claim(
                    claim_id=f"claim_maturity_tq_{run_id[:8]}",
                    text=tq_text,
                    technology="TurboQuant",
                    kind="fact",
                    evidence_ids=list(dict.fromkeys(tq_cits)),
                )
            )
            claims.append(
                Claim(
                    claim_id=f"claim_maturity_itme_{run_id[:8]}",
                    text=itme_text,
                    technology="ITME",
                    kind="fact",
                    evidence_ids=list(dict.fromkeys(itme_cits)),
                )
            )
        else:
            # 섹션 분리가 안 된 경우 fallback
            claims.append(
                Claim(
                    claim_id=f"claim_maturity_all_{run_id[:8]}",
                    text=generated_text,
                    technology="both",
                    kind="fact",
                    evidence_ids=list(dict.fromkeys(valid_citations)),
                )
            )

    # 6-4. Gap 객체 구성
    gap_objects = _extract_gaps_from_text(generated_text)

    # 6-5. Assessment 최종 조립
    assessment = Assessment(
        status=status,
        claims=claims,
        sources=list(sources_map.values()),
        evidence=list(evidence_map.values()),
        gaps=gap_objects,
    )

    # 7. trace 이벤트 생성 (Event 스키마 호환 dict, 누적 리듀서 등록)
    trace_event = {
        "node": "maturity",
        "status": status,
        "attempt": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chunks": len(accumulated_chunks),
        "citations": len(valid_citations),
        "invalid_citations": len(invalid_citations),
        "supplemental_searches": supplemental_searches,
        "covered_techs": sorted(list(covered_techs)),
    }

    # 8. State 14필드 단독 쓰기 반환
    return {
        "maturity": assessment.model_dump(),
        "trace": [trace_event],
    }