# R1 구현 및 인수 기록

기준: `RAG-Design_판교-8반_권수진 권예리 김민 박인기 정승원.pdf` (2026-09-22) §6·§7·§8·부록 A.
기준 main: `f96ec37`(R3 병합 후). 브랜치: `feat/r1-graph-state-schema`.

**설계서 원문과 대조했다.** State는 §7 표를, 그래프는 부록 A의 Mermaid 소스를 그대로 옮겼다.

## 구현 범위

| 항목 | 구현 위치 | 확인 방법 |
|---|---|---|
| 공용 Pydantic 계약 7종 | `src/schema.py` | R3 실물 출력 round-trip, 인용 사슬·failed 규칙 검사 |
| State §7 표 (14행 / 17필드) | `src/state.py` · [interface.md ①](interface.md) | fan-out 동시 실행에서 `InvalidUpdateError` 없음 |
| 그래프 부록 A 소스 | `src/graph.py` | `docs/evidence/r1/graph.mmd` 의 엣지가 부록 A와 일치 |
| 다섯 Assessment 병합과 병합 오류 | `collect_evidence` | 같은 ID·다른 내용 → `merge-errors.json` + 실행 종료 |
| `final_check` 판정 | `src/graph.py` · [interface.md ④](interface.md) | 인용 오류 + 내용 검토 완료 여부를 함께 확인 |
| run_id · `runs/<run_id>/` · run_status | `app.py` | `run.json` 의 running → 최종 상태 |
| 검토용 초안 저장과 재개 | `save_draft` · `app.py --resume` | `draft.json` 수정 후 `review` 부터 이어 실행 |
| 경로 재현 | `scripts/r1_smoke.py` | `docs/evidence/r1/runs/checks.json` |

## 설계서를 그대로 따른 지점

- **§7 표의 묶음 표기를 필드로 나눴다.** `run_id / run_config`, `validation / review_status`,
  `report / report_paths` — 14행이 Python 필드 17개가 된다. 나누는 판단은 R1 소유다(R3 계약).
- **`gaps` 는 최상위 필드다.** §7: *합류 후 수집, 종합 후 순차 병합.* 레지스트리 안에 넣으면
  종합 노드가 `collect_evidence` 소유 필드를 건드리게 돼 단독 쓰기 원칙이 깨진다.
- **`collect_evidence` 는 다섯 Assessment 를 병합한다.** 네 평가 + 기술 조사.
  §9가 *기술 조사와 종합 단계에서 사용한 근거도 빠짐없이 포함*하라고 했고, §8은 참고문헌을
  실제 인용에서 역으로 만들라고 했다. 기술 조사 근거가 레지스트리에 없으면 불가능하다.
- **되돌아오는 엣지는 `save_draft → review` 하나뿐이다**(부록 A). 재시도는 노드 내부의
  최대 1회 처리이므로 평가·종합으로 되돌아오는 엣지를 만들지 않았다.
- **`report` 뒤에 `publish`(레이아웃 확인 후 제출본 저장)가 있다**(부록 A).
  `report_paths` 는 이 노드가 쓴다.

## 판단이 필요했던 지점

- **검토 대기에서 실행을 멈춘다.** 부록 A는 `save_draft → review` 엣지를 그렸지만, 사람의
  검토 없이 그대로 두면 `review → final_check → save_draft → review` 가 무한히 돈다.
  `interrupt_after=["save_draft"]` 로 정지시키고, 재개는 고친 `draft.json` 을 들고
  `build_graph(start="review")` 로 다시 들어온다. checkpointer 는 정지에만 쓰고
  실행 간 상태는 파일(`draft.json`)로 넘긴다 — 실행 사이에 사람의 시간이 끼기 때문이다.
- **병합 오류는 그래프 분기가 아니라 예외다.** §7은 *병합 오류로 처리한다* 까지만 정한다.
  어긋난 근거로 종합·검토에 LLM 을 태울 이유가 없어 `MergeConflict` 로 끝내고
  `merge-errors.json` 을 남긴다. `app.py` 가 받아 `run_status=failed` 로 기록한다.
