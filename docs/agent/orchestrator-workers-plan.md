# KV cache RAG — Orchestrator-Workers 패턴 적용 계획 (최종안 v8 — 3인 분업)

| 항목 | 내용 |
|---|---|
| 저장소 | `MK513/kv-cache-rag` · 기준 `main` `b687e3b` (GitHub `origin/main`. 로컬에 없으면 `git fetch origin main`) |
| 제출 브랜치 | `agent/orchestrator-workers` (통합·제출 브랜치). 작업 브랜치 3개는 §11 |
| 과제 | Notion「Multi-Agent Orchestration」— **Orchestrator-Workers** 패턴 |
| 마감 · 제출 | DAY 2 퇴근 전 · 반별 채널 Slack 스레드에 `Agent_{캠퍼스}_{X반}_{이름1+이름2+...}.zip` (Git 링크 + 트레이스 PNG + 보고서 PDF) |
| 상태 | **최종안.** §13 결정 확정, §11 3인 분업·브랜치 계획 확정. 0단계 계약 커밋 완료 — 각 레인 구현 대기 |

---

## 0. 이번 판에 반영한 것

### 0-1. 교수님 피드백 (v6 에서 전부 반영, 유지)

| # | 교수님 피드백 | 반영 위치 |
|---|---|---|
| 1 | `schema.py` 에 계획 스키마 추가 | §3 `Task`·`WorkerResult` |
| 2 | `state.py` 에 제어 상태 추가, 제어/페이로드 분리 | §6 |
| 3-1 | `dispatch` + `Send` | §4 |
| 3-2 | `worker` 노드 하나로 기존 에이전트 감싸기 (`WORKERS` dict) | §4 |
| 3-3 | `try/except` — 그래프 레벨 Fallback | §4, §5 |
| 3-4 | `round` — 재시도 대상 선택 | §3, §4-1 |
| 3-5 | `route_after_eval` + `quality_eval` 엣지 | §4, §7 |
| 3-6 | `worker → collect_evidence` — 기존 Fan-in·병합 로직 유지 | §5 |
| — | `MAX_RETRY, MAX_WORKERS = 2, 8`, 상한 시 partial 로 publish | §7, §8 |

### 0-2. `eval5.md` 타당성 검토

> eval5 는 **v4**(파일명 `최종v3`)를 평가했다. v6 에서 이미 없어진 구조(`fanin` 노드, 전체 Worker 40회, `finish` 노드, SubTask 별 재시도 카운터)에 대한 지적은 v6 구조로 옮겨 판단했다.

| # | 지적 | 판정 | v7 반영 |
|---|---|---|---|
| F1 | 실행 실패 Gap 이 커버리지 통과 근거가 될 수 있음 | **타당. v6 에도 그대로 있음**(실패 칸에 Gap 을 남기고 커버리지는 "Claim 또는 Gap"). | Gap 을 `evidence_gap`·`execution_gap`·`invalid_evidence` 로 구분. 커버리지는 코드가 기록한 조사 이력이 있는 `evidence_gap` 만 인정. 판정 단위는 실패 수가 아니라 미충족 칸 (§3, §5, §7) |
| F2 | 부분 재계획의 보존·교체·무효화 계약 불완전 | **타당. v6 는 "칸 단위 최신 라운드로 교체"라 유효 Claim 이 함께 사라질 수 있고, Claim 무효화 필드가 없음** | `claim_flags` 필드와 쓰기 담당 추가. 보존 방식은 **칸 단위 교체**로 확정(§13 결정 1) |
| F3 | 실제 예외를 실패 결과로 바꾸는 경계 | **대부분 v6 에서 해결**(교수님 `try/except`). 남은 것: 오류 분류·timeout·SDK 내부 재시도·결과 저장 실패 처리 | §4 Worker 어댑터 책임 명시 |
| F4 | Worker·웹 검색 예산의 누적 집행 | Worker 는 v6 에서 구조상 유한(라운드당 8 × 3라운드). **웹 검색 16회의 누적 집행은 타당한 지적** | 웹 검색은 실행 전체 상한, dispatch 전에 남은 예산을 계산해 Task 에 할당, 0이면 보내지 않고 `execution_gap(budget)` (§4-1, §8) |
| F5 | attempt·묶음 식별·중복·순서 | 부분 타당. v6 는 `round` 가 묶음 식별자이고 `task_id` 에 `round` 가 들어가 시도마다 다르다. 남은 것: 중복 결과 처리·정렬 기준 | `task_id` 를 결과 식별자로, 같은 id 중복은 1회만 반영·내용이 다르면 정합성 오류, 종합 입력은 `task_id` 정렬 (§5) |
| F6 | 버전 연결·publish guard·Human Review 부결 적용 | **타당.** 같은 라운드 안에서 synthesis 만 수리될 수 있어 report 의 출처 synthesis 버전이 필요. Human Review 부결을 상태에 반영하는 단계가 없음 | `synthesis_version`·`report_manifest.based_on_synthesis_version`·publish guard, `apply_review` 단계 (§6, §7, §9) |
| 보완 | 최초 계획/재계획 검증 구분, 빈 재계획, 재계획 LLM 실패 | 타당 | §4-1 |
| 보완 | 같은 역할 병렬 호출 시 ID 충돌·파일 부작용 | **타당 — 코드로 확인함.** Claim ID 가 `claim_market_{mark}_{run_id[:8]}` 처럼 관점·run_id 만으로 만들어져(`src/agents/market.py:137`, `maturity.py:269`, `domain.py:224`) 같은 관점 Worker 둘이나 재계획 라운드가 같은 ID 를 낸다. stakeholder 는 `stakeholder.json` 등을 실행 폴더 고정 경로에 쓴다(`stakeholder.py:216-218`) | Worker 어댑터가 Claim ID 를 `"{task_id}:{원래 id}"` 로 바꿈. stakeholder 출력은 Task 폴더로 (§4, §5) |
| 보완 | 사전 조사의 관련 청크 기준 | v6 에서 τ 임계값으로 반영. 중복 제거·출처 묶음 계산법 추가 | §4-1 |
| 보완 | 평가 예산 경계 | 타당 | 마지막 허용 평가가 통과면 통과로 publish, 상한은 추가 실행 **전에** 검사 (§7) |
| 보완 | State 크기 | 타당 | 결과당 Claim·Evidence 발췌 길이 상한 (§6) |
| 보완 | 10쪽 "기본 보장" 표현 | 타당. 그리고 v6 의 partial 발행 경로에서는 10쪽 초과 PDF 가 나갈 수 있다 | **최소 표시 수준 렌더링이 10쪽 이하임을 0단계에서 확인**하고, 상한 도달 시에도 최소 수준으로 압축해 발행 (§7) |
| 보완 | "설계 확정" 표현 | 타당 | 상태를 "계획. 결정 확정 후 구현"으로 변경 |
| 보완 | 동적 실증·Human Review 표현 | 타당 | §12 |
| 정정 | "LangGraph 기본 recursion limit 은 1.0.6 부터 1000" | **이 저장소 기준으로는 사실과 다름.** `uv.lock` 의 langgraph 1.2.12 설치본은 `_internal/_config.py:32` 에서 `DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))` (1000 은 `errors.py` 의 예시 메시지). 어느 쪽이든 명시가 필요하다는 결론은 같다 | 근거를 남기고 `recursion_limit` 명시 유지. `GraphRecursionError` 는 정상 종료가 아니므로 `max_steps` 등 정상 가드가 먼저 걸리게 하고, `app.py` 의 기존 예외 처리가 `run.json` 에 failed 를 남긴다 (§8) |
| — | eval5 가 "b687e3b 를 확인하지 못함" | 검토 환경이 fetch 전 | 기준 커밋 표기에 fetch 안내 추가 |

### 0-3. 불필요·중복 코드 검토 (현재 저장소)

