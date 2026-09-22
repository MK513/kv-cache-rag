# R1 구현 계획 — 그래프 · State · 실행 제어

브랜치 `feat/r1-graph-state-schema` · 기준 커밋 `f96ec37` (R3 병합 후)

R1 산출물은 **나머지 4명의 계약**이다. 그래서 `schema.py` → `state.py` → `graph.py` →
`app.py` 순으로 낸다. 모든 노드는 mock 으로 두고 그래프가 끝까지 도는 것까지가 이번 범위다.
실제 LLM 호출 노드(R4)·검토 노드(R5)·검색(R2)은 이 브랜치에서 건드리지 않는다.

---

## 0. 전제 — 확인 필요 (blocking 아님, 진행하면서 확정)

- 설계서 §7 표의 **14필드 정확한 목록**을 아직 못 봤다. 아래 §2 표는 R3 계약
  (`docs/interface.md` 「R3 최신 설계서 적용 계약」)과 노드 목록에서 역산한 안이다.
  PDF §7 표와 다르면 §2 표를 먼저 고치고 그 다음에 코드를 고친다.
- 기존 `docs/interface.md` ① State 17키는 **구버전**이다. 이 브랜치가 ①을 교체한다.

---

## 1. `src/schema.py` (신설) — 가장 먼저

Pydantic v2. R3가 이미 만들어 둔 wire dict 를 **그대로** 받아들이는 것이 유일한 정답 조건이다.
(`src/tools/web_store.py:179-194`, `src/agents/stakeholder.py:144-188` 이 실물 생산자)

| 모델 | 필드 | 근거 |
|---|---|---|
| `Source` | source_id, run_id, collection, url?, requested_url?, final_url?, title, institution?, author?, published_at\|null, retrieved_at, version?, source_type?, allowed_uses, snapshot_path?, sha256?, body_path?, body_sha256?, page?, scope?, applies_to? | web_store.py 필드 전부 + 색인 출처(R2)용 page/scope/applies_to |
| `Evidence` | evidence_id, source_id, run_id, collection, quote, location, allowed_uses, snapshot_path?, sha256? | web_store.py:192 |
| `Claim` | claim_id, text, technology, kind(fact/inference/hypothesis), evidence_ids, explanation, stakeholder_group?, actor?, statement_date\|null, context? | stakeholder_contract.ClaimDraft + claim_id |
| `Gap` | role, technology, item, reason | stakeholder.py:155 |
| `Assessment` | claims, sources, evidence, gaps, status(completed/partial/failed) | stakeholder.py:186 |
| `Synthesis` | agreements, conflicts, gaps, text | R5 소비. 필드명은 R5와 합의 |
| `Event` | node\|tool, action?, attempt, timestamp, status, + 자유 필드 | trace 한 줄 |

설계 결정:
- `stakeholder_group` / `actor` / `context` 는 **이해관계자 전용**이라 `Optional`. R4의
  maturity/market Claim 이 이 필드를 채우지 않아도 통과해야 한다.
- `model_config = ConfigDict(extra='forbid')` 는 `Claim`·`Gap`·`Assessment` 에만 건다.
  `Source` 는 웹/색인 출처가 필드가 달라 `extra='ignore'`. 엄격하게 막으면 R2 색인 출처가 못 들어온다.
- **검증은 collect_evidence 에서 1회만** 한다. 노드마다 model_validate 를 부르지 않는다.
- Pydantic 모델은 경계에서만 쓰고 State 에는 dict 로 넣는다. LangGraph 직렬화·reducer 가
  dict 기준이고 R3가 이미 dict 를 반환한다.

## 2. `src/state.py` — 14필드 (교체)

| # | 키 | 형식 | 단독 쓰기 주체 |
|---|---|---|---|
| 1 | `run_id` | `str` | `app.py` (초기화) |
| 2 | `run_config` | `dict` | `app.py` — domain/runs_dir/web_top_k/web_context_chars/web{} |
| 3 | `sources_manifest` | `list[dict]` | `setup` (설정확인/적재) |
| 4 | `research` | `dict` (Assessment) | `research` |
| 5 | `maturity` | `dict` (Assessment) | `maturity` |
| 6 | `market` | `dict` (Assessment) | `market` |
| 7 | `stakeholder` | `dict` (Assessment) | `stakeholder` (R3 완성) |
| 8 | `domain_assessment` | `dict` (Assessment) | `domain_assessment` |
| 9 | `registry` | `dict` | `collect_evidence` — `{sources: {id: Source}, evidence: {id: Evidence}, gaps: [...]}` |
| 10 | `synthesis` | `dict` | `synthesis` |
| 11 | `review` | `dict` | `review` (R5) — `{errors: [...], review_status, round}` |
| 12 | `report` | `str` | `report` |
| 13 | `run_status` | `str` | `final_check` / `app.py` |
| 14 | `trace` | `Annotated[list[dict], operator.add]` | 전 노드 (누적) |

- fan-out 4노드가 `registry` 에 직접 쓰지 않는다 → `InvalidUpdateError` 가 구조적으로 불가능.
  병합은 `collect_evidence` 단독 책임.
- 재시도 횟수(`retrieval_round`/`validation_round`)는 **별도 키로 두지 않고** `review["round"]`,
  `research` Assessment 안에 둔다. 14필드를 늘리지 않기 위한 선택이며 §7 표와 다르면 되돌린다.