- **`Claim` 의 `stakeholder_group`·`actor`·`context` 를 선택으로 뒀다.** R3 의 `ClaimDraft` 는
  필수로 검사하지만 R4 의 TRL·시장성 Claim 에는 발언 주체가 없다. 이해관계자 Claim 의
  엄격한 검사는 R3 의 draft 단계가 계속 맡는다.
- **`fail` 은 `report` 를 쓰지 않는다.** §8: *제출용 출력을 막는다.* 구버전 `abort` 는 실패
  문구를 보고서 자리에 넣어 단독 쓰기 주체 계약을 깼다.

## run_status 판정 (§7 · 부록 A)

| 값 | 조건 |
|---|---|
| `running` | `start_run` 이 실행 시작 시 `run.json` 에 먼저 기록 |
| `failed` | Assessment 중 `failed`, `validation.errors` 잔존, 또는 병합 오류·예외 |
| `partial` | `review_status="pending"`(검토 대기) 또는 Assessment 중 `partial` |
| `completed` | 위에 해당하지 않고 보고서 생성 |

§7의 정의를 그대로 옮겼다 — *부분 결과는 일부 항목을 평가 보류로 남겼으나 포함된 주장의
검증은 통과한 경우*, *구조 오류나 미해결 인용 오류가 남으면 failed*,
*내용 검토가 끝나지 않았으면 review_status 를 pending 으로 두고 검토용 초안만 저장*.

## 검증

```bash
uv sync --extra test
uv run pytest -q                                         # 64 passed
uv run python -m scripts.r1_smoke --root /tmp/kv-r1-run  # 5경로
```

- `tests/test_r1_schema.py` — R3 가 커밋한 합성 실행 결과를 round-trip 으로 대조.
- `tests/test_r1_graph.py` — 통과 / 검토대기 / 검토 후 재개 / 미해결오류 / 병합오류 /
  부분 실패 / gaps 순차 병합.
- smoke 와 테스트는 `tests/mock_nodes.py` 하나를 공유하고, pytest 가 smoke 스크립트를
  그대로 실행한다. 증빙 스크립트가 따로 썩지 않게 한 것이다.
- 증빙: [`docs/evidence/r1/`](evidence/r1/) — `graph.mmd`, `pytest.txt`, `runs/checks.json`
  과 경로별 `run.json`·`state.json`·`trace.jsonl`·`draft.json`·`merge-errors.json`·
  `validation-errors.json`.

## 한계 — 이것으로 끝났다고 말하지 않는 것

- **평가 노드는 전부 mock 이다.** 증빙의 보고서·주장은 합성 데이터이며 기술 평가가 아니다.
  검증한 것은 배선·병합·상태 판정·저장이고, 검색·LLM·인덱스는 타지 않았다.
- **`uv run python app.py` 는 끝까지 돌지 않는다.** R4/R5 이행 전이라 `research` 에서
  `NotImplementedError` 로 멈추고 실패를 기록한다. mock 으로 우회해 통과시키지 않았다.
- **`publish` 노드는 스텁이다.** PDF 변환과 레이아웃 확인(§9 — SUMMARY 분량, 한글 글꼴,
  표·그래프 잘림, 참고문헌 위치)은 R5 가 구현한다.
- 한 `run_id` 에 한 프로세스만 붙는다(R3 계약과 동일). 잠금으로 강제하지 않는다.
- `run_config.model` 은 설정값을 복사할 뿐이다. §6이 요구하는 **실행 시 모델 식별자·버전과
  토큰 사용량 기록**은 LLM 을 실제로 호출하는 R4/R5 단계에서 붙여야 한다.

## 역할별 연결 작업

| 역할 | 할 일 |
|---|---|
| R2 | `index.build()` 의 `manifest` 를 그대로 쓴다. Source 를 만들 때 `source_id`·`run_id`·`collection`·`allowed_uses` 4개는 필수 |
| R4 | 평가 4노드 + `research` 가 `schema.Assessment` dict 를 반환하고 자기 결과 키에만 쓴다. 레지스트리·공용 `gaps` 에 직접 쓰지 않는다 |
| R5 | `synthesis.py` 의 `Synthesis`·`Conflict` 정의를 지우고 `src/schema.py` 에서 import. `synthesis` 가 `gaps` 를 순차 병합한다. `review` 가 `validation`·`review_status` 를 쓴다. `report`/`publish` 를 구현하고 `report_paths` 를 남긴다 |
