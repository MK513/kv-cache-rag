"""결정 로그 — 0단계 계약 (계획서 §6-2 관측성 위치)

계획·재시도 대상 선택·품질 판정의 **결정과 사유 전문**은 State 가 아니라
`runs/<run_id>/decisions.jsonl` 에 남긴다. State 에는 `last_decision` 요약만 둔다.
`run_id` 가 State·이 파일·LangSmith `metadata.run_id` 를 잇는 상관 키다.

orchestrator(담당 A)와 quality_eval(담당 C)이 함께 쓴다. 0단계 이후 동결.
"""

import json
from datetime import datetime, timezone

from src.state import run_dir


def log_decision(state, node: str, decision: str, reason: str, **fields) -> dict:
    """결정 한 줄을 decisions.jsonl 에 append 하고, State 의 last_decision 으로 쓸 요약을 돌려준다."""
    record = {"run_id": state["run_id"], "node": node, "decision": decision, "reason": reason,
              "ts": datetime.now(timezone.utc).isoformat(), **fields}
    path = run_dir(state) / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"node": node, "decision": decision, "reason": reason, "ts": record["ts"]}