| # | 대상 | 상태 | 처리 |
|---|---|---|---|
| D1 | `src/graph.py:206` `_pending()` | 정의만 있고 호출하는 곳 없음 | 그래프 재배선 때 삭제 |
| D2 | 역할 이름 목록 6벌: `graph.py` `FANOUT`·`MERGED`, `report.py` `PERSPECTIVE_NODES`, `reference.py` `ASSESSMENT_NODES`, `review.py` `REVIEW_NODES`, `validate.py` `node_names`, `app.py` `GENERATION_NODES` | 같은 값이 흩어져 있어 Worker 구조로 바꾸면 어긋나기 쉽다 | `src/schema.py` 에 `PERSPECTIVES`·`ASSESSMENT_ROLES` 하나로 두고 모두 import (필수 — 그래프 변경과 함께) |
| D3 | `TECHS = ["TurboQuant", "ITME"]` 4벌 (`research`·`maturity`·`market`·`domain`) | 중복 | `src/schema.py` 로 이동 (D2 와 함께) |
| D4 | 인용 정규식 `\[([0-9a-f]{12})\]` 5벌 (에이전트 4 + `report.py`) | 중복 | `src/agents/common.py: CITATION_RE` |
| D5 | 근거 공백 파서 4벌 (`_extract_gaps_from_text` ×3, `_gaps_from_text`) | 거의 같은 로직 | `common.py: gaps_from_text()` — 이번에 Gap 종류(F1)를 넣어야 해서 어차피 4곳을 고친다 |
| D6 | trace 이벤트 dict 를 에이전트 4곳이 직접 조립, `graph.py: event()` 와 중복 | 중복 | `common.py: event()` 하나로 |
| D7 | 의존성 `rank-bm25` (주석 "EnsembleRetriever(RRF) 의 sparse 축") | 검색이 dense 전용이라 `import` 하는 곳 없음 (`retrieve.py:88` 이 RRF/BM25 를 거부) | **이번 브랜치에서는 유지**(§13 결정 3 — 과제 범위 밖) |
| D8 | `src/rag/query_kw.py` (BM25 용 키워드 변환) | `scripts/ingest.py` 의 점검 assert 에서만 쓰임 | **이번 브랜치에서는 유지**(§13 결정 3) |
| D9 | `scripts/r1_smoke.py` | 옛 그래프 노드 이름에 mock 을 주입하는 스모크 스크립트. 그래프를 바꾸면 깨진다. 통합 테스트가 같은 역할 | **삭제**(§13 결정 3). `app.py` docstring 의 안내 문구도 함께 고친다 |
| D10 | 사람 검토 코드(`review.py` 워크시트·`reviews/verdicts.json`·`scripts/review_reader.py`) | Human Review 를 선택 단계로 유지하므로 **필요** | 유지 |
| D11 | 계획서 자체: `step_count` 와 `recursion_limit`·`MAX_RETRY`·`repair_count` | 겹쳐 보이지만 역할이 다르다(정상 종료 가드 vs 그래프 안전장치). `step_count` 는 과제 State 예시의 종료 가드 | 유지 |

D2~D6 은 Worker 어댑터·Gap 종류 작업으로 어차피 같은 파일을 고치므로 그때 함께 정리한다(별도 리팩터링 단계를 두지 않는다).

---

## 1. 결정 사항

| 항목 | 결정 | 이유 |
|---|---|---|
| 패턴 | **Orchestrator-Workers** | 관점별 조사를 실행 시점의 근거 상태에 따라 나누는 수가 달라진다. 이 분해를 Orchestrator 가 계획하고 그 수만큼 Worker 를 보낸다 |
| trade-off | 얻는 것: 계획이 State 에 남아 무엇을 왜 몇 개로 나눴는지 추적 가능, Worker 병렬 실행, 품질 미달 시 부족한 부분만 재조사. 치르는 것: 계획 단계 비용, 계획의 비결정성(결정적 개수 규칙·LLM 캐시로 완화), 실행 도중 즉시 재배치는 Supervisor 보다 약함(평가 후 재계획으로만 조정) | README 패턴 절 |
| 기존 자산 | research·maturity·market·stakeholder·domain_assessment 에이전트, RAG, `Claim–Evidence–Source–Gap` 계약, `collect_evidence` 병합 로직 유지 | 교수님 3-2·3-6 |
| Worker 실패 정책 | 일시 오류(timeout·네트워크·429·5xx): 그 라운드는 **계속 진행**하고 다음 라운드에 **재시도**. 영구 오류(스키마·코드 오류): 재시도하지 않고 **제외**. 둘 다 `execution_gap` 으로 남아 커버리지 통과 근거가 되지 못한다 → 상한까지 미충족이면 partial 발행 | Notion 의 계속/재시도/제외 |
| Human Review | 선택 단계, 기본 꺼짐 (`--human-review`) — 재현성을 높이기 위한 팀의 선택 | 기존 검토 코드 보존 |
| 모델 | 계획·생성·종합·Judge 모두 `gpt-4.1-mini` | 사용자 지시. Judge 만 바꿀 수 있게 `llm.judge_model` 키 |
| 품질 평가 | Hybrid(코드 검사 + LLM Judge) | Notion 3안 |
| 체크포인트 | `InMemorySaver` | 마감. 프로세스 간 재개 미지원, README 에 한계 명시 |
| goldenset | 보류. README Retrieval 칸 "미측정" | 접근 가능한 사용자 Notion 페이지의 필수 목록에 없음 |

---

## 2. 현행 코드 대비 변경

| 항목 | 현행 | 변경 |
|---|---|---|
| 작업 배분 | `src/graph.py:245-247` 고정 엣지, `FANOUT` | `research → orchestrator → dispatch(Send) → worker` |
| 조사 질의 | 에이전트마다 고정 `QUERIES` (예: `market.py:80`) | Task 의 `focus`·`queries`·대상 기술 |
| Claim ID | 관점·run_id 로만 생성 → 병렬·재계획 시 충돌 | Worker 어댑터가 `"{task_id}:{원래 id}"` 로 재부여 |
| 결과 수집 | 관점별 고정 키 4개 | `worker_results` reducer → `select_active_worker_results` → `collect_evidence` |
| 병렬 노드 예외 | 하나가 예외를 내면 그래프 전체 중단 | `worker` 의 `try/except` 로 실패 결과 반환 |
| 관점 누락 | `collect_evidence` 가 오류로 모아 실행 종료 (`graph.py:141,156`) | `execution_gap` 으로 기록하고 계속. 같은 ID 내용 충돌만 `MergeConflict` |
| Gap | 종류 구분 없음 | `evidence_gap`·`execution_gap`·`invalid_evidence` |
| 보고서 평가 | 생성 후 형식 점검만 | `quality_eval` 4기준 + `route_after_eval` |
| 종합 근거 | 문자열 (`schema.py:137`) | 항목마다 `claim_ids` |
| Human Review | 보고서 전 필수 대기 | 품질 통과 후 선택 단계 + `apply_review` |
| 역할 목록·상수·정규식·파서 | 여러 벌 (§0-3) | 한 곳으로 |
| 실행 상한 | 미지정 | `MAX_RETRY`·`MAX_WORKERS`·`repair_count`·`step_count` + `recursion_limit=60` |
| 한계 문구 | "워크시트에서 사람이 확인" (`report.py:321`) | 자동 검사·Judge 범위·한계 |

---

## 3. 스키마 (`src/schema.py`) — 교수님 1번

```python
TECHS = ["TurboQuant", "ITME"]                                   # D3
PERSPECTIVES = ["maturity", "market", "stakeholder", "domain_assessment"]   # D2
ASSESSMENT_ROLES = ["research"] + PERSPECTIVES

class Task(BaseModel):
    task_id: str                     # f"r{round}-{perspective}-{tech|both}[-{n}]" — 결과 식별자
    perspective: Literal[*PERSPECTIVES]
    technologies: list[Technology]
    focus: str
    queries: list[str]
    rationale: str
    round: int                       # 0 = 최초, 1·2 = 재계획
    web_budget: int = 0              # stakeholder 만. dispatch 전 할당

class WorkerResult(BaseModel):
    task: Task
    round: int                       # = task.round (계획 버전)
    status: Literal["completed", "partial", "failed"]
    assessment: dict | None          # 대상 기술 부분, Claim ID 재부여 후
    error_kind: Literal["", "transient", "permanent"] = ""
    error: str = ""
    attempted_queries: list[str]     # 코드가 실제 실행한 질의
    retrieved: int                   # 코드가 센 검색 결과 수 (임계값 이상)
    web_searches_used: int = 0

class Gap(BaseModel):                # 기존 + kind
    ...
    kind: Literal["evidence_gap", "execution_gap", "invalid_evidence"] = "evidence_gap"
```

- `plan` = **현재 라운드 Task 목록**. 전체 작업 이력은 `worker_results`(각 결과가 자기 Task 를 가짐)와 `decisions.jsonl`.
- `evidence_gap` 의 `attempted_queries`·`retrieved` 는 LLM 이 아니라 Worker 어댑터가 채운다 — "조사를 실제로 했는가"의 근거다.

