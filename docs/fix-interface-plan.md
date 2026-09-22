# 인터페이스 정합 수정 계획

브랜치 `fix/interface-mismatch` (`origin/main` = `1bbcdd5` 기준)

**기준은 설계서 PDF다** — `RAG-Design_판교-8반_권수진 권예리 김민 박인기 정승원.pdf`
(§5·§6·§7·§8·§9·부록 A). 코드끼리 어긋나면 PDF를 따르고, PDF가 정하지 않은 것만
`docs/interface.md`가 정한다.

R1~R5가 각자 main에 들어왔지만 **파이프라인은 한 번도 끝까지 돈 적이 없다.**
테스트 82개가 통과하는 이유는 각자 자기 함수를 손으로 만든 state로만 부르기 때문이다.

> **진행 상황 (2026-09-22): Stage 1~7 모두 적용.** 118 tests.
> `tests/test_integration_graph.py` 가 실물 노드로 그래프를 끝까지 돌린다
> (검색·LLM·색인·PDF 렌더링만 fixture, `stakeholder` 는 stub).
> 각 Stage 의 실제 작업은 계획보다 컸다 — 아래 「실제로 나온 것」 참고.

---

## 현상 요약

| # | 증상 | 위치 | 설계서 근거 |
|---|---|---|---|
| ① | 실물 노드가 그래프에 하나도 안 붙어 있다 (`_pending` 스텁 8개) | `src/graph.py:162-174` | 부록 A |
| ② | `market`만 구버전 계약 — `state["domain"]` KeyError, 관점 dict 반환 | `src/agents/market.py:49-50` | §6 |
| ③ | `synthesis`가 Gap(dict)을 `set()`에 넣어 TypeError | `src/agents/synthesis.py:121-141` | §7 |
| ④ | `report`가 없는 필드를 읽는다 (`domain`, `['text']`, `tech_status`) | `src/agents/report.py:99,132-150` | §9 |
| ⑤ | `review` 노드 진입점이 없다 (worksheet 생성 함수만 있다) | `src/output/review.py` | §8 |
| ⑥ | 검증 결과가 State에 안 실린다 — `validation_errors`는 필드가 아니다 | `src/output/validate.py:278-281` | §7·§8 |
| ⑦ | `publish` 구현이 없다 — `report_paths`를 쓰는 곳이 없다 | `src/output/pdf.py` | §9·부록 A |

⑥이 가장 위험하다. 죽지 않고 **조용히 통과**한다. `final_check`가 오류를 못 보고
`completed`로 끝낸다 — 설계서 §8의 *수정 후에도 무효 인용이 남으면 제출용 출력을 막는다*가
무력화된다.

---

## Stage 1 — 검증 결과를 State에 싣는다 (⑥)

**왜 먼저인가** 조용히 틀리는 버그라 다른 걸 먼저 고치면 계속 가려진다.

| 파일 | 바꿀 것 |
|---|---|
| `src/output/validate.py` | `check()` 반환을 `{"validation": {...}, "trace": [...]}`로 바꾼다. `validation_round` 제거 — 부록 A: *재시도는 노드 내부의 최대 1회 처리다* |
| `tests/test_r5_validate.py` | 반환 키 변경 반영 |

```python
return {"validation": {"errors": errors, "checked_nodes": node_names},
        "trace": [{"node": "validate", "status": "ok", "errors": len(errors)}]}
```

`errors` 한 줄 형식은 `claim_id`를 유지한다 — §8: *검증 실패 시 본문 위치를 찾을 수 있도록
claim_id를 유지한다.* 현재 `_structured_errors`가 이미 넣는지 확인하고 없으면 추가한다.

**검증** `uv run pytest tests/test_r5_validate.py -q`

---

## Stage 2 — `market`을 Assessment로 이행한다 (②)

**설계서 근거** §6: *네 평가 노드는 공통 평가 함수를 사용하되, 검색 함수·허용 자료·
프롬프트를 역할별로 고정한다.* / *Assessment는 claims, evidence, sources, gaps, status를
포함한다.* §5: *발표와 운영 사례를 구분한다. 인접 기술이나 제품의 성과를 선정 기술의
채택으로 옮기지 않는다.*

`research`·`maturity`·`domain`은 이미 이행했다. **그 셋이 쓰는 공통 경로를 그대로 쓴다.**

