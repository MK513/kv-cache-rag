"""Human semantic review worksheet — 담당: R5

자동 검증만으로 Claim을 승인하지 않는다.

각 Claim과 연결된 Evidence를 사람이 직접 비교할 수 있도록
검토용 CSV worksheet를 생성한다.

검토 대상:
- claim_id
- 주장 내용
- 근거 원문
- 출처
- 수치/단위/기준선/실험 조건
- TRL 근거
- 직접 채택 vs 인접 생태계 구분

review_result와 reviewer는 사람이 직접 입력한다.
"""

import csv
from pathlib import Path

from src.output import validate
from src.state import run_dir


REVIEW_NODES = [
    "research",
    "maturity",
    "market",
    "stakeholder",
    "domain_assessment",
]


def _evidence_map(assessment: dict) -> dict:
    """Assessment 내부 Evidence를 evidence_id 기준 dict로 만든다."""
    return {
        evidence["evidence_id"]: evidence
        for evidence in assessment.get("evidence", [])
        if evidence.get("evidence_id")
    }


def _source_map(assessment: dict) -> dict:
    """Assessment 내부 Source를 source_id 기준 dict로 만든다."""
    return {
        source["source_id"]: source
        for source in assessment.get("sources", [])
        if source.get("source_id")
    }


def _claim_text(claim: dict) -> str:
    """Claim schema의 실제 필드명이 달라져도 기본적으로 대응한다."""
    return (
        claim.get("text")
        or claim.get("claim")
        or claim.get("statement")
        or ""
    )


def _evidence_text(evidence: dict) -> str:
    """논문/웹 Evidence의 원문 구절을 공통 처리한다."""
    return (
        evidence.get("quote")
        or evidence.get("text")
        or ""
    )


def _source_location(evidence: dict, source: dict) -> str:
    """논문은 page, 웹은 URL을 우선 표시한다."""
    page = evidence.get("page")

    if page is not None:
        return f"page {page}"

    return source.get("url") or evidence.get("url") or ""


def build_review_rows(state: dict) -> list[dict]:
    """State의 Assessment들을 사람이 검토할 수 있는 row로 변환한다."""
    rows = []

    for node_name in REVIEW_NODES:
        assessment = state.get(node_name) or {}

        # 아직 구형 {text, citations, gaps} 구조인 노드는 건너뛴다.
        claims = assessment.get("claims")
        if not claims:
            continue

        evidence_by_id = _evidence_map(assessment)
        source_by_id = _source_map(assessment)

        for claim in claims:
            claim_id = claim.get("claim_id", "")
            evidence_ids = claim.get("evidence_ids") or []

            # 근거 없는 Claim도 worksheet에 남긴다.
            if not evidence_ids:
                rows.append({
                    "claim_id": claim_id,
                    "node": node_name,
                    "technology": claim.get("technology", ""),
                    "claim_kind": claim.get("kind", ""),
                    "claim_text": _claim_text(claim),
                    "evidence_id": "",
                    "source_id": "",
                    "source_type": "",
                    "location": "",
                    "evidence_quote": "",
                    "review_result": "",
                    "reviewer": "",
                    "review_comment": "",
                })
                continue

            for evidence_id in evidence_ids:
                evidence = evidence_by_id.get(evidence_id, {})
                source_id = evidence.get("source_id", "")
                source = source_by_id.get(source_id, {})

                rows.append({
                    "claim_id": claim_id,
                    "node": node_name,
                    "technology": claim.get("technology", ""),
                    "claim_kind": claim.get("kind", ""),
                    "claim_text": _claim_text(claim),
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "source_type": source.get("source_type", source.get("type", "")),
                    "location": _source_location(evidence, source),
                    "evidence_quote": _evidence_text(evidence),
                    "review_result": "",
                    "reviewer": "",
                    "review_comment": "",
                })

    return rows