---

## 4. 그래프 (`src/graph.py`) — 교수님 3번

```
START → setup → research → orchestrator ─(dispatch: Send × len(plan))→ worker ─→ collect_evidence
collect_evidence → synthesis → report → quality_eval ─(route_after_eval)→ orchestrator | publish → END
                                                         (+ 수리: synthesis | report — §13 결정 2)
(--human-review 일 때) quality_eval ─(통과)→ human_review ⏸ → apply_review → synthesis | publish
```

```python
from langgraph.types import Send

WORKERS = {"maturity": maturity, "market": market,
           "stakeholder": stakeholder, "domain_assessment": domain_assessment}
MAX_RETRY, MAX_WORKERS = 2, 8          # settings.yaml: orchestrator

def dispatch(state):
    return [Send("worker", state | {"task": t}) for t in state["plan"]]

def worker(state):
    task = state["task"]
    try:
        out = WORKERS[task["perspective"]](state)[task["perspective"]]
        out = adapt(out, task)        # 대상 기술만, Claim ID 재부여, 조사 이력 기록
        status, kind, error = out.get("status", "completed"), "", ""
    except Exception as exc:          # Fallback: 그래프를 죽이지 않는다
        out, status = None, "failed"
        kind, error = classify(exc), f"{type(exc).__name__}: {exc}"
    return {"worker_results": [result(task, status, out, kind, error)],
            "trace": [event("worker", status, task_id=task["task_id"])]}

def route_after_eval(state):
    if state["quality_eval"]["passed"]:
        return "human_review" if state["run_config"].get("human_review") else "publish"
    if state.get("retry_count", 0) >= MAX_RETRY:
        return "publish"                       # partial 로 표시하고 종료 (종료 보장)
    return state["quality_eval"]["next"]    # quality_eval 이 §7-2 규칙으로 정해 기록한 다음 노드 ("orchestrator" | "synthesis" | "report" | "publish")

b.add_edge("setup", "research")
b.add_edge("research", "orchestrator")
b.add_conditional_edges("orchestrator", dispatch, ["worker"])
b.add_edge("worker", "collect_evidence")      # Send 로 띄운 worker 가 전부 끝나야 실행된다
b.add_edge("collect_evidence", "synthesis")
b.add_edge("synthesis", "report")
b.add_edge("report", "quality_eval")
b.add_conditional_edges("quality_eval", route_after_eval,
                        ["orchestrator", "synthesis", "report", "human_review", "publish"])
b.add_edge("publish", END)
```

**Worker 어댑터 책임** (`src/agents/worker.py`)
- `adapt()`: 대상 기술의 Claim·Gap·참조 Evidence·Source 만 남김 · Claim ID 를 `"{task_id}:{원래 id}"` 로 재부여(Evidence ID 는 chunk_id 기반이라 같은 청크면 같은 내용 — 기존 병합 로직이 처리) · `attempted_queries`·`retrieved` 기록 · Claim 이 없으면 `evidence_gap`(조사 이력 포함) · 결과당 Claim·Evidence 발췌 길이 상한 적용 · 결과 원본을 `runs/<run_id>/workers/<task_id>.json` 에 원자적 저장(`save_json`).
- `classify()`: `TimeoutError`·연결 오류·HTTP 429/5xx·OpenAI `APITimeoutError`/`RateLimitError` → `transient`, 그 밖(검증·파싱·코드 오류) → `permanent`.
- 외부 호출 timeout: 웹은 기존 `timeout: 15`, LLM 은 `init_chat_model(..., timeout=60, max_retries=1)` 로 명시한다 — SDK 내부 재시도 1회 + 그래프 재시도(라운드)로 실제 호출 횟수를 설명할 수 있게 한다.
- 결과 파일 저장 자체가 실패하면 결과를 만들 수 없으므로 잡지 않고 실행 오류로 끝낸다(`app.py` 가 `run.json` 에 failed 기록). 모든 오류를 조사 Gap 으로 바꾸지 않는다.
- 에이전트 확장: `task` 가 있으면 기본 `QUERIES` 대신 `task["queries"]`, 지시문에 `focus`·대상 기술(`common.py: task_for(state)`). 프롬프트가 달라 LLM 캐시가 같은 응답을 재생하지 않는다.
- stakeholder: `WebStore(run_id, subdir=task_id)` 로 Task 별 폴더를 쓰고, `stakeholder.json`·`-validation.json`·`-trace.jsonl` 도 그 폴더에 쓴다(지금은 실행 폴더 고정 경로, `stakeholder.py:216-218`). 검색 횟수는 `task.web_budget` 을 넘지 않는다.

research 는 계획의 입력이라 Worker 가 아니다. research 가 예외를 내면 계획할 근거가 없으므로 현행처럼 실행 실패로 기록하고 끝낸다.

### 4-1. Orchestrator (`src/orchestrator/planner.py`)

**최초 계획 (round 0)** — 검증 범위: 4관점 × 2기술 전체 요구
1. 입력: research Assessment, 입력 도메인, 관점별 평가 기준.
2. **근거 가용성 사전 조사**(결정적): maturity·market·domain_assessment 에 대해 관점 허용 컬렉션에서 "기술 + 평가 기준 + 도메인" 질의로 검색, **유사도 ≥ τ** 인 청크만 남기고 `chunk_id` 로 중복 제거한 뒤 청크 수와 출처 묶음(`source_groups`) 수를 기술별로 센다.
3. **Task 개수 규칙**(결정적):

   | 관점 | 조건 | Task |
   |---|---|---|
   | maturity·market·domain_assessment | 두 기술 모두 청크 ≥ T | 기술별 2개 |
   | 〃 | 그 외, 두 기술 모두 청크 ≥ 1 | 1개 (두 기술 함께) |
   | 〃 | 한 기술의 청크가 0 | 다른 기술 1개 + 계획에 `evidence_gap(reason="사전 조사 근거 없음", attempted_queries=사전 조사 질의)` |
   | stakeholder | 항상 | 1개 (두 기술·4주체) |

   → 4~7개(`MAX_WORKERS` 이하). τ·T 는 0단계에서 보정·고정, README 공개.
4. **LLM 분해**: 정해진 Task 마다 `focus`·`queries`·`rationale`. 개수는 LLM 이 바꾸지 못한다.
5. 검증 실패 → 같은 개수의 결정적 기본 계획(평가 기준별 고정 `focus` 템플릿·기본 질의).

**재계획 (round = retry_count, 1·2)** — 검증 범위: **미충족 칸만**
- 대상: ① `transient` 로 실패한 칸(`permanent` 는 제외 확정) ② 품질 평가가 근거 문제로 지목한 칸. 같은 칸을 다른 유효 결과가 이미 충족하면 대상에서 뺀다 — 판정 단위는 실패 수가 아니라 미충족 칸.
- 우선순위(`execution_gap` → L1 무효 → 커버리지 → 편향) 순으로 최대 `MAX_WORKERS` 개. 넘치는 대상은 미충족으로 남기고 `decisions.jsonl` 에 기록한다(조용히 누락하지 않는다).
- **빈 재계획**(보낼 대상 없음 — 예: 남은 문제가 모두 `permanent` 제외): Worker 를 보내지 않고 `publish`(partial).
- 재계획 LLM 실패 → 대상 칸의 결정적 기본 Task.
- **웹 예산**: `max_searches`(16)는 **실행 전체** 상한. dispatch 전에 `남은 예산 = 16 − Σ worker_results.web_searches_used` 를 계산해 그 라운드 stakeholder Task 에 균등 분배(나머지는 앞 Task 부터 1씩). 할당이 0인 stakeholder Task 는 보내지 않고 `execution_gap(reason="웹 검색 예산 소진")`.
- `retry_count += 1`, `plan` = 이번 라운드 Task 목록.

---

## 5. Fan-in — `collect_evidence` (교수님 3-6)

기존 병합 로직(`_merge_one`, 같은 ID 내용 충돌 시 `MergeConflict`)은 그대로 쓰고, 입력부만 바꾼다.