| 파일 | 바꿀 것 |
|---|---|
| `src/agents/market.py` | `state["domain"]` → `state.get("run_config", {}).get("domain", ...)`. `perspective()` 대신 Claim/Source/Evidence/Gap을 조립해 `Assessment`를 반환. 검색은 `bind_document_search("market")` (컬렉션이 코드에 고정된다) |
| `tests/test_r4_*.py` | market 케이스 추가 |

`market.py:44`의 "ecosystem 근거 0건이면 raise"는 유지한다 — §5의 *자료가 없으면 미확인*
원칙을 지키는 가드다. 다만 raise 대신 `status="failed"` + Gap 반환이 §7의
*구조 오류나 미해결 인용 오류가 남으면 failed*에 더 가까운지는 R4가 판단한다.

**검증** market 노드 단독 호출 시 `Assessment.model_validate` 통과

---

## Stage 3 — `synthesis`를 Assessment 입력으로 바꾸고 gaps를 순차 병합한다 (③)

**설계서 근거** §7: *gaps — 합류 후 수집, **종합 후 순차 병합**.* / *종합 단계는 이미
병합된 근거를 재사용하며 새 자료가 필요하면 그 항목을 공백으로 남긴다.*
§6: *평가 종합 — 관점별 일치·차이·공백을 정리하고 **새로 쓴 주장도 검증한다**.*

| 파일 | 바꿀 것 |
|---|---|
| `src/agents/synthesis.py` | 입력을 `state[perspective]["claims"]`로 바꾼다(`["text"]` 없음). `state.get("validation_errors")` → `state.get("validation", {}).get("errors")`. `state.get("tech_status")` → 각 Assessment의 `status`·`gaps`에서 평가 보류를 만든다. `gaps`를 **반환한다**: `state["gaps"] + 종합이 새로 찾은 공백` |
| | `Synthesis`·`Conflict` 정의를 지우고 `src/schema.py`에서 import |
| `tests/test_r5_*.py` | 입력 형식 변경 반영 |

Gap은 dict이므로 `set()`으로 못 묶는다. `(role, technology, item)` 튜플을 키로 중복 제거한다.

§5의 *결합 효과는 공개된 결합 실험이 없으면 시사점에 가설로만 적는다*는
`combination_hypothesis` 필드가 이미 지킨다 — 유지.

**검증** 합성 Assessment 다섯 개로 `synthesis(state)` 호출 → `gaps`가 합류분을 포함

---

## Stage 4 — `review` 노드를 만든다 (⑤)

**설계서 근거** §8 *형식 검사와 내용 검토* 전체. 특히:
*프로그램은 Claim에 근거 ID가 연결됐는지, ID가 이번 실행에서 확보한 자료인지, 노드별 허용
자료인지 확인한다.* / *이 과정은 ID 대조만으로 자동 통과시키지 않는다. 팀원이 주장과 근거를
나란히 보고 확인하며, claim_id별 판정과 검토자를 기록한다.*

부록 A의 `R[출처 대조와 내용 검토]` 노드가 이것이다. **형식 검사와 내용 검토가 한 노드다.**

| 파일 | 바꿀 것 |
|---|---|
| `src/output/review.py` | `review(state) -> dict` 진입점 추가 |

```python
def review(state) -> dict:
    result = validate.check(state)                  # 형식 검사 (Stage 1)
    path = write_review_csv(state, run_dir(state) / "review.csv")   # 사람용 worksheet
    verdicts = read_verdicts(path)                  # 사람이 채운 판정 (없으면 {})
    validation = result["validation"] | {"claim_verdicts": verdicts,
                                         "reviewer": ...,
                                         "review_csv": str(path)}
    status = "passed" if verdicts and 모든_Claim이_판정됨 else "pending"
    return {"validation": validation, "review_status": status, "trace": [...]}
```

- 사람이 `review.csv`의 `review_result`·`reviewer`를 채우고 `--resume` 하면 그 판정을 읽는다.
  **판정이 비어 있으면 `pending`이다** — 자동 통과시키지 않는다(§8).
- §8: *근거를 확보하지 못한 주장은 평가 보류 항목으로 옮기고 사실 서술에서 제외한다* →
  부결된 Claim은 Gap으로 옮긴다. 이걸 `review`가 할지 `report`가 할지는 Stage 5에서 정한다.

