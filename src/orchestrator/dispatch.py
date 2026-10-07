"""Dynamic Fan-out — 담당 A (계획서 §4)

`plan` 의 Task 마다 Worker 하나를 `Send` 로 띄운다. Worker 수 = 계획의 길이.
계획이 비면(보낼 대상 없음) 더 조사하지 않고 발행으로 간다.
"""

from langgraph.types import Send


def dispatch(state):
    plan = state.get("plan") or []
    if not plan:
        return "publish"
    return [Send("worker", state | {"task": task}) for task in plan]