1. **`select_active_worker_results(worker_results, claim_flags)`** (`src/orchestrator/selection.py`)
   - 같은 `task_id` 결과가 여러 번 오면 1회만 반영, 내용이 다르면 정합성 오류(실행 실패로 종료).
   - **칸 단위 교체**(§13 결정 1 확정): (관점, 기술) 칸마다 그 칸을 대상으로 한 **가장 최근 `round` 의 결과만** 쓴다. 재계획한 칸은 새 결과로 통째로 바뀌고, 재계획하지 않은 칸은 이전 라운드 결과를 그대로 쓴다.
   - `claim_flags` 가 `invalid` 인 Claim 은 어느 결과에서든 제외한다. 칸 교체 전(재계획 예산이 없어 partial 로 발행할 때)이나 Human Review 부결에도 무효 Claim 이 보고서에 남지 않게 하기 위해서다.
   - **유효 Claim 소실 완화**: 재계획 Task 의 `rationale` 에 그 칸의 기존 유효 Claim 과 인용 청크 id 를 "다시 확인할 기존 근거"로 넣어, 새 Worker 가 같은 근거를 다시 다루게 한다. 그래도 새 결과에 나오지 않은 기존 Claim 은 사라진다 — README 한계에 적는다.
   - 두 기술을 함께 다룬 Task 의 `technology="both"` Claim: 한 기술 칸이 교체되면 사라진 근거를 인용한 `both` Claim 은 버리고, 나머지는 `claim_flags=recheck` 로 표시해 Judge 표본에 넣는다.
   - 칸에 유효 Claim 이 없으면: 그 칸 최신 결과가 `failed` → `execution_gap`, 조사했으나 근거 없음 → `evidence_gap`(조사 이력 포함), 전부 무효 → `invalid_evidence`.
   - 종합 입력은 `task_id` 로 정렬한다(병렬 결과 도착 순서에 의존하지 않는다).
2. 관점별 Assessment 로 조립해 기존 `MERGED` 루프(→ `ASSESSMENT_ROLES`)에 넣는다.
3. 관점 Assessment 가 없을 때 오류로 모으지 않고 Gap 으로 기록. 같은 ID 내용 충돌만 `MergeConflict`.
4. `last_error` 에 이번 라운드 실패 Task 요약.

---

## 6. State (`src/state.py`) — 교수님 2번

```python
class ReportState(TypedDict, total=False):
    # ── 상관 키 ──
    run_id: str
    run_config: dict

    # ── 제어 ──
    plan: list[dict]                              # 현재 라운드 Task 목록
    retry_count: int                              # 재계획 라운드 수. 상한 MAX_RETRY
    repair_count: int                             # synthesis·report 수리 횟수. 상한 2
    step_count: int                               # orchestrator·route_after_eval 결정마다 +1
    claim_flags: dict[str, dict]                  # {claim_id: {status: invalid|recheck, reason, by: quality_eval|human_review, at_report_version}}
    last_decision: dict
    last_error: str | None
    stop_reason: str
    run_status: str                               # running / completed / partial / failed

    # ── 페이로드 ──
    research: dict                                # 선행 단계 Assessment (research 노드)
    maturity: dict                                # ┐ collect_evidence 가 worker_results 에서 조립한 관점별 Assessment.
    market: dict                                  # │ synthesis·report·reference·validate·review 가 지금처럼
    stakeholder: dict                             # │ state[역할] 로 읽는다 (src/agents/report.py:68,
    domain_assessment: dict                       # ┘ reference.py:65, validate.py:198 등) — 출력 계층 변경 최소화
    worker_results: Annotated[list[dict], operator.add]
    source_registry: dict
    evidence_registry: dict
    gaps: list[dict]
    synthesis: dict                               # + synthesis_version, round
    report: str
    report_manifest: dict                         # + based_on_synthesis_version
    report_version: int
    quality_eval: dict                            # passed, verdicts, evaluated_report_version, pdf_path, pdf_sha256, scope
    report_paths: list[str]

    # ── 누적 로그 ──
    trace: Annotated[list[dict], operator.add]    # 가벼운 이벤트만 (현행 유지)
```

### 6-1. 쓰기 담당

| 필드 | 쓰는 노드 | 채널 |
|---|---|---|
| `worker_results`·`trace` | worker (병렬) / `trace` 는 모든 노드 | `operator.add` |
| `plan`·`retry_count`·`last_decision` | orchestrator | 기본 |
| `last_error`·registry·`gaps`·관점별 Assessment 4개(`maturity`·`market`·`stakeholder`·`domain_assessment`) | collect_evidence | 기본 |
| `step_count`·`repair_count`·`stop_reason` | orchestrator·quality_eval (결정한 노드) | 기본 |
| `claim_flags` | quality_eval(L1 무효·recheck) · apply_review(사람 부결) | 기본 — 같은 단계에 함께 실행되지 않음 |
| `synthesis` | synthesis (`synthesis_version += 1`) | 기본 |
| `report`·`report_manifest`·`report_version` | report | 기본 |
| `quality_eval` | quality_eval | 기본 |
| `run_status`·`report_paths` | publish | 기본 |

### 6-2. State 설계 7항목 (README 에 그대로 옮긴다)

| 항목 | 설계 |
|---|---|
| 제어 vs 페이로드 분리 | 제어: `plan`, `retry_count`, `repair_count`, `step_count`, `claim_flags`, `last_decision`, `last_error`, `stop_reason`, `run_status` / 페이로드: `research`, 관점별 Assessment 4개, `worker_results`, registry, gaps, synthesis, report, `report_manifest`, `quality_eval` |
| 관측성 위치 | 계획 전문·재시도 대상 선택·품질 판정 사유는 `decisions.jsonl` + LangSmith. State 에는 `last_decision` 요약만 |
| 지속성 비용 | 누적 필드는 `worker_results`·`trace`. `worker_results` 는 라운드당 8 × 3라운드 = 최대 24건이고 결과당 Claim·Evidence 발췌 길이에 상한을 둔다. `trace` 는 가벼운 이벤트만. 원본·웹 스냅샷·이전 보고서는 `runs/<run_id>/` 파일. 0단계에 실제 State 크기 측정 |
| 상관 | `run_id` = thread_id = LangSmith `metadata.run_id`. 결정·Worker 마다 `task_id`·`round`·`report_version` |
| 재개/복구 | `InMemorySaver`(프로세스 내). 상태: `plan`·`run_status` / 에러: `last_error`·`worker_results[].error_kind`·`stop_reason` / 재시도: `retry_count`·`repair_count`·`step_count`. 결과 식별자 `task_id`, 중복 결과는 1회만 반영. 프로세스 간 재개는 미지원(한계 명시) |
| 동시 처리 | `Send` Worker 는 `worker_results`·`trace` 에만 쓰고 `operator.add` 로 병합. 결과 순서에 의존하지 않도록 `task_id` 로 정렬. Claim ID 는 `task_id` 접두로 전역 유일. State 밖 공유 자원(웹 수집 파일)은 Task 별 폴더 |
| 종료 보장 | `MAX_RETRY=2`, `MAX_WORKERS=8`, `repair_count ≤ 2`, `max_steps`, `recursion_limit=60`. 상한은 추가 실행 **전에** 검사하고, 닿으면 partial 로 publish |

---

## 7. 품질 평가와 Loop (`src/orchestrator/evaluator.py`) — 교수님 3-5

### 7-1. 평가 기준

평가 대상은 **현재 보고서**. `report` 노드는 하나의 보고서 모델 객체에서 Markdown 과 `report_manifest`(절별 표시 Claim id, 표시 Gap 과 종류, 인용 evidence id, 참고문헌, 공개한 예외, `based_on_synthesis_version`)를 함께 만든다.

| 기준 | 코드 검사 (먼저) | Judge 입력 | Judge 판정 |
|---|---|---|---|
| Groundedness L1 | 표시된 Claim 이 claim → evidence → source 로 닫힘 | Claim + 인용 구절 + 출처·실험 조건 | 근거가 주장을 뒷받침하는가 |
| Groundedness L2 | SUMMARY·종합·시사점 문장의 `claim_ids` 가 표시된 유효 Claim 을 가리킴 | 종합 문장 + 참조 Claim 원문 | 참조 범위를 벗어난 서술인가 |
| 중립성 | 승자·추천·우열 표현 패턴 | SUMMARY + 시사점 + 관점별 비교 문장 | 우열 판정, 조건 다른 수치의 단순 비교 |
| 편향 통제 | ① 인용 근거가 2개 이상 출처 묶음 ② 단일 묶음 비중 ≤ 60% (기술별) | 기술별 인용 출처 + 보고서에 쓰지 않은 같은 기술의 한계·비용 Claim | ③ 선택적 근거 사용 |
| 관점 커버리지 | `report_manifest` 에서 4관점 × 2기술마다 **유효 Claim 또는 수용 가능한 `evidence_gap`**. `execution_gap`·`invalid_evidence` 는 미충족 | 관점별 본문 + 관점 평가 기준 | 관점을 실질적으로 다루는가 |
| 구조·분량 | SUMMARY 맨 앞, REFERENCE 맨 뒤, 인용–참고문헌 일치, 렌더링 PDF ≤ 10쪽 | — | — |

