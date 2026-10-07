"""품질 평가와 재작업 라우팅 — 담당 C (agent/ow-quality). 0단계 계약 스텁.

계약 (계획서 §7):
- `quality_eval(state)` → {"quality_eval": schema.QualityEval dict, "claim_flags", "repair_count",
  "step_count", "stop_reason", "trace"}. 다음 노드는 `quality_eval["next"]` 에 정해 둔다.
- `route_after_eval(state)` → `quality_eval["next"]` 를 그대로 돌려준다(그래프는 이것만 읽는다).
- `apply_review(state)` → Human Review 판정을 읽어 부결 Claim 을 `claim_flags` 에
  `{status: "invalid", by: "human_review"}` 로 기록한다.
"""


def quality_eval(state) -> dict:
    raise NotImplementedError("quality_eval 은 담당 C 가 agent/ow-quality 에서 구현한다 (계획서 §7)")


def route_after_eval(state) -> str:
    """그래프 조건부 엣지. 판단은 quality_eval 이 하고 여기서는 결과만 읽는다."""
    return state["quality_eval"]["next"]


def apply_review(state) -> dict:
    raise NotImplementedError("apply_review 는 담당 C 가 agent/ow-quality 에서 구현한다 (계획서 §9)")
