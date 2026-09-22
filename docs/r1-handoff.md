# R1 구현 및 인수 기록

기준: 최신 설계서(2026-09-22) §7·§8, 사용자 지정 R1 역할.
기준 main: `f96ec37`(R3 병합 후). 브랜치: `feat/r1-graph-state-schema`.

## 구현 범위

| 항목 | 구현 위치 | 확인 방법 |
|---|---|---|
| 공용 Pydantic 계약 7종 | `src/schema.py` | R3 실물 출력 round-trip, 인용 사슬·failed 규칙 검사 |
| State 14필드 · 필드별 단독 쓰기 주체 | `src/state.py` · [interface.md ①](interface.md) | fan-out 4노드 동시 실행에서 `InvalidUpdateError` 없음 |
| 그래프 재배선 (조건부 엣지 1개) | `src/graph.py` | `docs/evidence/r1/graph.mmd` |
| Source·Evidence 병합과 병합 오류 | `src/graph.py` `collect_evidence` | 같은 ID·다른 내용 → `merge-errors.json` + 실행 종료 |
| run_id · `runs/<run_id>/` · run_status | `app.py` | `runs/<run_id>/run.json` 의 running → 최종 상태 |
| 검토 대기 저장·재개 | `save_draft` · `app.py --resume` | `draft.json` 수정 후 `final_check` 부터 이어 실행 |
| 4경로 재현 | `scripts/r1_smoke.py` | `docs/evidence/r1/runs/checks.json` |

**변경 계약은 [interface.md ①](interface.md)** 가 현행이다. 구버전 17키·관점 dict는 대체됐다.

## 설계 판단과 이유

- **조건부 엣지는 `final_check` 하나.** 근거 보완 재조사·인용 수정 재작성은 노드 내부
  루프로 내렸다(R3 `stakeholder` 가 이미 그렇게 한다). 그래프에 되돌아오는 엣지를 두면
  경로가 곱해져 실행 경로를 재현할 수 없다.
- **병합 오류는 분기가 아니라 예외.** 같은 ID 에 다른 내용이 오면 어느 원문을 가리키는지
  고를 근거가 없다. 조용히 덮어쓰면 R5 의 원문 대조가 엉뚱한 출처를 가리킨다.
  `merge-errors.json` 을 남기고 끝낸다 — 어긋난 근거로 종합·검토에 LLM 을 태우지 않는다.
- **검증 지점은 `collect_evidence` 하나.** 노드마다 `model_validate` 를 부르지 않는다.
- **재개는 checkpointer 가 아니라 `draft.json`.** 사람이 `review` 를 고쳐 놓은 것이 재개
  입력이므로 `final_check` 부터 이어 간다. 평가·종합 LLM 을 다시 태우지 않는다.
- **`fail` 은 `report` 를 쓰지 않는다.** 구버전 `abort` 는 실패 문구를 보고서 자리에
  넣었다. 단독 쓰기 주체 계약이 깨지고, 실패한 실행이 보고서 자리를 차지한다.
- **재시도 횟수는 State 필드가 아니다.** 근거 보완은 노드 내부, 재작성은 `review["round"]`.
  설계서 §7 표에 별도 행이 있으면 되돌린다.

## run_status 판정

| 값 | 조건 |
|---|---|
| `running` | `start_run` 이 실행 시작 시 `run.json` 에 먼저 기록 |
| `failed` | Assessment 중 `failed`, 또는 `review.errors` 잔존, 또는 병합 오류·예외 |
| `partial` | 검토 대기(`review_status="pending"`) 또는 Assessment 중 `partial` |
| `completed` | 위에 해당하지 않고 보고서 생성 |

근거 공백(`partial`)은 실패가 아니다. 보고서는 내고 상태로만 표시한다.
검토 대기만 초안으로 멈춘다.

## 검증

```bash
uv sync --extra test
uv run pytest -q                                        # 63 passed
uv run python -m scripts.r1_smoke --root /tmp/kv-r1-run  # 4경로 + 재개
```

- `tests/test_r1_schema.py` — R3 가 커밋한 합성 실행 결과를 그대로 통과시키는지 round-trip 대조.
- `tests/test_r1_graph.py` — 통과 / 검토대기+재개 / 미해결오류 / 병합오류 / 부분 실패.
- smoke 와 테스트는 `tests/mock_nodes.py` 하나를 공유하고, pytest 가 smoke 스크립트를
  그대로 실행한다. 증빙 스크립트가 따로 썩지 않게 한 것이다.
- 증빙: [`docs/evidence/r1/`](evidence/r1/) — `graph.mmd`, `pytest.txt`, `runs/checks.json`
  과 경로별 `run.json`·`state.json`·`trace.jsonl`·`draft.json`·`merge-errors.json`.

## 한계 — 이것으로 끝났다고 말하지 않는 것

- **평가 노드는 전부 mock 이다.** 증빙의 보고서·주장은 합성 데이터이며 기술 평가가 아니다.
  검증한 것은 배선·병합·상태 판정·저장이고, 검색·LLM·인덱스는 타지 않았다.
- **`uv run python app.py` 는 끝까지 돌지 않는다.** R4/R5 이행 전이라 `research` 에서
  `NotImplementedError` 로 멈추고 실패를 기록한다. mock 으로 우회해 통과시키지 않았다.
- **설계서 §7 표의 14필드 목록을 직접 대조하지 못했다.** PDF 가 저장소에 없어 R3 계약과
  노드 목록에서 역산했다. 표와 다르면 [interface.md ①](interface.md) 을 먼저 고친다.
- 한 `run_id` 에 한 프로세스만 붙는다(R3 계약과 동일). 같은 실행 폴더에 여러 프로세스가
  동시에 쓰는 것은 지원하지 않는다.
- `collect_evidence` 는 fan-out 4개만 병합한다(설계서 §8). `research` 인용을 R5 가
  역참조해야 하면 `src/graph.py` 의 `FANOUT` 앞에 `"research"` 를 붙인다.

## 역할별 연결 작업

| 역할 | 할 일 |
|---|---|
| R2 | `index.build()` 의 `manifest` 를 그대로 쓴다. Source 를 만들 때 `source_id`·`run_id`·`collection`·`allowed_uses` 4개는 필수 |
| R4 | 평가 4노드가 `schema.Assessment` dict 를 반환하고 자기 필드에만 쓴다. `registry` 에 쓰지 않는다 |
| R5 | `synthesis.py` 의 `Synthesis`·`Conflict` 정의를 지우고 `src/schema.py` 에서 import. `review` 노드가 `{errors, review_status, round}` 를 쓴다. `status=failed` Assessment 를 통과시키지 않는다 |
