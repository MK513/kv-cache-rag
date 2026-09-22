"""State 스키마 17키 — 담당: R1 (설계서 §7, v13)

병렬 실행되는 네 평가 노드는 각자 자기 키만 갱신한다.
누적이 필요한 trace 만 명시적 reducer 를 쓴다.

관점 dict 공통 구조:  {"text": str, "citations": list, "gaps": list[str]}
  - papers_core/ecosystem 기반 노드의 citations 는 chunk_id 문자열 목록
  - 웹 기반 노드(stakeholder)의 citations 는 url 문자열 목록 (원본은 web_sources)
"""

import operator
from typing import Annotated, TypedDict


class ReportState(TypedDict, total=False):
    # ── 입력 · 출처 ──
    domain: Annotated[str, "평가 도메인 (데이터센터/클라우드)"]
    sources: Annotated[list[dict], "색인 매니페스트 — id/collection/sha256/pages/chunks"]
    web_sources: Annotated[list[dict], "웹 조회 원본 — url/title/published_at/retrieved_at/source_type"]

    # ── 기술 조사 (evidence 단일 쓰기 주체) ──
    evidence: Annotated[list[dict], "chunk_id/collection/source/page/text — research 만 갱신"]
    research: Annotated[dict, "{text, citations, gaps}"]
    tech_status: Annotated[dict, "기술별 'ok' | '평가 보류' — 보완 후에도 근거 미확보 시"]
    gaps: Annotated[list[str], "기술 조사 단계의 근거 공백"]
    retrieval_round: Annotated[int, "질의 보완 횟수. §8 최대 1회"]

    # ── 병렬 평가: 각 노드가 자기 키에만 쓴다 ──
    maturity: Annotated[dict, "TRL — papers_core RAG"]
    market: Annotated[dict, "시장성 — ecosystem RAG"]
    stakeholder: Annotated[dict, "이해관계자 — 웹"]
    domain_assessment: Annotated[dict, "도메인 적용 — papers_core + context RAG"]

    # ── 종합 · 검증 · 출력 ──
    synthesis: Annotated[dict, "일치/상충/gaps 병합"]
    validation_errors: Annotated[list[dict], "인용 검증 결과 — output/validate.py 단독 쓰기"]
    validation_round: Annotated[int, "재작성 횟수. 한도 초과 시 오류 기록 후 종료"]
    report: Annotated[str, "최종 Markdown"]

    # ── 누적 ──
    trace: Annotated[list[dict], operator.add]
