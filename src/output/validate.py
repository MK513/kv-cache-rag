"""R5 자동 검증.

LLM 채점이 아니라 결정적 검사를 수행한다.

검사 대상은 `schema.Assessment` 다 — claims / evidence / sources / gaps / status.

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


def check(state) -> dict:
    """현재 State의 자동 검증 결과를 State 필드 `validation` 으로 반환한다.

    설계서 §8 *형식 검사* — Claim에 근거 ID가 연결됐는지, ID가 이번 실행에서 확보한
    자료인지, 노드별 허용 자료인지 확인한다. 내용 검토(사람)는 `review.py` 가 맡는다.

    재시도 횟수는 세지 않는다. 부록 A: *재시도는 노드 내부의 최대 1회 처리다.*
    """
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

        errors.extend(
            _structured_errors(
                node_name=node_name,
                assessment=assessment,
                current_run_id=current_run_id,
            )
        )

    # conflicts == [] 는 정상일 수 있으므로 오류로 처리하지 않는다.

    return {
        "validation": {
            "errors": errors,
            "checked_nodes": node_names,
        },
        "trace": [{
            "node": "validate",
            "status": "failed" if errors else "ok",
            "errors": len(errors),
        }],
    }