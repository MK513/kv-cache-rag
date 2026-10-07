# 담당 B — README 반영용 (A 가 조립)

## Agents — Workers

| 에이전트 | 근거 | Task 입력 시 |
|---|---|---|
| research (선행, Worker 아님) | papers_core `direct` | Task 를 받지 않는다. Orchestrator 계획의 입력 |
| maturity | papers_core `direct·comparison` | `task.queries` × 대상 기술로 검색, 대상 기술 절만 Claim |
| market | ecosystem | `task.queries` 를 대상 기술(둘이면 both)로 검색 |
| domain_assessment | papers_core + context(배경) | `task.queries` × 대상 기술, 결합 가설 Claim(`both`)은 두 기술 Task 일 때만 만든다 |
| stakeholder | 웹 원문(Tavily → 본문 수집) | 대상 기술 × 4주체 질의(주체 질의 뒤에 `task.queries` 를 붙임, 검색 횟수는 그대로). 검색 상한 = `task.web_budget`, 기록은 `runs/<run_id>/workers/<task_id>/` |

- Task 의 `focus`·`rationale` 은 지시문 끝에 붙는다. 프롬프트가 달라지므로 LLM 캐시가 이전 라운드 응답을 재생하지 않는다.
- Worker 로 호출되면 `worker_meta = {attempted_queries, retrieved, web_searches_used}` 를 함께 반환한다. 이 값은 LLM 이 아니라 코드가 센다 — 조사를 실제로 했다는 근거다.
  - `attempted_queries`: 실제로 실행된 질의만. 웹 예산으로 차단된 검색은 넣지 않는다.
  - `retrieved`: 문서 에이전트는 대상 기술에 해당하는 유사도 ≥ τ(`orchestrator.split.tau`) 청크 수(domain 의 context 고정 배경 검색은 질의·청크 모두 제외), stakeholder 는 본문 수집에 성공한 원문 수.

## Features — 에이전트 쪽 근거 공백 종류

| 상황 | Gap.kind |
|---|---|
| 조사했으나 근거 없음 / 인용이 없는 절 | `evidence_gap` |
| 검색 결과에 없는 인용 ID (인용 위조) | `invalid_evidence` — 커버리지 통과 근거가 되지 못한다 |
| stakeholder 에서 검색하지 못한 주체(예산 소진·검색 실패), 웹 검색 전부 실패 | `execution_gap` |
| stakeholder 초안 인용이 끝내 검증 실패 — 검색 결과가 있던 주체 / 결과 0건이던 주체 | `invalid_evidence` / `evidence_gap` |

- Task 가 한 기술만 맡으면 LLM 의 `근거 공백` 줄에서 다른 기술 항목은 버리고, 기술이 적히지 않은 항목은 그 기술로 둔다.
- `근거 공백` 줄은 Claim 본문에 넣지 않는다(`common.claim_body`) — Gap 으로만 남는다.

(Worker 예외로 인한 `execution_gap` 은 A 의 worker 어댑터가 만든다. 에이전트는 LLM 호출 오류를 잡지 않고 올린다 — stakeholder 도 초안 검증 실패(`ValueError`)만 수리하고 나머지는 worker 의 `classify()` 로 넘긴다.)

## State — Worker 가 읽고 쓰는 항목

| 항목 | 방향 | 설명 |
|---|---|---|
| `task` | 읽기 | Send 로 받는 `schema.Task`. 에이전트가 읽는 State 는 이것과 `run_id`·`run_config`·`domain` 뿐이다 |
| `research` | 쓰기 | 선행 research 노드만. Orchestrator 계획의 입력 |
| 관점별 결과 + `trace` + `worker_meta` | 반환 | State 에 직접 쓰지 않는다. A 의 worker 어댑터가 `worker_results` 로 옮기고, `collect_evidence` 가 관점별 Assessment 4개로 조립한다 |

## 공통 함수 정리

에이전트마다 있던 `TECHS`·인용 정규식·근거 공백 파서·trace 조립·검색 누적 루프·기술별 Claim 분리를 `src/schema.py`·`src/agents/common.py` 하나로 모았다.

## Contributors

- B : Worker 에이전트(Task 기반 조사), 근거 수집·웹 수집 분리, 공통 함수 정리
