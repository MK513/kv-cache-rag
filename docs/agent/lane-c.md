# 담당 C — 출력 계층 (`agent/ow-quality`) 인계 문서

계획서 §11-4 C 레인 작업 결과와, A 가 PR ③ 에서 그래프에 연결할 때 필요한 것, README 에 옮길 문구를 적는다.

## 1. 바뀐 파일

| 파일 | 내용 |
|---|---|
| `src/agents/synthesis.py` | 출력 `GroundedSynthesis`(일치·차이 항목마다 `claim_ids`). LLM 출력 형식 `SynthesisDraft`. 입력에 Claim ID 표시, 모르는 ID 는 버림(`trace.dropped_refs`). `claim_flags` invalid Claim 제외. 품질 평가가 종합 문제로 돌려보내면 사유를 재작성 지시로 넣음. `synthesis_version += 1`, `round = retry_count`. `PERSPECTIVES` 사용 |
| `src/agents/report.py` | 보고서 모델 하나에서 Markdown + `report_manifest` 생성(`build()`), `report_version += 1`. 칸당 표시 상한·압축 단계(0·1·2), 종합 참조 Claim·기술별 한계·비용 Claim 1건 고정, Gap 종류 표시, 편향 예외 공개, partial 표시(`build(partial=...)`), §6 한계 문구 교체(Judge 범위 n/N). `CITATION_RE`·`ASSESSMENT_ROLES` 공용 것 사용 |
| `src/orchestrator/evaluator.py` | `quality_eval`(코드 검사 → Judge), `route_after_eval`, `apply_review`, `route_after_review` |
| `src/orchestrator/versions.py` | `check_publish_guard()`, `PublishGuardError` |
| `src/orchestrator/quality_rules.py` (신규) | report·evaluator 가 함께 쓰는 순수 규칙(한계·비용 Claim, 수용 가능한 Gap, 출처 묶음·편향 지표, 층화 표본) |
| `src/output/pdf.py` | `count_pages()`(pypdf), 새 `publish`(guard → 통과면 평가한 PDF 그대로 / 미달이면 partial 재렌더링 → 10쪽 초과 시 최소 수준 압축), `--measure` CLI. 옛 흐름은 `_publish_legacy` 로 그대로 |
| `src/output/reference.py`·`validate.py`·`review.py` | 역할 목록 → `ASSESSMENT_ROLES`(D2). `reference.cited_source_ids()`. `review.prepare_worksheet()`·`load_review_verdicts()` |
| `config/settings.yaml` `report:`·`quality:` | 표시 상한 잠정값, `quality.judge`, `source_groups`(논문 4편 1저자 소속 포함), ITME 편향 예외 |
| 테스트 | `tests/test_quality_eval.py`·`tests/test_quality_report.py`·`tests/quality_fixtures.py` 신규, `test_r5_synthesis.py`·`test_r5_report.py` 갱신 |
| `tests/test_contract.py` (A 소유) | `test_lane_stubs_are_explicit` 만 수정 — C 스텁이 구현돼 `NotImplementedError` 를 기대하던 부분을 worker 스텁 확인 + guard 픽스처 통과로 바꿨다. A 확인 필요 |

## 2. A 가 그래프에 연결할 것 (PR ③)

```python
from src.orchestrator.evaluator import quality_eval, route_after_eval, apply_review, route_after_review
from src.output.pdf import publish

b.add_edge("synthesis", "report")
b.add_edge("report", "quality_eval")
b.add_conditional_edges("quality_eval", route_after_eval,
                        ["orchestrator", "synthesis", "report", "human_review", "publish"])
b.add_edge("human_review", "apply_review")          # human_review 뒤에서 interrupt_after
b.add_conditional_edges("apply_review", route_after_review, ["synthesis", "publish"])
b.add_edge("publish", END)
```