- **수용 가능한 `evidence_gap`**: Worker 어댑터가 기록한 `attempted_queries` 가 1개 이상이고 그 질의의 임계값 이상 검색 결과가 0(또는 결과에서 유효 Claim 을 만들지 못함)이며, 보고서 §6 에 조사 범위와 한계가 표시된 경우. `focus` 를 복사해 채운 Gap 은 이 기준을 만족하지 못한다.
- 코드 검사 통과 기준만 Judge 호출. 표본: 60건 이하 전수, 넘으면 관점 × 기술 층화 결정적 표본 60건, `recheck` Claim 은 반드시 포함. 검사 범위 n/N 을 판정 파일과 보고서 §6 에 적는다.
- L1 에서 탈락한 Claim 은 `claim_flags[claim_id] = {status: "invalid", reason, by: "quality_eval", at_report_version}`.
- 편향 ①② 실패라도 사전 정의 예외(해당 기술에 다른 출처 묶음 자료가 없음, 예: ITME direct 원문은 `itme-paper` 1편)를 충족하면 §6 에 예외 사유·조사 범위를 기재하는 것으로 처리, 이미 기재돼 있으면 다시 보내지 않는다. ③ 은 예외와 무관하게 매번 검사. `source_groups`·임계값·예외 기준은 0단계 확정·README 공개.
- Judge: `llm.judge_model`(기본 `gpt-4.1-mini`), temperature 0, 고정 프롬프트, LLM 캐시, 호출 실패 시 1회 재시도. 생성=Judge 동일 모델의 자기평가 편향은 README 한계.

### 7-2. `route_after_eval`

상한은 추가 실행 **전에** 검사한다. 마지막으로 허용된 평가가 통과면 통과로 publish 한다.

| 순서 | 조건 | 다음 |
|---|---|---|
| 1 | `passed` | `human_review`(켜진 경우) 또는 `publish` |
| 2 | `retry_count >= MAX_RETRY` | `publish` — partial |
| 3 | 근거 문제: L1 실패, 커버리지 미달(`execution_gap`·`invalid_evidence`·근거 부족), 편향 ①② 예외 미충족, ③ 인데 한계 근거 미수집 | `orchestrator` (재계획 — 미충족 칸만). 빈 재계획이면 `publish` partial |
| 4 | 종합 문제: L2·중립성, ③ 인데 한계 근거 수집됨 (`repair_count < 2`) | `synthesis` |
| 5 | 보고서 문제: 보고서에서만 빠진 커버리지, 구조, 예외 미기재, 10쪽 초과 (`repair_count < 2`) | `report` (재조립·압축) |
| 6 | `repair_count` 소진, Judge 실패 | `publish` — partial |

**partial 발행 규칙**: `run_status=partial`, `stop_reason` 기록, 보고서 첫머리·§6·README 실행 표에 미달 기준과 사유. 상한 도달·예산 소진은 통과로 표시하지 않는다. **10쪽 초과 상태로는 발행하지 않는다** — partial 발행 직전에도 렌더링 쪽수를 확인하고, 넘으면 최소 표시 수준(4관점 × 2기술 칸마다 1개 + SUMMARY + REFERENCE)으로 압축해 발행한다. 0단계에서 최소 표시 수준 렌더링이 10쪽 이하임을 확인해 두므로 이 압축은 항상 성립한다.

**publish guard** (`src/orchestrator/versions.py`, `route_after_eval` 과 `publish` 직전 공통):
- `report_manifest.based_on_synthesis_version == synthesis.synthesis_version`
- `quality_eval.evaluated_report_version == report_version`, PDF 경로·sha256 일치
- 불일치 = 구버전 → 해당 단계부터 다시 생성. 같은 버전 파일 손상 = 실행 오류.

압축 규칙: 칸마다 최소 1개(표시 Claim 또는 수용 Gap), 한계·비용 Claim 이 있는 기술은 1건 유지, SUMMARY·REFERENCE·예외 문구는 압축하지 않는다. 기본 표시 상한은 0단계 실측으로 9쪽 이하가 되게 정한다(한 예시 기준이며, 실제 보장은 최종 렌더링 검사).

---

## 8. 상한 (`settings.yaml: orchestrator:`)

| 상한 | 값 | 단위 |
|---|---|---|
| `MAX_RETRY` | 2 | 재계획 라운드 |
| `MAX_WORKERS` | 8 | 라운드당 Task·Worker (최초 4~7) → 실행 전체 최대 24 |
| `max_repairs` | 2 | synthesis·report 수리 |
| `max_searches` | 16 | **실행 전체** 웹 검색 (라운드마다 새로 주지 않음) |
| `max_judge_retries` | 1 | |
| LLM 호출 | `timeout=60`, SDK `max_retries=1` | 한 호출이 끝나지 않는 경우 대비 |
| `max_steps` | 12 | `step_count` — 정상 종료 가드 |
| `recursion_limit` | 60 | 그래프 안전장치. 최대 경로 ≈ 30 superstep. 설치본 langgraph 1.2.12 의 기본값은 10007(`_internal/_config.py:32`) 이라 명시 필수. 초과 시 `GraphRecursionError` → `app.py` 가 `run.json` 에 failed 기록 (정상 가드가 먼저 걸리도록 설계) |

---

## 9. Human Review (선택)

- 기본 꺼짐. `--human-review` 로 켜면 품질 통과 후 `human_review` 에서 멈춘다(`interrupt_after`). 사람은 기존 워크시트(`review.csv` → `reviews/verdicts.json`)에 판정을 적는다.
- 재개하면 **`apply_review`** 가 판정을 읽어 부결 Claim 을 `claim_flags[claim_id] = {status: "invalid", by: "human_review"}` 로 기록한다. 부결이 있으면 `synthesis` 로(그 Claim 은 선택 단계에서 빠진다), 없으면 `publish`.
- 제출용 기본 실행과 트레이스는 꺼진 상태로 만든다(재현성을 높이기 위한 팀의 선택). 켠 실행을 제출하면 보고서 §6 에 적는다.

---

## 10. 테스트 (CI, mock, API 키 없이)

`pytest.mark.parametrize` 로 묶는다. 핵심을 먼저 통과시키고 E2E 실행 후 나머지.

**핵심 12개**
1. 계획 3개 → Worker 정확히 3회 → `worker_results` 3건 → `collect_evidence` 정확히 1회 → `synthesis` 1회
2. 사전 조사 결과에 따라 Task 수가 규칙대로 달라지고(4~7) `Send` 수 = `len(plan)`. LLM 이 다른 개수를 내도 규칙 개수, 폴백도 같은 개수
3. LLM 계획 실패 → 결정적 기본 계획
4. **병렬 Worker 2개가 실제로 예외를 던짐**(실패 객체 주입이 아님) → 그래프가 죽지 않고 두 결과 모두 보존, 나머지로 진행
5. `transient` 실패 칸 → 다음 라운드 재시도 → 성공 반영 / `permanent` → 재시도 없음
6. **코드 기반 모드에서 `execution_gap` 만 있는 필수 칸은 통과로 publish 되지 않는다**
7. 같은 칸을 다른 유효 결과가 충족하면 실패 Task 를 재실행하지 않는다
8. **market × ITME 의 비용 Claim 만 L1 무효 → 재계획 1개(rationale 에 기존 유효 Claim 포함) → market × ITME 칸은 새 결과로만 구성, 무효 Claim 재등장 없음, 나머지 7칸은 이전 결과 그대로, 두 번 재계획해도 동일** · 재계획 예산이 없어 partial 발행할 때도 `invalid` Claim 은 보고서에 없음
9. 같은 관점 Worker 2개 병렬 → Claim ID 충돌 없음, `MergeConflict` 없음
10. L2·중립성 실패 → `synthesis` → 재평가 / 같은 라운드에서 synthesis 만 바뀐 상태에 옛 report·quality 를 붙이면 publish guard 가 막음
11. `retry_count >= MAX_RETRY` → partial publish, 보고서에 미달 기준, 10쪽 이하
12. `max_steps`·`recursion_limit` 안에서 종료