**검증** worksheet 미작성 → `pending` / 전부 판정 → `passed` / 오류 잔존 → `validation.errors`

---

## Stage 5 — `report`를 Claim 기반으로 다시 쓴다 (④)

**설계서 근거** §6: *보고서 생성은 검증된 내용을 정해진 형식으로 조립하는 프로그램이며
별도의 판단 LLM을 호출하지 않는다.* §9 목차표와 REFERENCE 규칙.
§8: *참고문헌은 실제 포함된 Claim의 근거에서 역으로 만든다.*

목차(SUMMARY·1~6·REFERENCE)는 이미 §9대로다. **§4 본문 생성 경로만 바꾼다.**

| 파일 | 바꿀 것 |
|---|---|
| `src/agents/report.py` | `state['domain']` → `run_config["domain"]`. `state[p]['text']` → 각 Assessment의 **승인된 Claim**을 목차 형식으로 조립. `tech_status` → Assessment `status`·`gaps`에서 평가 보류 표기 생성. `sources`/`web_sources` → `source_registry`·`evidence_registry` |
| `src/output/reference.py` | 이미 신형 Assessment 경로가 있다(`_used_source_ids_from_assessment`). `source_registry` 기준으로 부르도록 호출부만 정리. legacy fallback은 남겨도 무해 |

§9 REFERENCE 규칙 확인 항목 — 논문은 저자·연도·제목·게재처 또는 arXiv 버전·**인용 페이지**·
URL, 웹은 기관/작성자·게시일·제목·사이트·URL·조회일. **후보로만 조회한 자료는 제외**한다
(R3의 `status="candidate"`는 Source가 아니므로 자동으로 제외된다).

**검증** 합성 State로 `report(state)` → §9 목차 7개 절이 모두 있고 REFERENCE가 실제 인용
source_id 집합과 일치

---

## Stage 6 — `publish` 노드를 만든다 (⑦)

**설계서 근거** 부록 A `Z[레이아웃 확인 후 제출본 저장]`. §9: *변환 후 SUMMARY 분량, 한글
글꼴, 표·그래프의 잘림과 참고문헌 위치를 확인한다.* 제출 파일명
`RAG-Output_판교_8반_권수진+권예리+김민+박인기+정승원.pdf`.

`src/output/pdf.py`에 이미 `quality_checks()`와 `build_submission()`이 있고 파일명 상수도
§9와 같다. **노드는 얇은 래퍼면 된다.**

| 파일 | 바꿀 것 |
|---|---|
| `src/output/pdf.py` | `publish(state) -> {"report_paths": [...], "trace": [...]}` 추가. Markdown을 `runs/<run_id>/report.md`에 쓰고 `build_submission()`을 부른다. 품질 검사 실패는 `trace`에 남긴다 |

`build_submission`이 `validation_errors`를 받아 차단하는 구조는 유지하되 인자를
`state["validation"]["errors"]`로 넘긴다. weasyprint 미설치 시 Markdown까지만 남기고
`report_paths`에 md 경로만 넣는다 — 현재 동작과 같다.

**검증** 오류 있으면 PDF 미생성 + `report_paths`에 md만 / 통과 시 §9 파일명으로 PDF

---

## Stage 7 — 그래프에 실물을 연결한다 (①)

**마지막이다.** 먼저 붙이면 Stage 1~6이 한꺼번에 터져서 원인 분리가 안 된다.

| 파일 | 바꿀 것 |
|---|---|
| `src/graph.py` | `DEFAULT_NODES`의 `_pending` 8개를 실물로 교체. `_pending`은 함수만 남긴다(다음 역할이 또 쓸 수 있게) |
| `scripts/r1_smoke.py` | mock 경로는 그대로. 실물 통합용 `scripts/e2e_smoke.py`는 LLM·색인이 필요하므로 별도 |
| `docs/interface.md` | ②(관점 dict) 절 삭제 — 마지막 소비자였던 `market`이 Stage 2에서 없어진다 |

**검증**

```bash
uv run pytest -q
uv run python -m scripts.r1_smoke --root /tmp/kv-r1-run     # mock 5경로 유지
uv run python app.py --domain "..."                          # 실물 1회 (LLM 비용 발생)
```