def write_review_csv(state: dict, output_path: str | Path) -> Path:
    """Human review용 CSV 파일을 생성한다."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = build_review_rows(state)

    fieldnames = [
        "claim_id",
        "node",
        "technology",
        "claim_kind",
        "claim_text",
        "evidence_id",
        "source_id",
        "source_type",
        "location",
        "evidence_quote",
        "review_result",
        "reviewer",
        "review_comment",
    ]

    with output_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path

APPROVED = {"확인", "통과", "승인", "ok", "pass", "approved"}
REJECTED = {"부결", "반려", "reject", "rejected", "fail"}


def read_verdicts(path: str | Path) -> dict[str, dict]:
    """사람이 채운 worksheet 에서 claim_id 별 판정을 읽는다.

    한 Claim 에 근거가 여러 개면 row 도 여러 개다. 먼저 채워진 판정을 그 Claim 의
    판정으로 본다. 빈 칸은 미판정이며 자동 통과시키지 않는다(§8).
    """
    path = Path(path)
    if not path.exists():
        return {}

    verdicts: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            claim_id = (row.get("claim_id") or "").strip()
            result = (row.get("review_result") or "").strip()
            if not claim_id or not result or claim_id in verdicts:
                continue
            lowered = result.lower()
            verdicts[claim_id] = {
                "result": result,
                "verdict": "확인" if lowered in APPROVED else
                           "부결" if lowered in REJECTED else "미상",
                "reviewer": (row.get("reviewer") or "").strip(),
                "comment": (row.get("review_comment") or "").strip(),
            }
    return verdicts


def _claim_ids(state: dict) -> set[str]:
    return {
        claim.get("claim_id")
        for node_name in REVIEW_NODES
        for claim in (state.get(node_name) or {}).get("claims") or []
        if claim.get("claim_id")
    }


def review(state) -> dict:
    """출처 대조와 내용 검토 (설계서 §8, 부록 A 의 `R` 노드).

    프로그램 형식 검사와 사람의 내용 검토가 한 노드다. 형식 검사는 `validate.check` 가
    하고, 내용 검토는 worksheet 를 통해 사람이 한다. **ID 대조만으로 자동 통과시키지
    않는다** — 판정이 하나라도 비어 있으면 `review_status="pending"` 이고 그래프는
    검토용 초안을 저장한 뒤 멈춘다.
    """
    checked = validate.check(state)
    validation = dict(checked["validation"])

    path = run_dir(state) / "review.csv"
    if not path.exists():
        # ponytail: 초안이 재작성되면 worksheet 가 낡는다. 판정을 지우지 않으려고
        #           덮어쓰지 않는다. 재작성이 잦아지면 claim_id 기준 병합으로 바꾼다.
        write_review_csv(state, path)

    verdicts = read_verdicts(path)
    claim_ids = _claim_ids(state)
    pending = sorted(claim_ids - set(verdicts))
    rejected = sorted(cid for cid, v in verdicts.items() if v["verdict"] == "부결")
    unclear = sorted(cid for cid, v in verdicts.items() if v["verdict"] == "미상")

    validation |= {
        "review_csv": str(path),
        "claim_verdicts": verdicts,
        "pending_claims": pending,
        # §8 — 근거를 확보하지 못한 주장은 사실 서술에서 제외한다. report 가 이 목록을 뺀다.
        "rejected_claims": rejected,
        "reviewers": sorted({v["reviewer"] for v in verdicts.values() if v["reviewer"]}),
    }
    if unclear:
        validation["errors"] = list(validation.get("errors") or []) + [
            {"node": "review", "claim_id": cid, "kind": "판정 값 미상", "ids": []}
            for cid in unclear
        ]

    status = "passed" if claim_ids and not pending else "pending"
    return {
        "validation": validation,
        "review_status": status,
        "trace": [{
            "node": "review",
            "status": status,
            "attempt": 1,
            "claims": len(claim_ids),
            "pending": len(pending),
            "rejected": len(rejected),
            "errors": len(validation.get("errors") or []),
        }],
    }