- `human_review` 노드(A)는 멈추기 전에 `review.prepare_worksheet(state)` 를 호출해 `runs/<run_id>/review.csv` 를 만든다(표시된 Claim 만, 이미 있으면 새 Claim 행만 추가, 원장 판정 이월). 안 불러도 `apply_review` 가 만들지만 그 경우 판정 없이 지나간다.
- `quality_eval` 은 `quality_eval`·`claim_flags`·`repair_count`·`step_count`·`last_decision`·`trace`(+ 상한 도달 시 `stop_reason`)를 쓴다. `retry_count` 는 읽기만 한다.
- 재계획 대상 칸: `quality_eval["verdicts"][기준]["target_cells"]` (`"perspective:tech"`). 근거 문제 기준은 `groundedness_l1`·`coverage`·`bias`. 상세 사유는 `runs/<run_id>/quality-v{n}.json` 의 `problems.evidence`.
- `publish` 가 `run_status`(completed / partial / failed)와 `report_paths` 를 쓴다. 옛 `final_check` 의 `run_status` 판정은 새 흐름에서 필요 없다.
- `route_after_eval` 은 publish·human_review 로 갈 때 publish guard 를 보고, 구버전이면 `"report"` 를 돌려준다(엣지 목록 안). 같은 버전 PDF 손상은 `PublishGuardError` → `app.py` 가 failed 기록.
- `run_config.human_review`(bool) 로 Human Review 경로를 켠다. `run_config.quality_judge`(bool)가 있으면 설정 `quality.judge` 보다 우선한다 — 통합 테스트(API 키 없음)에서는 `False` 로 둔다.
- `tests/mock_nodes.py` 의 `synthesis` 대역이 문자열 agreements 를 내면 L2 코드 검사에서 실패한다(근거 Claim 없음). 새 흐름 E2E mock 은 `claim_ids` 를 넣거나 `tests/quality_fixtures.py` 의 종합 예시를 쓴다.

### 라우팅 규칙 해석 (계획서 §7-2)

| 순서 | 조건 | 다음 |
|---|---|---|
| 0 | Judge 재시도 후 실패 | publish (partial, `stop_reason=Judge 실패`) |
| 1 | 문제 없음 | human_review(켠 경우) · publish |
| 2 | `step_count >= max_steps` | publish (partial) |
| 3 | 근거 문제 — `retry_count >= MAX_RETRY` 면 publish(partial), 아니면 | orchestrator |
| 4 | 종합 문제(L2·중립성·③ 근거 수집됨), `repair_count < max_repairs` | synthesis |
| 5 | 보고서 문제(표시 누락·구조·예외 미공개·10쪽 초과), `repair_count < max_repairs` | report |
| 6 | `repair_count` 소진 | publish (partial) |

계획서 표의 2번(`retry_count >= MAX_RETRY` → publish)은 **근거 문제가 있을 때만** 적용했다. 재계획 예산이 끝났어도 종합·보고서 수리는 `repair_count` 상한 안에서 계속할 수 있게 했다 — 재계획 라운드와 수리는 서로 다른 상한이기 때문이다.

## 3. README 에 옮길 문구 (C 담당 부분)

### Features — 품질 평가 (Hybrid: 코드 검사 + LLM Judge)

보고서가 만들어지면 `quality_eval` 이 **보고서에 실제로 표시된 것**(`report_manifest`)을 6개 기준으로 평가한다. 기준마다 결정적 코드 검사를 먼저 하고, 통과한 기준만 LLM Judge(`gpt-4.1-mini`, temperature 0, 고정 프롬프트)가 의미를 판정한다.

| 기준 | 코드 검사 | Judge | 실패 시 |
|---|---|---|---|
| 근거 연결 L1 | 표시 Claim → Evidence → Source 연결 | 인용 구절이 주장을 뒷받침하는가 (60건 이하 전수, 넘으면 관점×기술 층화 표본) | Claim 무효 표시 → 그 칸 재계획 |
| 종합 근거 L2 | 종합 문장마다 참조 Claim 이 있고 표시된 유효 Claim 인가 | 참조 범위를 벗어난 서술인가 | synthesis 재작성 |
| 중립성 | 승자·추천·우열 표현 패턴 | 우열 판정·조건 다른 수치의 단순 비교 | synthesis 재작성 |
| 편향 통제 | ① 기술별 출처 묶음 ≥ 2 ② 단일 묶음 ≤ 60% | ③ 한계·비용 근거를 빼고 성능만 실었는가 | 재계획 / 예외 공개 / 재작성 |
| 관점 커버리지 | 4관점×2기술 칸마다 유효 Claim 또는 조사 이력 있는 근거 공백 | 관점 기준을 실질적으로 다루는가 | 그 칸 재계획 |
| 구조·분량 | SUMMARY 처음·REFERENCE 끝·인용–참고문헌 일치·렌더링 PDF ≤ 10쪽 | — | report 재조립·압축 |

