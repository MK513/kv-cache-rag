"""worker 노드 — 담당 A (agent/ow-orchestration). 0단계 계약 스텁.

계약 (계획서 §4):
- 입력: `Send("worker", state | {"task": t})` — `state["task"]` 는 schema.Task.
- `WORKERS[task["perspective"]](state)` 로 기존 에이전트를 호출한다. 에이전트 반환 계약(담당 B):
  `{역할: Assessment, "trace": [...], "worker_meta": schema.WorkerMeta}`.
- 예외는 try/except 로 잡아 `status="failed"` 결과로 바꾼다 — 그래프를 죽이지 않는다.
- 반환: `{"worker_results": [schema.WorkerResult dict], "trace": [...]}` — 두 키 모두 operator.add.
"""


def worker(state) -> dict:
    raise NotImplementedError("worker 는 담당 A 가 agent/ow-orchestration 에서 구현한다 (계획서 §4)")