**나머지**: 결과 도착 순서 바꿔도 같은 종합 입력 · 같은 `task_id` 결과 중복 → 1회 반영 · 웹 예산 소진 후 stakeholder 재시도 요청 → 보내지 않고 `execution_gap` · 라운드 대상이 8개 초과 → 우선순위대로 8개, 나머지 기록 · 빈 재계획 → partial publish · 10쪽 초과 → 압축 → 재검사 · 편향 예외 미기재 → `report` 1회, 기재 후 반복 없음 · 예외가 있어도 한계 누락이면 ③ 실패 · 보고서에서 한 관점 누락 → 실패 · stakeholder Task 2개 병렬 → 각 폴더 기록 보존 · Human Review 부결 Claim 이 재종합에서 되살아나지 않음 · 기본 상한에서 렌더링 ≤ 10쪽, 최소 표시 수준 ≤ 10쪽(PDF extra 환경만)

---

## 11. 3인 분업 · 브랜치 계획

### 11-1. 원칙

1. **파일 단위 소유.** 모든 변경 파일에 담당자가 한 명이다. 남의 파일은 고치지 않는다. 필요하면 담당자에게 요청한다.
2. **계약 먼저.** 세 사람이 함께 쓰는 정의(스키마·State·설정 키·함수 시그니처)는 분기 전에 0단계 "계약 커밋"으로 통합 브랜치에 먼저 넣고, 이후에는 동결한다. 계약 변경은 담당 A 가 통합 브랜치에 작은 PR 로 올리고 나머지는 그 커밋을 merge 해 받는다.
3. **각자 혼자 테스트할 수 있게.** 레인마다 다른 레인의 결과를 기다리지 않도록 0단계에서 계약을 따르는 픽스처와 스텁을 둔다.
4. **브랜치마다 `uv run pytest -q` 전체 통과.** 자기 레인 변경으로 깨지는 기존 테스트는 그 테스트의 담당자가 고친다(§11-5).

### 11-2. 브랜치 구조

```
main (b687e3b)
 └─ agent/orchestrator-workers            ← 통합·제출 브랜치. 0단계 계약 커밋 후 분기
     ├─ agent/ow-orchestration            ← 담당 A: 조정 계층 (계획·dispatch·worker 어댑터·그래프·app)
     ├─ agent/ow-workers                  ← 담당 B: 하위 에이전트 (5개 에이전트·공통 함수·웹 수집)
     └─ agent/ow-quality                  ← 담당 C: 출력 계층 (종합·보고서·품질 평가·발행·사람 검토)
```

- 세 작업 브랜치는 모두 0단계 계약 커밋에서 분기하고, **PR 로 `agent/orchestrator-workers` 에 merge** 한다(main 에는 merge 하지 않는다 — 과제 요건 "Branch 로 기존 작업과 구분").
- 작업 중 통합 브랜치가 바뀌면 각자 `git merge agent/orchestrator-workers` 로 받는다(rebase 대신 merge — 공유 이력 보존).
- 제출 대상은 `agent/orchestrator-workers` 다.

### 11-3. 0단계 — 계약 커밋 (담당 A, 분기 전, 통합 브랜치에 직접)

| 파일 | 내용 |
|---|---|
| `src/schema.py` | `TECHS`·`PERSPECTIVES`·`ASSESSMENT_ROLES` (D2·D3), `Task`, `WorkerResult`, `Gap.kind`, `Synthesis.agreements/conflicts` 의 `claim_ids`(§7), `ReportManifest`(절별 표시 Claim id·표시 Gap 과 종류·인용 evidence id·참고문헌·공개 예외·`based_on_synthesis_version`), `QualityEval`(`passed`·`verdicts`·`next`·`evaluated_report_version`·`pdf_path`·`pdf_sha256`·`scope`) |
| `src/state.py` | §6 의 필드 전체 (관점별 Assessment 4개 포함) |
| `config/settings.yaml` | 모델 `gpt-4.1-mini`·`judge_model`·LLM `timeout`·`max_retries`, `orchestrator:`(`MAX_RETRY`·`MAX_WORKERS`·`max_repairs`·`max_steps`·`recursion_limit`·`max_searches`·`split.tau`·`split.T` 자리값), `report:`(표시 상한 자리값), `quality:`(`source_groups`·편향 임계값·예외 기준 자리값). **섹션마다 담당 주석**을 단다 |
| `src/llm.py` | 모델 docstring, `timeout`·`max_retries` 전달, `judge_llm()` |
| `src/agents/common.py` | `CITATION_RE`·`event()`·`gaps_from_text()` 를 **추가만** 한다(기존 에이전트는 아직 자기 사본을 쓴다. 바꾸는 것은 각 레인) |
| `src/observability.py` (신규) | `log_decision(state, node, decision, reason, **fields)` → `runs/<run_id>/decisions.jsonl` 에 append. A·C 가 함께 쓴다 |
| 계약 스텁 | `src/orchestrator/__init__.py`, `src/orchestrator/evaluator.py`(`quality_eval`·`route_after_eval`·`apply_review` 시그니처만, `NotImplementedError`), `src/orchestrator/versions.py`(`check_publish_guard` 시그니처만), `src/agents/worker.py`(시그니처만) |
| `tests/fixtures/contract/` | 계약을 따르는 예시 JSON — `task.json`, `worker_result_ok.json`·`_failed.json`, `state_after_collect.json`(관점별 Assessment·registry·gaps), `state_after_report.json`(report·manifest). 이후 **읽기 전용**, 더 필요하면 각 레인이 자기 폴더에 추가 |
| `.env.example`·`README.md` 모델 문구·`.github/workflows/test.yml`(`agent/**` push 트리거) | 0단계 공통 정리 |

0단계 완료 조건: `uv run pytest -q` 전체 통과(스텁은 아직 그래프에 연결하지 않음) → 통합 브랜치 push → 세 작업 브랜치 생성.

**계약 커밋에서 실제로 정한 세부** (구현 시 참고)
- `Synthesis` 는 그대로 두고, 목표 형식을 `GroundedSynthesis`(`Agreement`·`GroundedConflict`, `synthesis_version`, `round`)로 따로 정의했다. 담당 C 가 `synthesis.py`·`report.py` 를 옮길 때 `Synthesis` 를 이것으로 바꾼다 — 계약 커밋에서 바꾸면 기존 동작·테스트가 깨지기 때문이다.
- `Gap` 의 새 필드(`kind`·`attempted_queries`·`missing_info`·`impact`)는 모두 기본값이 있어 기존 에이전트는 그대로 통과한다. 그 영향으로 `tests/test_r1_schema.py` 의 R3 round-trip 기대값에 기본값을 채우도록 고쳤다.
- `route_after_eval` 은 계약 그대로 `state["quality_eval"]["next"]` 를 읽는 한 줄로 구현했다. `quality_eval`·`apply_review`·`check_publish_guard`·`worker` 는 `NotImplementedError` 스텁이다.
- 에이전트 반환의 `worker_meta` 형식은 `schema.WorkerMeta`, Worker 결과는 `schema.WorkerResult`(실패 결과는 `assessment` 없음·`error_kind` 필수, `round == task.round`)로 검증한다.
- 계약 검증 테스트: `tests/test_contract.py` (담당 A).

### 11-4. 레인별 담당 파일과 작업

#### 담당 A — 조정 계층 (`agent/ow-orchestration`)

