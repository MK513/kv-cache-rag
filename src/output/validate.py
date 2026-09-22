"""R5 자동 검증.

LLM 채점이 아니라 결정적 검사를 수행한다.

지원 구조
1) 구형 Assessment
   {text, citations, gaps, bad_citations}

2) 신형 Assessment
   {
       claims: [...],
       evidence: [...],
       sources: [...],
       gaps: [...],
       status: ...
   }

자동 검증은 구조와 출처 연결을 확인한다.
Claim 내용이 실제 근거에서 의미적으로 도출되는지는
human review worksheet에서 사람이 확인한다.
"""


RAG_NODES = {
    "research": {"papers_core", "ecosystem", "context"},
    "maturity": {"papers_core"},
    "market": {"ecosystem"},
    "domain_assessment": {"papers_core", "context"},
}

WEB_NODES = {"stakeholder"}


def _build_index():
    """RAG index를 실제 검증 시점에만 로드한다.

    validate.py import 시점에 pdfplumber 등 R2 의존성을 불러오지 않도록
    지연 import한다. 단위 테스트에서는 이 함수를 monkeypatch한다.
    """
    from src.rag.index import build

    return build()


def _legacy_rag_errors(
    node_name: str,
    assessment: dict,
    valid_chunk_ids: set[str],
) -> list[dict]:
    """구형 {text, citations, bad_citations} 구조를 검사한다."""
    errors = []

    citations = assessment.get("citations") or []
    bad = set(assessment.get("bad_citations") or [])

    # common.py를 거치지 않은 데이터도 방어적으로 다시 검사
    bad.update(
        citation
        for citation in citations
        if citation not in valid_chunk_ids
    )

    if bad:
        errors.append({
            "node": node_name,
            "kind": "없는 인용 ID",
            "ids": sorted(bad),
        })

    if not citations:
        errors.append({
            "node": node_name,
            "kind": "인용 누락",
            "ids": [],
        })

    return errors


def _structured_errors(
    node_name: str,
    assessment: dict,
    current_run_id: str | None,
) -> list[dict]:
    """신형 Claim → Evidence → Source 구조를 검사한다."""
    errors = []

    claims = assessment.get("claims") or []
    evidence_list = assessment.get("evidence") or []
    source_list = assessment.get("sources") or []

    evidence_by_id = {
        evidence.get("evidence_id"): evidence
        for evidence in evidence_list
        if evidence.get("evidence_id")
    }

    source_by_id = {
        source.get("source_id"): source
        for source in source_list
        if source.get("source_id")
    }

    seen_claim_ids = set()

    for claim in claims:
        claim_id = claim.get("claim_id")

        if not claim_id:
            errors.append({
                "node": node_name,
                "kind": "claim_id 누락",
                "ids": [],
            })
            continue

        if claim_id in seen_claim_ids:
            errors.append({
                "node": node_name,
                "kind": "claim_id 중복",
                "ids": [claim_id],
            })

        seen_claim_ids.add(claim_id)

        evidence_ids = claim.get("evidence_ids") or []
        claim_kind = claim.get("kind")

        # 사실 Claim은 직접 근거 필수
        if claim_kind == "fact" and not evidence_ids:
            errors.append({
                "node": node_name,
                "claim_id": claim_id,
                "kind": "사실 Claim 근거 누락",
                "ids": [],
            })

        # 추론/가설도 전제 근거 필수
        if claim_kind in {"inference", "hypothesis"} and not evidence_ids:
            errors.append({
                "node": node_name,
                "claim_id": claim_id,
                "kind": "추론·가설 전제 근거 누락",
                "ids": [],
            })

        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id)

            if evidence is None:
                errors.append({
                    "node": node_name,
                    "claim_id": claim_id,
                    "kind": "Evidence 없음",
                    "ids": [evidence_id],
                })
                continue

            source_id = evidence.get("source_id")

            if not source_id or source_id not in source_by_id:
                errors.append({
                    "node": node_name,
                    "claim_id": claim_id,
                    "kind": "Source 연결 실패",
                    "ids": [evidence_id],
                })

            evidence_run_id = evidence.get("run_id")

            if (
                current_run_id
                and evidence_run_id
                and evidence_run_id != current_run_id
            ):
                errors.append({
                    "node": node_name,
                    "claim_id": claim_id,
                    "kind": "다른 run의 Evidence",
                    "ids": [evidence_id],
                })

            source = source_by_id.get(source_id, {})
            source_run_id = source.get("run_id")

            if (
                current_run_id
                and source_run_id
                and source_run_id != current_run_id
            ):
                errors.append({
                    "node": node_name,
                    "claim_id": claim_id,
                    "kind": "다른 run의 Source",
                    "ids": [source_id],
                })

            # allowed_uses가 존재하면 노드 사용 권한 검사
            allowed_uses = evidence.get("allowed_uses")

            if allowed_uses is not None:
                expected_use = (
                    "domain"
                    if node_name == "domain_assessment"
                    else node_name
                )

                if expected_use not in allowed_uses:
                    errors.append({
                        "node": node_name,
                        "claim_id": claim_id,
                        "kind": "허용되지 않은 Evidence 사용",
                        "ids": [evidence_id],
                    })

            # RAG 자료의 collection 범위 검사
            if node_name in RAG_NODES:
                collection = evidence.get("collection")

                if (
                    collection
                    and collection not in RAG_NODES[node_name]
                ):
                    errors.append({
                        "node": node_name,
                        "claim_id": claim_id,
                        "kind": "허용되지 않은 collection",
                        "ids": [evidence_id],
                    })

    return errors


def check(state, stage: str) -> dict:
    """현재 State의 자동 검증 결과를 반환한다."""
    valid_chunk_ids = set(_build_index()["chunks"])
    current_run_id = state.get("run_id")

    errors = []

    node_names = [
        "research",
        "maturity",
        "market",
        "stakeholder",
        "domain_assessment",
    ]

    for node_name in node_names:
        assessment = state.get(node_name) or {}

        if not assessment:
            continue

        # 신형 구조
        if "claims" in assessment:
            errors.extend(
                _structured_errors(
                    node_name=node_name,
                    assessment=assessment,
                    current_run_id=current_run_id,
                )
            )

        # 구형 RAG 구조
        elif node_name in RAG_NODES:
            errors.extend(
                _legacy_rag_errors(
                    node_name=node_name,
                    assessment=assessment,
                    valid_chunk_ids=valid_chunk_ids,
                )
            )

    # conflicts == [] 는 정상일 수 있으므로 오류로 처리하지 않는다.

    return {
        "validation_errors": errors,
        "validation_round": (
            state.get("validation_round", 0)
            + (1 if stage == "post_synthesis" else 0)
        ),
        "trace": [{
            "node": f"validate:{stage}",
            "errors": len(errors),
        }],
    }