실물 실행은 §6의 *기본 생성 단계는 기술 조사 1회, 관점 평가 4회, 종합 1회*를 확인하고,
`runs/<run_id>/trace.jsonl`의 LLM 호출 수를 센다.

---

## 설계서와 코드가 어긋난 지점 (PDF 기준으로 정함)

| 항목 | 코드 현황 | PDF | 결론 |
|---|---|---|---|
| 재시도 횟수 State 필드 | `validate.py`가 `validation_round` 기록 | 부록 A: *재시도는 노드 내부의 최대 1회 처리* | 필드 없앤다 (Stage 1) |
| `tech_status` | `synthesis`·`report`가 읽음 | §7 표에 없다. §5: *자료가 없는 항목은 평가 보류로 남긴다* | Assessment `status`·`gaps`로 대체 (Stage 3·5) |
| 관점 dict `{text, citations, gaps}` | `market`만 사용 | §6: Assessment = claims/evidence/sources/gaps/status | 삭제 (Stage 2·7) |
| 형식 검사와 내용 검토 노드 분리 | `validate`·`review` 별도 함수 | 부록 A: `R[출처 대조와 내용 검토]` 한 노드 | `review`가 `validate`를 부른다 (Stage 4) |
| REFERENCE 생성 | `reference.py`에 신형·구형 두 경로 | §8: *실제 포함된 Claim의 근거에서 역으로* | 신형 경로만 쓴다 (Stage 5) |

---

## 소유와 진행

| Stage | 파일 소유 | 비고 |
|---|---|---|
| 1 | R5 | 계약 위반이 명백하고 수정이 작다 |
| 2 | R4 | market만 남은 이행 |
| 3 | R5 | 죽는 버그(TypeError) |
| 4 | R5 | 새 노드 |
| 5 | R5 | 보고서 본문 구성이 걸려 설계 판단이 필요하다 |
| 6 | R5 | 기존 함수 래핑 |
| 7 | R1 | 배선 + 계약 문서 정리 |

Stage 1·3은 죽거나 조용히 틀리는 버그라 먼저 간다. Stage 5는 §9 목차 해석이 들어가므로
R5와 같이 본다.


---

## 실제로 나온 것 (계획과 다른 부분)

계획은 "market만 구버전"이라고 봤지만, **테스트를 붙이자 드러난 게 더 많았다.**
R4 테스트가 하나도 없었고 R5 테스트는 구버전 State 를 손으로 만들어 넣고 있었다.

| Stage | 계획 | 실제 |
|---|---|---|
| 2 | market 하나 이행 | research·maturity·domain 도 Assessment 조립에서 ValidationError 로 죽고 있었다. `technology="both"`, 빈 `evidence_ids`, 가설에 explanation 없음, `allowed_uses=["domain_assessment"]`, maturity 가 쓰지도 않는 LLM 을 만들어 API 키를 요구 |
| 3 | 입력 형식 교체 | `schema.Synthesis` 의 `conflicts: min_length=1` 과 `Conflict.favors` 가 설계서 §5 "상충하는 의견을 억지로 만들지는 않는다"와 어긋나 제거 |
| 5 | §4 본문만 교체 | §9 목차표에 맞춰 근거 공백을 §5.3 → §6 으로 옮기고, §10 이 요구하는 조사 시점·검토 범위를 §6 에 추가 |
| 7 | 배선만 | `collect_evidence` 가 정상 실행에서 병합 오류를 냈다. 같은 청크를 여러 노드가 인용하면 `allowed_uses` 만 달라지는데 이걸 내용 불일치로 봤다. 허용 용도는 원문 속성이 아니라 사용 권한이라 합집합으로 합친다 |

## 남은 것

- 실물 LLM·색인으로 한 번도 돌리지 않았다. `uv run python app.py` 는 R2 코퍼스와
  API 키가 있어야 한다. §6 의 "기본 생성 단계는 기술 조사 1회, 관점 평가 4회, 종합 1회"
  확인은 그때 한다.
- `stakeholder` 는 통합 테스트에서 stub 이다. 네트워크·실행별 저장소를 쓰므로
  `scripts/r3_smoke.py` 가 따로 검증한다.
- 품질 점검(`quality_checks`)은 Markdown 문자열만 본다. §9 의 "표·그래프의 잘림"은
  실제 PDF 렌더 결과를 봐야 알 수 있다.