| 파일 | 작업 |
|---|---|
| `src/orchestrator/planner.py` (신규) | 사전 조사(τ 이상 청크·`chunk_id` 중복 제거·출처 묶음 수), 개수 규칙, LLM 분해, 기본 계획 폴백, 재계획(대상 선택·우선순위·빈 재계획·웹 예산 분배) — §4-1 |
| `src/orchestrator/dispatch.py` (신규) | `dispatch()` — §4 |
| `src/orchestrator/selection.py` (신규) | `select_active_worker_results()` — 칸 단위 교체, `claim_flags` 제외, 중복·정렬, Gap 종류 — §5 |
| `src/agents/worker.py` | `worker` 노드, `WORKERS`, try/except, `classify()`, `adapt()`(대상 기술 필터·Claim ID 재부여·조사 이력·결과 파일 저장) — §4 |
| `src/graph.py` | 재배선, `collect_evidence` 입력부(관점별 Assessment 4개 조립·누락은 Gap), `_pending` 삭제(D1), `MERGED`→`ASSESSMENT_ROLES`, `event` → `common.event`, `human_review` 노드와 `interrupt_after` 연결 |
| `app.py` | `--human-review`, `invoke` config(`run_name`·`tags`·`metadata.run_id`·`recursion_limit`), `GENERATION_NODES`→`ASSESSMENT_ROLES`, `r1_smoke` 안내 문구 삭제 |
| `scripts/r1_smoke.py` | 삭제 (D9) |
| `config/settings.yaml` 의 `orchestrator:` 섹션 | τ·T 보정값 기입(도메인 2개로 Task 수 차이 확인) |
| 테스트 | `tests/test_r1_graph.py`·`tests/test_integration_graph.py`·`tests/mock_nodes.py`·`tests/conftest.py` 갱신, 신규 `tests/test_orchestrator_*.py` — §10 핵심 1·2·3·4·5·6·7·9·12 |
| 통합·제출 | §11-6 통합 PR, E2E 실행·도메인 비교 실행, LangSmith 트레이스 PNG, `docs/evidence/agent/`, `README.md` 조립 |

#### 담당 B — 하위 에이전트 (`agent/ow-workers`)

| 파일 | 작업 |
|---|---|
| `src/agents/research.py`·`maturity.py`·`market.py`·`domain.py` | `task` 가 있으면 `task["queries"]`·`focus`·대상 기술 사용(`common.task_for`), 자기 사본 `TECHS`·인용 정규식·Gap 파서·trace 조립을 `schema`/`common` 것으로 교체(D3~D6), Gap 에 `kind` |
| `src/agents/stakeholder.py` | 위와 같음 + `WebStore(run_id, subdir=task_id)`, `stakeholder.json`·`-validation.json`·`-trace.jsonl` 을 Task 폴더에, 검색 수 ≤ `task["web_budget"]` |
| `src/agents/common.py` | `task_for(state)` 추가, 0단계에서 넣은 공통 함수 유지보수 |
| `src/tools/web_store.py` | `subdir` 인자 |
| **에이전트 반환 계약** | `{역할: Assessment, "trace": [...], "worker_meta": {"attempted_queries": [...], "retrieved": n, "web_searches_used": n}}` — A 의 `adapt()` 가 `worker_meta` 로 조사 이력·`evidence_gap` 을 만든다 |
| 테스트 | `tests/test_r3_*.py`·`tests/test_r4_*.py` 갱신, 신규 `tests/test_workers_task.py`(Task 입력 시 질의·대상 기술·`worker_meta`·웹 예산·Task 폴더) |

#### 담당 C — 출력 계층 (`agent/ow-quality`)

| 파일 | 작업 |
|---|---|
| `src/agents/synthesis.py` | `claim_ids` 구조, `claim_flags` 의 `invalid` Claim 제외, 품질 평가 피드백 반영, `synthesis_version += 1`, `PERSPECTIVES` 사용 |
| `src/agents/report.py` | 보고서 모델 객체 → Markdown + `report_manifest` 동시 생성, 표시 상한·압축 단계·최소 표시 수준, Gap 종류 표시, partial 표시, 한계 문구 교체(`report.py:321`), `CITATION_RE`·역할 목록을 `common`/`schema` 것으로 |
| `src/orchestrator/evaluator.py` | `quality_eval`(코드 검사 → Judge, 층화 표본, `claim_flags`, 편향 예외, `next` 결정과 `repair_count`·`stop_reason`), `route_after_eval`(= `quality_eval["next"]` 읽기), `apply_review` — §7, §9 |
| `src/orchestrator/versions.py` | `check_publish_guard()` — §7-2 |
| `src/output/pdf.py` | 렌더링 쪽수 측정, publish guard 적용, partial 발행, 10쪽 초과 시 최소 수준 압축 후 발행 |
| `src/output/reference.py`·`validate.py`·`review.py` | 역할 목록을 `ASSESSMENT_ROLES` 로(D2), `review.py` 는 `apply_review` 가 읽을 판정 로더 |
| `config/settings.yaml` 의 `report:`·`quality:` 섹션 | 쪽수 실측으로 표시 상한(9쪽 이하)·최소 수준 ≤ 10쪽 확인, `source_groups`(논문 4편 1저자 소속 포함)·편향 임계값·예외 기준 기입 |
| 테스트 | `tests/test_r5_*.py` 갱신, 신규 `tests/test_quality_*.py` — §10 핵심 8·10·11 과 품질 나머지 |

#### 공유 파일 규칙

| 파일 | 규칙 |
|---|---|
| `src/schema.py`·`src/state.py` | 0단계 후 동결. 변경은 A 가 통합 브랜치에 계약 PR → 각자 merge |
| `config/settings.yaml` | 섹션 소유: `llm:`·`orchestrator:` = A, `report:`·`quality:` = C, 그 외 기존 섹션은 수정 금지. 다른 섹션 줄은 건드리지 않는다(git 이 다른 hunk 로 병합) |
| `src/agents/common.py` | 0단계에서 공통 함수 추가 후 B 소유. A·C 는 import 만 |
| `src/observability.py` | 0단계 후 동결(A·C import 만) |
| `README.md` | A 가 마지막에 조립. B·C 는 자기 내용(Agents 설명·Features·품질 평가·State 해당 항목·Contributors 문구)을 `docs/agent/lane-b.md`·`lane-c.md` 에 써서 넘긴다 |
| `pyproject.toml`·`uv.lock` | 이번 계획에서 의존성 변경 없음. 필요해지면 A 가 계약 PR 로 |

### 11-5. 기존 테스트 담당

| 테스트 | 대상 모듈 | 담당 |
|---|---|---|
| `test_r1_graph.py`·`test_integration_graph.py`·`mock_nodes.py`·`conftest.py`·`test_r1_schema.py` | graph·schema | A |
| `test_r3_retrieval.py`·`test_r3_web.py`·`test_r3_stakeholder.py`·`test_r4_*.py` | rag·tools·에이전트 | B |
| `test_r5_*.py` | synthesis·report·output | C |

### 11-6. 진행 순서와 merge 순서

| 단계 | A | B | C |
|---|---|---|---|
| 0 | 계약 커밋 (§11-3), 통합 브랜치 push | — | — |
| 1 | 뼈대: 기본 계획 `orchestrator`, `dispatch`, `worker`(스텁 에이전트로), `selection`, `collect_evidence` 입력부, 그래프 재배선(품질 노드는 계약 스텁을 통과시키는 테스트용 대역) → 핵심 1·4·9 | 에이전트 Task 반영·공통 함수 교체·`worker_meta` | `report_manifest`·`claim_ids`·`quality_eval` 코드 검사·`next` 결정 (픽스처로) |
| 2 | 실패 처리·재시도 대상 선택·웹 예산 분배 → 핵심 5·6·7 | stakeholder Task 폴더·웹 예산·`web_store.subdir` | publish guard·partial 발행·쪽수 측정·압축, `settings` 의 `report:`·`quality:` 보정 |
| 3 | LLM 분해 계획, τ·T 보정 → 핵심 2·3 | (B 완료 → **PR ①**) | Judge(2안)·층화 표본·`apply_review` (→ **PR ②**) |
| 4 | **PR ③**: B·C merge 후 실제 노드 연결, 통합 테스트(핵심 8·10·11·12 E2E mock), `--human-review` | 리뷰 지원 | 리뷰 지원 |
| 5 | E2E 실제 실행 + 도메인 비교 실행, 트레이스, `docs/evidence/agent/`, README 조립 | 실행 결과 확인(근거·웹) | PDF 쪽수·보고서 구성 확인 |

- **merge 순서**: 계약 커밋 → PR ① B → PR ② C → PR ③ A. B·C 는 서로 파일이 겹치지 않아 순서를 바꿔도 된다. A 는 마지막에 실제 노드를 연결하므로 B·C 가 들어온 뒤 merge 한다.
- PR 마다: 담당 파일 밖 변경 없음 확인(`git diff --stat agent/orchestrator-workers...`), `uv run pytest -q` 전체 통과, 다른 두 사람 중 한 명 리뷰.
- 6단계(Judge 없이 제출해야 할 때)의 표기 규칙은 그대로: README·보고서에 "코드 기반 평가(1안)".

