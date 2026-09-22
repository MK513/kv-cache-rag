"""인용 검증 — 담당: R5 | validation_errors · validation_round 단독 쓰기 주체

LLM 채점이 아니라 결정적 검사다. 인덱스 실물과 대조한다.

두 지점에서 같은 노드를 재사용한다 (그래프 배선은 R1):
  ① fan-in 직후  — 평가 4노드의 인용 오류를 잡아 평가를 재실행
  ② synthesis 직후 — 종합 단계에서 새로 생긴 인용 오류를 잡아 재작성
①이 없으면 평가 노드가 만든 가짜 ID 가 §4 본문에 그대로 실린다.
"""

from src.rag.index import build

# 웹 인용(url)을 쓰는 stakeholder 는 chunk_id 검증 대상이 아니다.
CITED_NODES = ["research", "maturity", "market", "domain_assessment"]


def check(state, stage: str) -> dict:
    valid = set(build()["chunks"])
    errors = []

    for name in CITED_NODES:
        node = state.get(name) or {}
        if not node:
            continue
        bad = [c for c in node.get("bad_citations", []) if c not in valid]
        if bad:
            errors.append({"node": name, "kind": "없는 인용 ID", "ids": bad})
        elif not node.get("citations"):
            errors.append({"node": name, "kind": "인용 누락", "ids": []})

    if stage == "post_synthesis":
        s = state.get("synthesis") or {}
        if not s.get("conflicts"):
            errors.append({"node": "synthesis", "kind": "관점 상충 0건", "ids": []})

    return {
        "validation_errors": errors,
        "validation_round": state.get("validation_round", 0) + (1 if stage == "post_synthesis" else 0),
        "trace": [{"node": f"validate:{stage}", "errors": len(errors)}],
    }
