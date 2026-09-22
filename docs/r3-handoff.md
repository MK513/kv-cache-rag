# R3 구현 및 인수 기록

기준: 사용자 첨부 최종 설계서(2026-09-22), 사용자 지정 R3 역할.
기준 main: `791af1b`. 브랜치: `feat/r3-retrieval-web-stakeholder`.

## 구현 범위

| 항목 | 구현 위치 | 확인 방법 |
|---|---|---|
| Dense 코사인·기술/컬렉션/관점 필터 | src/rag/retrieve.py | 실제 FAISS에서 코사인 0.6, 잘못된 컬렉션/메타데이터 차단 |
| 역할을 Python에서 고정한 검색 도구 | src/tools/docs.py | collection/perspective가 모델 입력에 없고 추가 인자 거부 |
| 검색 후보와 본문 근거 분리 | src/tools/web_search.py | 후보만으로 Source/Evidence 생성 불가 |
| HTTP·HTML/PDF 본문 추출 | src/tools/web_fetch.py | 실패/빈 본문/오류 페이지/사설 주소 차단, redirect 최종 URL 보존 |
| 실행별 저장소·한도·해시 | src/tools/web_store.py | 재개 후 예산 유지, 실행 간 격리, 저장 파일 변조 차단 |
| 이해관계자 네 주체×두 기술 | src/agents/stakeholder.py | Claim별 근거 연결, kind·발언 주체·시점·맥락, Gap·실패 반환 |
| 모델 응답 형식 검사 | src/agents/stakeholder_contract.py | 필수 필드, 추론 설명, 근거 ID 검사 및 수정 최대 1회 |
| 재현 실행 | scripts/r3_smoke.py | API 없는 합성 데이터 통합 실행 |