## 3. `src/graph.py` — 배선 (교체)

```
START → setup → research → [maturity ‖ market ‖ stakeholder ‖ domain_assessment]
      → collect_evidence → synthesis → review → final_check
final_check ─ 통과     → report → END
            ├ 검토대기 → save_draft → END   (run_status=partial, 같은 run_id 로 재개)
            └ 미해결오류 → fail → END        (run_status=failed)
```

- **조건부 엣지는 `final_check` 하나뿐이다.** 기존 `route_after_research` /
  `route_after_eval` 2개 조건부 엣지는 없앤다. 근거 부족 재조사는 `research` 노드 내부 루프,
  인용 오류 재작성은 `review` → `synthesis` 가 아니라 `review` 결과를 State 에 남기고
  `final_check` 가 판정한다. 그래프가 단순해지고 4분기 경로 재현이 쉬워진다.
- `build_graph(**node_overrides)` — 기본값은 실제 구현, 테스트/증빙은 mock 주입.
  (mock 전면 교체가 완료 기준이라 주입점이 필요하다. 이 목적 외 추상화는 만들지 않는다.)
- `collect_evidence`: 4개 Assessment 의 `sources`/`evidence` 를 id 기준 병합.
  **같은 id 에 다른 내용이면 즉시 실패** (`MergeConflict` 예외 → `run_status=failed`, trace 기록).
  비교는 dict 전체 동등성. `gaps` 는 단순 concat.
- `save_draft`: `runs/<run_id>/draft.json` 에 State 스냅샷 저장 후 종료. 재개는 같은 run_id 로
  `app.py --resume <run_id>` — draft.json 을 초기 State 로 읽어 다시 invoke.
  (LangGraph checkpointer 는 쓰지 않는다. 파일 하나로 되는 일에 SqliteSaver 를 붙이지 않는다.)

## 4. `app.py` — 실행 제어

- `run_id` 생성: `YYYYMMDD-HHMMSS-<6자리 hex>`. `--run-id` 로 덮어쓸 수 있고 `--resume` 는 필수 인자.
- 저장소 `runs/<run_id>/`:
  `run.json`(run_config·run_status·시작/종료), `state.json`, `trace.jsonl`, `report.md`,
  `draft.json`(검토대기 시), `web/`(R3 소유, 그대로 둠), `stakeholder*.json`(R3 소유).
- `run_status` 판정:
  | 값 | 조건 |
  |---|---|
  | `running` | 실행 시작 시 `run.json` 에 먼저 기록 |
  | `completed` | report 생성 + 4 Assessment 전부 `completed` |
  | `partial` | report 는 나왔으나 Assessment 중 `partial` 존재, 또는 검토대기 저장 |
  | `failed` | Assessment 중 `failed`, 병합 오류, 미해결 검토 오류 |
- **버그 수정:** 현재 `app.py:16` 의 `Path(__file__).resolve().parents[1]` 는 저장소 **상위**
  디렉토리를 루트로 잡는다 (app.py 가 루트에 있음). `parents[0]` 으로 고친다.
- 기존 `retrieve.TRACE` 전역 회수는 빼고 노드가 trace 를 반환하게 한다.

## 5. `config/settings.yaml`

- 추가: `run:` 블록 (`runs_dir`, `web_top_k`, `web_context_chars`) + `web:` 한도 9종
  (R3 계약 표의 기본값 그대로).
- 제거: `retrieval.rrf_k` / `dense_weight` / `sparse_weight` — R3가 "구버전 잔여, R1 정리 가능"
  이라 명시. `limits.retrieval_round` / `validation_round` 는 노드 내부 루프 한도로 유지.

## 6. 증빙 · 검증

- `tests/test_r1_graph.py` — mock 노드로 4분기 전부:
  ① 정상 completed ② 병합 충돌 failed ③ 검토대기 partial + draft.json + 재개
  ④ Assessment failed → failed. 각 경로에서 `run_status`, `trace`, `runs/<run_id>/` 존재 확인.
- `scripts/r1_smoke.py --root <tmp>` — 4경로를 순서대로 돌려 실행 로그를 남긴다 (R3의
  `r3_smoke.py` 와 같은 방식).
- 그래프 이미지: `graph.get_graph().draw_mermaid()` 결과를 `docs/evidence/r1/graph.mmd` +
  README mermaid 블록. PNG(`draw_mermaid_png`)는 mermaid.ink 네트워크가 필요해 선택 사항.
- `docs/interface.md` ① 표를 §2 표로 교체하고 변경 이유를 R3 절과 같은 형식으로 기록.

## 7. 건드리지 않는 것

`src/agents/{research,maturity,market,domain,synthesis,report}.py`, `src/output/validate.py` 는
구버전 State 키를 쓴다. 이번 브랜치는 **mock 으로 대체**하고 실물 이행은 R4/R5 몫으로 남긴다.
`src/rag/*`, `src/tools/*` 는 R2/R3 소유라 읽기만 한다.

## 8. 작업 순서

1. `src/schema.py` + `tests/test_r1_schema.py` (R3 실제 출력 fixture 로 round-trip) → **먼저 공유**
2. `src/state.py` + `docs/interface.md` ① 교체
3. `src/graph.py` + mock 노드 + `tests/test_r1_graph.py`
4. `app.py` + `config/settings.yaml` + `scripts/r1_smoke.py`
5. 증빙 생성 (`docs/evidence/r1/`), README R1 절 추가