### 11-7. 레인 간 계약 요약 (누가 무엇을 주고받나)

| 생산자 → 소비자 | 계약 | 정의 위치 |
|---|---|---|
| A(planner) → B(에이전트) | `state["task"]` = `Task` (queries·focus·technologies·web_budget) | `schema.Task` |
| B(에이전트) → A(worker 어댑터) | `{역할: Assessment, "trace", "worker_meta"}` | §11-4 B, 픽스처 `worker_result_*.json` |
| A(collect_evidence) → C(synthesis·report·validate·reference·review) | State 의 관점별 Assessment 4개 + `research` + registry + `gaps`(kind 포함) | `state.py`, 픽스처 `state_after_collect.json` |
| C(quality_eval) → A(그래프) | `quality_eval["next"]` ∈ {orchestrator, synthesis, report, human_review, publish}, `claim_flags`, `retry_count` 는 읽기만 | `schema.QualityEval` |
| A(planner 재계획) ← C(quality_eval) | `quality_eval["verdicts"][기준]["target_cells"]` — 재계획 대상 칸 | `schema.QualityEval` |
| A·C → 관측성 | `log_decision()` | `src/observability.py` |

### 11-8. Contributors (README 에 그대로)

- A : Orchestrator 설계(동적 계획·Dynamic Fan-out·재계획), Worker 어댑터·Fallback, 그래프·State 통합, 실행·트레이스
- B : Worker 에이전트(Task 기반 조사), 근거 수집·웹 수집 분리, 공통 함수 정리
- C : 종합·보고서 생성, 품질 평가 노드(Hybrid)·재작업 라우팅, 발행·분량 관리, Human Review

(이름은 팀에서 채운다. PM·PL 역할은 쓰지 않는다.)

## 12. 실증과 제출물

- **기본 실행** = 제출 보고서 + 동적 동작 트레이스: research → 계획(Task n개와 사유) → `Send` Worker n개 병렬 → `collect_evidence` → 보고서 → 품질 평가 → (있었다면) 재계획 m개.
- **Dynamic Fan-out 실증**: 입력 도메인만 다른 실행 2개의 계획을 나란히 제시(Task·Worker 수 차이). 최초 계획의 동적 분할만으로도 유효하며, 재계획은 일어난 경우 추가 근거다. 보완 프로파일(`config/profiles/replan-demo.yaml`)은 공개하되 "질의를 줄이면 반드시 재계획된다"고 보장하지 않는다. README 에 "기본 실행"과 "검증용 프로파일"을 구분. 결과를 조작하거나 실패를 꾸미지 않는다.
- 트레이스마다 입력·프로파일·`run_id`·라운드별 Task 수·실패·재시도·발행 상태(completed/partial)를 README 표로 연결. `docs/tracing/tracing-1.png` … (파일명 순서)
- **재현성 증빙**: 제출 실행과 비교 실행의 `plan`·`decisions.jsonl`·`quality-v{n}.json`·`run.json` 을 `docs/evidence/agent/<run_id>/` 에 커밋. 재실행 시 달라질 수 있는 것(웹 검색 결과와 stakeholder 근거)과 같게 나오는 것(코퍼스 기반 Task 수) 명시.
- 보고서 PDF: 지난 RAG 과제와 같은 목차, SUMMARY 맨 앞·REFERENCE 맨 뒤(현행 충족), 실제 인용 자료만 REFERENCE(현행 충족), TRL 공개 정보 기반 추정 표시(현행 유지), 10쪽 이하, partial 이면 첫머리 표시.
- README: Subject / Overview(Objective · Pattern 과 선정 이유 · trade-off · 동적 처리) / Selected Technologies(SW TurboQuant · HW ITME, 각 이유) / Features(근거 추출, 확증 편향 방지, 품질 평가, Worker 실패 정책과 Gap 종류) / Tech Stack(LangGraph · Generator/Judge `gpt-4.1-mini` · Retrieval FAISS — 지표 미측정 · Embedding multilingual-e5-small 과 이유) / Agents(Orchestrator · 선행 research · Workers 4관점 · collect_evidence·synthesis · report · evaluator) / State Schema 7항목(§6-2) / Architecture 이미지 / Directory(실제 폴더와 일치) / Usage / Contributors(PM·PL 제외) / 한계(동일 모델 Judge, 표본 범위, 계획 비결정성, 프로세스 간 재개 미지원, partial 발행 조건, 칸 단위 교체 시 새 결과에 다시 나오지 않은 기존 Claim 은 사라짐).

### 모듈 구조 (담당: §11-4)

```
src/orchestrator/
  planner.py      # [A] 사전 조사 + 개수 규칙 + LLM 분해 + 폴백, 재계획(대상·우선순위·웹 예산)
  dispatch.py     # [A] dispatch(): plan → Send
  selection.py    # [A] select_active_worker_results()
  evaluator.py    # [C] quality_eval + route_after_eval + apply_review
  versions.py     # [C] publish guard
src/agents/worker.py   # [A] worker 노드: WORKERS, try/except, classify, adapt
src/agents/common.py   # [B] task_for, CITATION_RE, gaps_from_text, event (D4~D6)
src/agents/*.py        # [B] 기존 에이전트 유지 (task 반영) / synthesis.py·report.py 는 [C]
src/tools/web_store.py # [B] subdir 인자
src/output/*.py        # [C] pdf·reference·validate·review
src/observability.py   # [0단계] log_decision()
src/graph.py · app.py  # [A]
src/schema.py · src/state.py · config/settings.yaml   # [0단계 계약] 이후 §11-4 공유 파일 규칙
```

---

## 13. 결정 사항 (2026-10-07 확정)

| # | 결정 | 확정 | 반영 |
|---|---|---|---|
| 1 | 부분 재계획 시 결과 보존 방식 | **칸 단위 교체** — 재계획한 칸은 최신 라운드 결과로 통째 교체. 무효 Claim 은 `claim_flags` 로 항상 제외. 유효 Claim 소실은 재계획 Task 에 기존 유효 근거를 넘겨 완화하고 README 한계에 기재 | §5, §10 테스트 8, §12 |
| 2 | `route_after_eval` 수리 경로 | **유지** — `orchestrator \| synthesis \| report \| publish` | §4, §7-2 |
| 3 | 과제와 무관한 정리 | **`scripts/r1_smoke.py` 만 삭제**. `rank-bm25`·`query_kw.py`·`scripts/ingest.py` 는 유지 | §0-3, §11 단계 1 |
| 4 | 기타 | 논문 4편 출처 묶음(1저자 소속)·지난 PDF 14쪽 실측 — 담당 C 가 2단계에서 처리, τ·T 보정 — 담당 A | §11-4 |
| 5 | 3인 분업 | 통합 브랜치 `agent/orchestrator-workers` + 작업 브랜치 `agent/ow-orchestration`(A)·`agent/ow-workers`(B)·`agent/ow-quality`(C), 0단계 계약 커밋 후 분기, merge 순서 B → C → A | §11 |

## 부록. 이력

| 판 | 주요 내용 |
|---|---|
| v1~v3 | Supervisor 패턴으로 설계 |
| v4 | 강사 피드백으로 Orchestrator-Workers 전환 |
| v5 | 과제 페이지 재대조 6건 |
| v6 | 교수님 피드백 전면 반영 (`research → orchestrator`, `WORKERS`, `Send`, try/except, `round`, `worker → collect_evidence`, `route_after_eval`, partial 발행, `MAX_RETRY=2`·`MAX_WORKERS=8`) |
| **v7 (이 문서)** | eval5: Gap 종류 분리(실행 실패 Gap 은 커버리지 불인정), `claim_flags`, Worker 어댑터 책임(오류 분류·timeout·Claim ID 재부여), 실행 전체 웹 예산, 결과 중복·정렬, publish guard·synthesis 버전, `apply_review`, 10쪽 초과 partial 발행 방지. 코드 검토 D1~D11. recursion 기본값 근거 명시. 결정 확정: 칸 단위 교체, 수리 경로 유지, `r1_smoke.py` 만 삭제 |
| **v8 (이 문서)** | 3인 분업: 통합 브랜치 + 작업 브랜치 3개, 0단계 계약 커밋, 파일 단위 소유, 공유 파일 규칙, 레인 간 계약, merge 순서. State 에 관점별 Assessment 4개 키 추가(출력 계층이 `state[역할]` 로 읽음), `route_after_eval` 은 `quality_eval["next"]` 만 읽음 |