- 출처 묶음: 논문은 1저자 소속(TurboQuant=Google, ITME=SK hynix, InfiniGen=서울대, PIM-CXL=한양대), 그 밖은 발행 주체. 설정 `quality.source_groups`.
- 사전 정의 예외: ITME 직접 원문은 SK hynix 자료뿐이라 ①② 가 실패해도 보고서 §6 에 사유·조사 범위를 공개하면 통과로 본다. ③ 은 예외와 무관하게 매번 검사한다.
- 실행 실패(`execution_gap`)·무효 근거(`invalid_evidence`)는 커버리지 근거가 되지 못한다.
- 판정 전문: `runs/<run_id>/quality-v{n}.json`, 결정·사유: `runs/<run_id>/decisions.jsonl`.

### Features — 발행·분량 관리

- 10쪽 상한은 문자 수 추정이 아니라 렌더링한 PDF 쪽수로 확인한다. 넘으면 표시 상한을 단계적으로 줄인다(압축 0 → 1 절반 → 2 칸마다 1개). SUMMARY·REFERENCE·편향 예외 문구는 줄이지 않는다.
- 상한(재계획·수리·step)에 닿거나 Judge 가 실패하면 **partial** 로 발행한다. 보고서 첫머리와 §6 에 미달 기준·사유를 적고, 10쪽을 넘으면 최소 표시 수준으로 다시 렌더링한다. 통과로 표시하지 않는다.
- publish guard: 종합 버전 = 보고서가 기반한 종합 버전, 평가한 보고서 버전 = 현재 보고서 버전, 평가한 PDF sha256 = 제출 PDF. 어긋나면 보고서부터 다시 만들고, 같은 버전 파일 손상은 실행 오류로 끝낸다.

### State 해당 항목

| 필드 | 쓰는 노드 |
|---|---|
| `synthesis` (+`synthesis_version`·`round`) | synthesis |
| `report`·`report_manifest`·`report_version` | report |
| `quality_eval`·`repair_count`·`step_count`·`claim_flags`(L1 무효)·`stop_reason` | quality_eval |
| `claim_flags`(사람 부결) | apply_review |
| `run_status`·`report_paths` | publish |

### 한계 (README 한계 절에 추가)

- Judge 는 생성과 같은 `gpt-4.1-mini` 라 자기평가 편향이 남을 수 있다. L1 은 60건을 넘으면 층화 표본만 Judge 가 보고 나머지는 코드 검사만 거친다(보고서 §6 에 n/N).
- 한계·비용 Claim 판별과 우열 표현 검사는 키워드 기반이라 놓치는 표현이 있을 수 있다(Judge 가 보완).
- 표시 상한(`report.max_claims_per_cell` 등)은 잠정값이다 — §4 미완료 항목 참고.

### Contributors

- C : 종합·보고서 생성, 품질 평가 노드(Hybrid)·재작업 라우팅, 발행·분량 관리, Human Review

## 4. 미완료 · 확인 필요

- **쪽수 실측 미완료.** 이 환경에 `libpango` 가 없어 weasyprint 렌더링을 못 했다. `report:` 표시 상한은 잠정값이다. pango 가 있는 환경에서 E2E 실행 후 `uv run python -m src.output.pdf --measure runs/<run_id>/report-v1.md` 로 기본 상한 9쪽 이하, 최소 수준(압축 2) 10쪽 이하를 확인해 값을 고정해야 한다(계획서 §13 결정 4 의 "지난 PDF 14쪽 실측").
- **실제 Judge 호출 미검증.** 테스트는 대역 모델로만 돌았다. 첫 E2E 실행에서 `quality-v1.json` 의 판정·사유를 사람이 한 번 읽어 볼 것.
- `kv-cache-survey` 1저자 소속은 원문 첫 쪽에 없어 독립 묶음으로 뒀다.