**변경 계약 전체는 [interface.md](interface.md#r3-최신-설계서-적용-계약-2026-09-22)를 따른다.**
R1 공유 스키마가 아직 없는 상태에서 R3가 State/schema.py를 덮어쓰지 않았다.
LLM draft 모델은 공용 Assessment가 아니며 R1 모델 합류 후 wire dict 호환 확인이 필요하다.

## 동작과 상태

- 검색은 성공했지만 필요한 자료를 찾지 못하면 Gap으로 남기고 `partial`.
- 검색 제공자가 전체 호출에서 실패해 수집 자체를 수행하지 못하면 `failed`.
- 본문이 없는 항목은 보완 검색한다. 본문이 있어도 초안에서 필요한 근거가 없다고 판단한
  기술×주체 항목을 추가 조사한다. 같은 항목은 보완 검색을 한 번만 한다.
- 최대 검색 16회(초기 8 + 항목별 보완 8). 입력 예산을 초과한 원문은 완전한 인용 레코드
  단위로 모델 입력에서 제외하며, 제외된 근거는 그 초안에서 인용할 수 없다.
- 생성은 최초 초안 1회, 근거 보완 후 재작성 최대 1회, 형식/인용 수정은 전체에서 최대 1회다.
  따라서 LLM 호출은 최대 3회다. 의미 일치 여부는 사람의 검토가 필요하다.
- `completed`도 R3 구조 검사 완료일 뿐이다. 내용 검토는 `pending`으로 기록한다.
- 수정 후에도 무효 인용/형식 오류가 남으면 claims를 비우고 `failed`를 반환한다.

## 검증

```bash
uv sync --extra test
uv run pytest -q
uv run python -m scripts.r3_smoke --root /tmp/kv-r3-new-run
```

1. `pytest`: 초기 저장소에는 테스트가 없었다. R3 테스트는 결함/누락을 먼저 재현하고 구현했다.
2. 실제 소형 FAISS와 고정 벡터를 사용했다. E5 모델을 실행한 검색 품질 지표가 아니다.
3. HTML 추출·Pydantic 검사·SHA-256·스냅샷/manifest 기록은 실제 코드다.
4. 자동 테스트와 smoke의 HTTP/검색/LLM 입력은 합성 fixture다. 실제 기술 평가의 증빙으로 쓰지 않는다.
5. 실제 공개 HTTP 확인: Google Research의 TurboQuant 소개 페이지를 수집해 **본문 11,373자,
   근거 조각 50개**를 저장했다. 원문은 `runs/r3-live-fetch-check/web/`에 있고 Git에서는 제외된다.
   이 결과는 수집기 동작 확인이며 기술 주장이나 검색 품질에 대한 검증 결과가 아니다.
6. Tavily 및 LLM의 유료 실제 호출, R2 원문 ingest/E5 검색 평가, R1 전체 그래프,
   R5 PDF 출력·내용 검토는 이번 R3 검증 범위에 포함하지 않았다.
7. langchain-community 기존 FAISS 래퍼의 패키지 종료 안내 DeprecationWarning 1건이 있다.
   동작 오류는 아니며 새 래퍼로의 이전은 별도 의존성 변경이다.

실행 시점의 정확한 테스트 수/결과는 [pytest 결과](evidence/r3/pytest.txt),
[차단·정상 경로 로그](evidence/r3/checks.json),
[웹 스냅샷 매니페스트](evidence/r3/runs/r3-smoke/web/manifest.json)를 참고한다.
증빙의 경로는 저장소 루트 기준 상대 경로로 치환했다. JSON/HTML 내용은 전부 합성 자료다.

## 도구 I/O 예시

- [논문 검색 출력](evidence/r3/document-tool-output.json)
- [웹 검색 후보 출력](evidence/r3/search-tool-output.json)
- [본문 수집 출력](evidence/r3/fetch-tool-output.json)
- [정상 Claim/Gap 연결 결과](evidence/r3/valid-assessment-output.json)
- [구조 오류 실패 결과](evidence/r3/failed-assessment-output.json)

## 기존 코드와의 차이 및 인수

| 역할 | R3가 제공한 것 | 팀 통합 시 필요한 작업 |
|---|---|---|
| R1 | stakeholder/trace 두 키 반환, 실행 폴더, 실패·partial 상태 | run_id/run_config 초기화, 공유 schema, collect_evidence, failed 라우팅 |
| R2 | FAISS dense 기존 store 계약과 source 별칭 호환 | 최신 38/48.3/35쪽 코퍼스와 파서·revision·goldenset 적용 |
| R4 | bind_document_search(role, collection=None) | 공통 함수에 역할별 도구 주입, source_id/cosine_score 사용 |
| R5 | Claim↔Evidence↔Source 연결, 검토용 원문 위치와 스냅샷 | 웹 주장도 검증, 원문 의미 검토, Claim 기반 참고문헌, 실패 시 제출 차단 |

`load_paper`는 R2 담당이므로 구현하지 않았다. 세 검색·수집 도구가 R3 완료 대상이다.
README 기존 전체 파이프라인 설명 및 구버전 State/validator/report는 다른 역할의 변경을 기다린다.
이 브랜치를 단독 적용하고 기존 app.py가 최신 설계 전체를 실행한다고 간주하면 안 된다.

## 리뷰에서 확인하고 수정한 사항

- 빈 객체 `{}`를 LLM이 반환하면 정상 평가 보류로 넘어가던 문제: claims/gaps를 필수 필드로 변경.
- 전체 검색 서비스 장애를 자료 부재로 처리하던 문제: 수집을 수행하지 못한 경우 failed로 분리.
- 페이지를 가져오기만 하면 보완 검색을 건너뛰던 문제: 초안의 근거 공백에서도 한 번 보완.
- 입력이 길 때 모든 근거를 모델에 전달하던 문제: 전체 입력 문자 상한, 제외 근거 인용 차단.
- HTTP 200을 반환하는 접근 검증 페이지를 본문으로 읽던 문제: 대표 오류/검증 제목을 차단.

## 운영상 제한

한 run_id에는 한 프로세스/한 저장소 소유자를 사용한다. 동시 다중 프로세스 쓰기 잠금은 제공하지 않는다.
정적 HTML/text/PDF 수집이며 동적 렌더링/로그인 문서는 실패할 수 있다. URL·메타데이터와 원문을
확보해도 실제 발언 주체·시점·주장의 의미 일치가 자동으로 증명되는 것은 아니다.
색인 구조가 R2에서 바뀌면 interface.md의 `build()` 계약에 맞춘 어댑터가 필요하다.
