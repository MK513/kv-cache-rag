"""worker 노드 — 담당 A (계획서 §4)

`Send("worker", state | {"task": t})` 로 Task 하나를 받아 기존 에이전트를 실행한다.
Worker 는 노드 하나이고, `task["perspective"]` 로 에이전트를 고른다 — 에이전트 코드는 그대로 둔다.

**그래프 레벨 Fallback**: 에이전트가 예외를 던지면 잡아서 `status="failed"` 결과로 바꾼다.
병렬로 도는 Worker 하나의 예외가 그래프 전체를 멈추지 않는다. 재시도·제외는 다음 라운드에
Orchestrator 가 `round` 로 고른다.

어댑터(`adapt`)가 하는 일
- Task 의 대상 기술 Claim·Gap 과 그 Claim 이 참조하는 Evidence·Source 만 남긴다.
- Claim ID 를 `"{task_id}:{원래 id}"` 로 바꾼다. 에이전트는 관점·run_id 로만 ID 를 만들어
  (`market.py:137` 등) 같은 관점 Worker 둘이나 재계획 라운드가 같은 ID 를 낸다.
- 결과 원본을 `runs/<run_id>/workers/<task_id>.json` 에 원자적으로 저장한다. 저장 자체가
  실패하면 결과를 만들 수 없으므로 잡지 않는다(실행 오류).

반환은 `worker_results`·`trace` 두 키뿐이다 — 둘 다 operator.add reducer 라 병렬 Worker 가
같은 단계에 써도 충돌하지 않는다.
"""

from src.agents.common import event
from src.agents.domain import domain_assessment
from src.agents.market import market
from src.agents.maturity import maturity
from src.agents.stakeholder import stakeholder
from src.schema import Assessment, Task, WorkerMeta, WorkerResult
from src.state import run_dir
from src.tools.web_store import save_json

WORKERS = {"maturity": maturity, "market": market,
           "stakeholder": stakeholder, "domain_assessment": domain_assessment}

# 다시 시도하면 성공할 수 있는 오류. 클래스 이름으로 본다 — OpenAI·requests·httpx 를
# 여기서 import 하지 않아도 되고, 하위 클래스도 MRO 로 잡힌다.
TRANSIENT = {"TimeoutError", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout",
             "APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError",
             "ServiceUnavailableError"}


def classify(exc: BaseException) -> str:
    """일시 오류면 transient(다음 라운드 재시도), 그 밖은 permanent(제외)."""
    if any(cls.__name__ in TRANSIENT for cls in type(exc).__mro__):
        return "transient"
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None),
                                                          "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return "transient"
    return "permanent"


def adapt(out: dict, task: Task) -> dict:
    """에이전트 Assessment 를 Task 범위로 줄이고 Claim ID 를 전역 유일하게 만든다."""
    assessment = Assessment.model_validate(out).model_dump()
    techs = set(task.technologies)
    both = len(techs) > 1

    def in_scope(technology):
        return technology in techs or (technology == "both" and both)

    claims = [dict(c, claim_id=f"{task.task_id}:{c['claim_id']}")
              for c in assessment["claims"] if in_scope(c["technology"])]
    cited = {eid for c in claims for eid in c["evidence_ids"]}
    evidence = [e for e in assessment["evidence"] if e["evidence_id"] in cited]
    used_sources = {e["source_id"] for e in evidence}
    sources = [s for s in assessment["sources"] if s["source_id"] in used_sources]
    gaps = [g for g in assessment["gaps"] if in_scope(g["technology"])]

    # 에이전트의 "failed" 는 실행 실패가 아니라 근거를 못 만들었다는 뜻이다(근거 0건·무효 인용).
    # 실행 실패는 예외로만 표현하므로 여기서는 partial 로 둔다.
    status = assessment["status"]
    if status == "failed" or not claims:
        status = "partial"
    return Assessment.model_validate({"claims": claims, "evidence": evidence, "sources": sources,
                                      "gaps": gaps, "status": status}).model_dump()


def make_worker(workers: dict | None = None):
    """테스트·실행이 에이전트를 바꿔 끼울 수 있게 Worker 노드를 만든다."""
    table = WORKERS | (workers or {})

    def worker(state) -> dict:
        task = Task.model_validate(state["task"])
        path = run_dir(state) / "workers" / f"{task.task_id}.json"
        try:
            raw = table[task.perspective](state)
            meta = WorkerMeta.model_validate(raw.get("worker_meta") or {})
            assessment = adapt(raw[task.perspective], task)
            result = WorkerResult(task=task, round=task.round, status=assessment["status"],
                                  assessment=assessment, meta=meta, result_path=str(path))
        except Exception as exc:                     # Fallback: 그래프를 죽이지 않는다
            result = WorkerResult(task=task, round=task.round, status="failed",
                                  error_kind=classify(exc),
                                  error=f"{type(exc).__name__}: {exc}"[:500],
                                  result_path=str(path))
        record = result.model_dump()
        save_json(path, record)
        return {"worker_results": [record],
                "trace": [event("worker", result.status, task_id=task.task_id,
                                perspective=task.perspective, round=task.round,
                                error_kind=result.error_kind)]}

    return worker


worker = make_worker()
