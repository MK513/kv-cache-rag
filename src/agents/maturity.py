"""TRL(기술성숙도) 평가 노드 — 담당: R4 | papers_core RAG (동일 인덱스, 다른 쿼리).

- 컬렉션 바인딩: papers_core 전용 (동일 인덱스를 사용하되 TRL 실증 증거 중심 질의)
- TRL 6밴드 판정 규칙 엄격 적용 (확인된 최고 구간 / 그 근거 / 상위 미도달 이유)
- 4대 가드레일 반영:
    1) CXL 표준/상용 제품과 ITME 구체 아키텍처 성숙도 3단계 엄격 분리
    2) TurboQuant(SW)에 반도체 하드웨어 '수율' 평가 기준 적용 금지
    3) 발표 시기(최신 논문 등) 기반의 TRL 자의적 가감 금지
    4) 양자화 일반 이론과 TurboQuant 실제 구현체 분리
- 단일 쓰기 주체 원칙 준수: maturity 및 trace 키만 갱신 (evidence 쓰기 금지)
"""

from typing import Any, Dict, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel

from src.agents.common import run_assessment_node
from src.tools.docs import search_source_documents
from src.llm import get_llm

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
"""

REQUIRED_MATURITY_CRITERIA = [
    "TurboQuant 확인된 최고 구간",
    "TurboQuant 최고 구간 근거",
    "TurboQuant 상위 구간 미도달 사유",
    "ITME 확인된 최고 구간",
    "ITME 최고 구간 근거",
    "ITME 상위 구간 미도달 사유",
]

# ============================================================================
# 2. 질의 목록 및 보완 검색 빌더
# ============================================================================
BASE_QUERIES = [
    "실험 환경 하드웨어 구현 공개 범위 prototype testbed code repository",
    "실제 배포 운영 사례 프로토타입 시뮬레이션 production workload evaluation trace",
]


def build_maturity_supplemental_queries(missing_items: List[str]) -> List[Dict[str, Any]]:
    """필수 평가 항목(최고 구간, 근거, 미도달 사유) 결여 시 국소 보완 검색 질의를 생성합니다."""
    queries = []
    for item in missing_items:
        if "TurboQuant" in item:
            tech = "TurboQuant"
            q = "TurboQuant open-source code benchmark reproduce github HuggingFace kernel implementation"
        else:
            tech = "ITME"
            q = "ITME FPGA ASIC emulation hardware testbed gem5 simulation prototype environment"

        queries.append({
            "query": q,
            "collection": "papers_core",
            "technology": tech,
            "perspective": "maturity",
            "top_k": 4,
        })
    return queries


# ============================================================================
# 3. LangGraph Maturity 노드 함수
# ============================================================================
def maturity(state: Dict[str, Any], llm: Optional[BaseChatModel] = None) -> Dict[str, Any]:
    """LangGraph maturity 노드 실행 함수.

    - papers_core 컬렉션에서 TRL 전용 쿼리로 검색
    - 단일 평가 실행 엔진(run_assessment_node)을 통해 보완 검색 및 인용 자가 수정 1회 수행
    - State 17키 계약: maturity 키와 trace 키만 반환 (단일 쓰기 원칙 준수)
    """
    if llm is None:
        llm = get_llm()

    # 1. 1차 기본 검색 질의 구성 (양 기술 × 2개 기준 질의)
    initial_queries = []
    for tech in ["TurboQuant", "ITME"]:
        for q in BASE_QUERIES:
            initial_queries.append({
                "query": q,
                "collection": "papers_core",
                "technology": tech,
                "perspective": "maturity",
                "top_k": 4,
            })

    # 2. 공통 평가 실행 함수 호출
    assessment_result = run_assessment_node(
        node_name="maturity",
        state_key="maturity",
        system_prompt=MATURITY_SYSTEM_PROMPT,
        initial_queries=initial_queries,
        allowed_collections=["papers_core"],
        search_func=search_source_documents,
        llm=llm,
        required_criteria=REQUIRED_MATURITY_CRITERIA,
        supplemental_query_builder=build_maturity_supplemental_queries,
        extra_instructions=(
            "### [TRL 보고서 작성 형식]\n"
            "본문은 반드시 다음 순서로 기술하십시오:\n"
            "1. TurboQuant TRL 평가:\n"
            "   - 양자화 이론 일반과 TurboQuant 구현 분리 서술\n"
            "   - 확인된 최고 구간 (TRL X) 및 논문 실증 근거 [12자리hex]\n"
            "   - 상위 구간 미도달 구체 사유 (SW에 수율 언급 금지)\n"
            "2. ITME TRL 평가:\n"
            "   - CXL 표준 / 상용 CMM-D 제품 / ITME 아키텍처 성숙도 3단계 분리 서술\n"
            "   - ITME 아키텍처 기준 확인된 최고 구간 (TRL X) 및 프로토타입/시뮬레이션 근거 [12자리hex]\n"
            "   - 상위 구간 미도달 구체 사유\n"
            "3. 발표 시기(연도)에 의한 가감 없이 순수 실증 근거에 기반한 요약"
        ),
    )

    # 3. State 17키 규격 반환 (evidence에 쓰지 않고 maturity와 trace만 반환)
    return {
        "maturity": assessment_result["maturity"],
        "trace": assessment_result.get("trace", []),
    }