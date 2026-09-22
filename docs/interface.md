# 팀 인터페이스 계약

> **2026-09-22 R3 변경:** 아래 「R3 최신 설계서 적용 계약」이 R3 경로에서는 우선한다.
> 기존 ①~⑥은 main의 구버전 계약을 설명하는 기록이다. R1/R4/R5 전체가 새 계약으로
> 이행했다고 뜻하지 않는다. 사용자가 최신 PDF 기준 구현과 필요한 계약 변경 기록을 요청했다.

담당자끼리 **먼저 고정해야 하는 것만** 모았다. 여기 정의된 형식은 소유자 승인 없이 바꾸지 않는다.
구현 세부는 각 파일의 docstring, 코퍼스 수집 절차는 [corpus-prep.md](corpus-prep.md) 참고.

| 계약 | 소유 | 소비 |
|---|---|---|
| ① State (§7 14행 / 17필드) + `src/schema.py` | R1 | 전원 |
| ② ~~관점 dict `{text, citations, gaps}`~~ → `schema.Assessment` | R1 · R5 | R3 · R4 |
| ③ chunk 스키마 + 검색 도구 시그니처 | R2 | R3 · R4 |
| ④ `validation` · `review_status` | R5 | R1 (라우팅) |
| ⑤ `sources.json` 의 `scope` · `perspectives` 값 | R2 (선별 R3) | R3 · R4 |

①②③만 정해지면 **R3~R5는 인덱스 완성 전에도 목업 청크로 개발을 시작할 수 있다** (§6).

---

## ① State + 공용 schema — `src/state.py` · `src/schema.py` (R1)

> **2026-09-22 R1 (브랜치 `feat/r1-graph-state-schema`).** 구버전 17키를 교체한다.
> 아래 표는 설계서 §7 표를 그대로 옮긴 것이다. §7 표는 **14행**이고 그중 세 행이
> 묶음 표기(`run_id / run_config`, `validation / review_status`, `report / report_paths`)라
> Python 필드로는 **17개**다. 묶음을 필드로 나누는 판단은 R1 소유다(R3 계약 문서).

`TypedDict(total=False)`. **병렬 구간에서는 각 평가 노드가 자기 결과 키만 쓴다.** 웹 자료는
전역에 쌓지 않고 각 Assessment에 넣는다. 누적 reducer는 `trace` 하나뿐이다.

| §7 행 | 필드 | 형식 | 쓰기 주체와 용도 |
|---|---|---|---|
| 1 | `run_id` | `str` | 초기화. 실행 식별자 |
| 1 | `run_config` | `dict` | 초기화(`app.py`) + `setup`이 자료 버전을 채운다 |
| 2 | `research` | Assessment | `research` |
| 3 | `maturity` | Assessment | `maturity` |
| 4 | `market` | Assessment | `market` |
| 5 | `stakeholder` | Assessment | `stakeholder` |
| 6 | `domain_assessment` | Assessment | `domain_assessment` |
| 7 | `source_registry` | `dict[str, Source]` | **`collect_evidence`만** |
| 8 | `evidence_registry` | `dict[str, Evidence]` | **`collect_evidence`만** |
| 9 | `gaps` | `list[Gap]` | `collect_evidence` 합류 후 수집 → `synthesis` 종합 후 순차 병합 |
| 10 | `synthesis` | Synthesis | `synthesis` |
| 11 | `validation` | `dict` | 검증 단계(`review`). 오류·내용 검토 결과 |
| 11 | `review_status` | `str` | 검증 단계(`review`). `pending`이면 검토용 초안만 저장 |
| 12 | `run_status` | `str` | 제어 단계(`final_check` · `app.py`) |
| 13 | `report` | `str` | 출력 단계(`report`). Markdown 본문 |
| 13 | `report_paths` | `list[str]` | 출력 단계(`publish`). Markdown·PDF 저장 경로 |
| 14 | `trace` | `Annotated[list[dict], operator.add]` | 전 노드 (누적) |

```python
run_config = {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"],
              "model": {...}, "limits": {...}, "runs_dir": "runs",
              "web_top_k": 3, "web_context_chars": 40000, "web": {...},
              "sources": [...]}      # setup 이 적재한 자료 버전(매니페스트)

validation = {"errors": [...], "reviewer": "...", "claim_verdicts": {...}}
review_status = "pending" | "passed"
```

**단일 쓰기 주체 원칙** — 병렬 노드는 공용 `gaps`나 레지스트리를 직접 수정하지 않는다.
자기 Assessment 안에만 Source·Evidence·Gap을 담고, 병합은 `collect_evidence`가 한다.
`gaps`만 순차 2단계(합류 → 종합)로 쓰며, 순서가 정해져 있어 reducer가 필요 없다.

**재시도 횟수는 State 필드가 아니다.** 설계서 부록 A: *재시도는 노드 내부의 최대 1회
처리다.* 근거 보완도 재작성도 노드 안에서 끝낸다.

### 공용 모델 — `src/schema.py`

`Claim` / `Assessment` / `Source` / `Evidence` / `Gap` / `Synthesis` / `Conflict` / `Event`.
필드는 설계서 §6·§7과 R3 실물 출력(`src/tools/web_store.py`, `src/agents/stakeholder.py`)에서 맞췄다.

- **검증은 dict 경계에서 한 번만.** `collect_evidence`가 `Assessment.model_validate`를
  부르고, 그 뒤로는 State를 dict로 흐른다. 노드마다 검증하지 않는다.
- `Assessment` = `claims` / `sources` / `evidence` / `gaps` / `status`(§6). **인용 사슬**을
  본다 — claim → evidence → source가 자기 안에서 닫히는지, `status=failed`인데 claims가
  남았는지. 병합이 믿을 근거는 이것뿐이다.
- `Source`·`Evidence`는 `extra="allow"`. 웹 출처(R3)와 색인 출처(R2)는 필드가 다르다.
  필수는 `source_id` · `run_id` · `collection` · `allowed_uses` 넷.
- `Claim`·`Gap`·`Assessment`는 `extra="forbid"`. 오타난 필드가 조용히 통과하면 안 된다.
- `Claim`의 `stakeholder_group`·`actor`·`context`는 **선택**이다. R3의 `ClaimDraft`는 이를
  필수로 검사하지만, R4의 TRL·시장성 Claim에는 발언 주체가 없다. 이해관계자 Claim의
  엄격한 검사는 R3의 draft 단계가 계속 맡는다.
- `Gap.item`은 자유 문자열이다. 이해관계자는 `competitors` 같은 4값이지만 maturity는
  "TRL 6 실증 환경 근거" 식이라 Literal로 묶을 수 없다.
- `Role` = `research` `maturity` `market` `stakeholder` `domain` `synthesis`. State 필드명은
  `domain_assessment`지만 역할 이름은 R2의 `perspectives`와 맞춰 `domain`이다.
  `synthesis`는 종합 단계가 남기는 Gap 때문에 있다(§7 *종합 후 순차 병합*).
- **R5 조치:** `src/agents/synthesis.py`의 `Synthesis`·`Conflict` 정의를 지우고
  `src/schema.py`에서 import한다. 같은 정의를 두 벌 두지 않는다.

### trace 한 줄 형식 — `schema.Event`

```python
{"node": "maturity", "status": "ok", "attempt": 1, "timestamp": "...", "chunks": 14}
{"tool": "search_market_signals", "node": "stakeholder", "status": "ok", ...}
```

`node` 또는 `tool` 중 하나는 반드시 넣는다. 나머지 필드는 자유(`extra="allow"`).
이벤트에 노드·시도 번호·시간을 넣는다(§7).

---

## ② 관점 dict — `src/agents/common.py` (R1 · R5) — **구버전**

> `schema.Assessment`가 대체한다(①). 평가 노드는 `{text, citations, gaps}`가 아니라
> Assessment를 반환한다. 아래는 구버전 `report`·`validate` 경로를 읽기 위한 기록이다.

다섯 평가 노드가 **모두 같은 모양**으로 반환한다.

```python
{
  "text": str,              # 본문 마크다운
  "citations": list[str],   # 실재하는 인용 ID (또는 url — stakeholder)
  "gaps": list[str],        # 근거 공백
  "bad_citations": list[str],  # 존재하지 않는 인용 ID — validate 가 읽는다
}
```

`common.perspective(text, valid_ids)` 가 이 dict를 만들어 준다. 직접 조립하지 말 것.

### 인용 ID 형식

본문에 **`[` + 12자리 16진수 + `]`** 로 박는다. `common.extract_citations` 가
정규식 `\[([0-9a-f]{12})\]` 로 뽑아 인덱스 실물과 대조한다.

```
TurboQuant 는 ... 로 보고한다 [a1b2c3d4e5f6].
```

`chunk_id = sha1(f"{source}|{page}|{text}")[:12]` — 재실행해도 값이 변하지 않는다.
따라서 **청킹 파라미터를 바꾸면 인용 ID가 전부 달라진다.** 평가셋 라벨링은 청킹 확정 후에.

### 근거 공백 표기

LLM 출력의 마지막 줄을 `common.extract_gaps` 가 파싱한다. 프롬프트가 이 형식을 강제한다.

```
근거 공백: TurboQuant 처리량 미확인 | 결합 실측 자료 없음
근거 공백: 없음
```

---

## ③ chunk 스키마 + 검색 도구 — `src/tools/docs.py`, `src/tools/web_search.py` (R2 · R3)

### `search_source_documents` (R2) — 색인 검색

```python
search_source_documents.invoke({
    "query": str,             # 한국어 가능. BM25 축만 영어 키워드로 자동 변환
    "collection": str,        # papers_core | ecosystem | context   (필수)
    "technology": str,        # TurboQuant | ITME | both            (기본 both)
    "top_k": int,             # 기본 5
    "perspective": str,       # research | maturity | market | domain | stakeholder (기본 "")
}) -> list[chunk]
```

반환 chunk:

```python
{
  "chunk_id": "a1b2c3d4e5f6",
  "collection": "papers_core",
  "source": "itme-paper",          # sources.json 의 id
  "applies_to": ["ITME"],          # 목록이다. 한 문서가 두 기술에 걸릴 수 있음
  "scope": "direct",
  "page": 7,                       # PDF 실제 페이지 (웹 문서는 1)
  "text": "...",
}
```

- **`collection` 은 필수다.** 시장 근거를 `papers_core` 에서 찾으면 의미적으로 가까운
  기술 서술이 회수돼 시장 주장의 근거 자리에 놓인다.
- `perspective` 를 주면 `sources.json` 의 `perspectives` 로 문서를 거른다.
  비교군 논문이 기술 조사의 1차 근거로 올라오는 것을 막는다.
- 필터가 랭킹 **뒤**에 걸리므로, 필터가 있으면 내부 검색 풀을 4배로 키운다.

### `summarize_evidence` (R2)

```python
summarize_evidence.invoke({"chunk_ids": list[str]}) -> {"summary": str, "citations": list[str]}
```

### `search_market_signals` (R3) — 웹 조사, **이해관계자 전용**

색인하지 않으므로 200쪽 가드 계산 대상이 아니다. 질의 해시로 스냅샷을 캐시해 커밋한다.

```python
search_market_signals.invoke({
    "query": str, "technology": str, "top_k": int,
}) -> list[{
  "url": str, "title": str,
  "published_at": str,     # 없으면 "미확인"
  "retrieved_at": str,     # YYYY-MM-DD
  "snippet": str,
  "source_type": str,      # 논문 | 표준 | 제품발표 | 리포트 | 개발자논의 | 뉴스 | 미확인
}]
```

---

## ④ `validation` · `review_status` — 내용 검토 (R5 → R1)

> **2026-09-22 R1.** 구버전 `validation_errors` · `validation_round` · `validate_eval` ·
> `validate_final` · `abort`를 대체한다. 라우팅이 이 값을 보므로 형식은 R5·R1이 함께 정한다.

R5가 쓰고 `final_check`가 읽는다. 설계서 §8: *형식 검사와 내용 검토*.

```python
validation = {
  "errors": [{"node": "maturity", "claim_id": "claim-...", "kind": "없는 인용 ID"}],
  "reviewer": "권예리",                       # claim_id별 판정과 검토자를 기록한다(§8)
  "claim_verdicts": {"claim-...": "확인"},
}
review_status = "pending"   # 내용 검토가 끝나지 않았다 → 검토용 초안만 저장
```

프로그램이 보는 것(§8) — Claim에 근거 ID가 연결됐는지, ID가 **이번 실행에서 확보한**
자료인지, 노드별 허용 자료(컬렉션 및 웹 출처 범위)인지. 검증 실패 시 본문 위치를 찾을 수
있도록 `claim_id`를 유지한다.

사람이 보는 것(§8) — 원문 구절이 주장을 뒷받침하는지. 성능 수치의 단위·비교 기준선·실험
조건, TRL 단계의 근거, 직접 채택과 인접 생태계 자료의 구분. **ID 대조만으로 자동
통과시키지 않는다.**

### `final_check`가 판정하는 것 (부록 A)

| 조건 | run_status | 다음 |
|---|---|---|
| Assessment 중 `failed`, 또는 `validation.errors` 잔존 | `failed` | `fail` — 오류·로그 저장, 제출용 출력 차단 |
| `review_status == "pending"` | `partial` | `save_draft` — 검토용 초안만 저장, 실행 정지 |
| Assessment 중 `partial` | `partial` | `report` — 보고서는 낸다 |
| 그 외 | `completed` | `report` → `publish` |

재시도는 노드 내부의 최대 1회 처리이므로 그래프에 되돌아오는 엣지는
`save_draft → review`(검토 결과 반영 후 재개) 하나뿐이다.

---

## ⑤ `sources.json` 의 분류 값 (R2, 자료 선별 R3)

| 필드 | 값 | 의미 |
|---|---|---|
| `collection` | `papers_core` \| `ecosystem` \| `context` | 색인 분리 단위 |
| `scope` | `direct` | 선정 기술의 1차 근거 |
| | `comparison` | 계열 비교군. 선정 기술의 성능 근거로 대체하지 않는다 |
| | `ecosystem` | 인접 생태계. **직접 채택 근거가 아니다** |
| | `secondary` | 2차 자료(서베이). 성능 주장 근거로 인용하지 않는다 |
| `perspectives` | `research` `maturity` `market` `domain` `stakeholder` | 이 문서를 볼 수 있는 노드 |
| `applies_to` | `["TurboQuant"]` `["ITME"]` `[둘 다]` | **목록이다** |
| `excerpt_only` | `true` | 전문을 색인하지 않는다 → `excerpt_pages: [시작, 끝]` 필수 |

`excerpt_pages` 가 없으면 전문이 색인되고 `scripts/ingest.py` 가 경고한다.

---

## ⑥ 목업으로 먼저 개발하기

인덱스가 없어도 ③ 형식만 지키면 노드를 짤 수 있다.

```python
# R3 · R4 가 자기 노드를 개발할 때
MOCK = [{
    "chunk_id": "a1b2c3d4e5f6", "collection": "papers_core", "source": "itme-paper",
    "applies_to": ["ITME"], "scope": "direct", "page": 7,
    "text": "ITME reports 1.80x throughput over an NVMe-oF baseline ...",
}]
```

R5는 ② 형식의 더미 관점 dict로 `synthesis` · `report` 를 먼저 완성한다.
그 더미가 곧 R3·R4의 출력 스펙이 된다.

---

## 변경 절차

1. 이 문서를 먼저 고친다.
2. 소유자(위 표)의 승인을 받는다.
3. 코드를 고친다.

**청킹 파라미터**(`config/settings.yaml` 의 `chunk_tokens` · `chunk_overlap`)를 바꾸면
모든 `chunk_id` 가 달라져 **평가셋 라벨링을 다시 해야 한다.** 확정 후 동결한다.

---

## R3 최신 설계서 적용 계약 (2026-09-22)

기준: `RAG-Design_판교-8반_권수진+권예리+김민+박인기+정승원.pdf` 3·4·6·7·8절,
사용자가 지정한 R3 역할. 기준 커밋 `791af1b`. 브랜치 `feat/r3-retrieval-web-stakeholder`.
시장성은 **ecosystem RAG**를 유지한다. R3는 sources.json 분량이나 R1 State를 대신 확정하지 않는다.

### 변경 이유 및 영향

| 구버전 | R3 변경 | 소비자 조치 |
|---|---|---|
| RRF 기본, 번역 LLM 호출 | Dense FAISS 코사인만 사용 | R2 eval은 mode="dense". RRF는 평가 근거 후 별도 도입 |
| 필터 후 상위 후보 부족 가능 | 전체 컬렉션 후보에서 허용 기술·관점 필터 | R2는 applies_to/perspectives/scope 메타데이터 필수 제공 |
| source, 점수 없음 | source_id, cosine_score 추가, source 별칭 유지 | R4 기존 키 호환, 새 구현은 source_id 사용 |
| LLM에게 collection/perspective 노출 가능 | bind_document_search로 역할을 코드에서 고정 | R4는 역할별 바인딩 도구 사용 |
| 검색 snippet을 사실 근거로 전달 | 본문 수집 성공 자료만 전달 | R5는 검색 후보를 REFERENCE에 넣지 않음 |
| data/web_cache 전역 공유 | runs/<run_id>/web/ 스냅샷·manifest·events | R1이 run_id와 실행 저장 루트를 전달 |
| URL 목록을 모두 citations로 지정 | Claim별 evidence_ids 연결 및 형식 검증 | R5가 원문과 의미 검토, pending 자동 승인 금지 |
| stakeholder + web_sources 쓰기 | stakeholder + trace만 쓰기 | R1 collect_evidence가 Assessment.sources/evidence 병합 |

### 검색 계약

`search_source_documents.invoke({query, technology="both", collection, top_k=5, perspective=""})`
호출은 유지한다. Python 함수 인자 순서는 기존대로 query, collection, technology이며
호출자는 이름 있는 인자를 사용한다. 빈 질의, 미지원 컬렉션/기술, top_k<=0은 오류다.
`perspective`가 있으면 역할의 컬렉션 허용표와 원문 메타데이터를 모두 확인한다.
구버전 raw 도구는 신뢰하는 Python 코드 전용이며 LLM에 직접 연결하지 않는다.

R4 연결: `bind_document_search("market")`가 반환하는 도구의 입력은 query,
technology, top_k뿐이다. research/maturity는 papers_core, market은 ecosystem,
domain은 papers_core/context를 사용한다. context는 scope=comparison/secondary만 허용한다.
research는 direct만, maturity는 direct/comparison, market은 direct/ecosystem만 허용한다.
반환 뒤에도 범위를 다시 검사하며 잘못된 청크가 섞이면 전체 호출을 실패시킨다.

반환: chunk_id, source_id, source(호환 별칭), collection, page, text, cosine_score,
applies_to, perspectives, scope. score는 정규화 벡터의 코사인 값이며 RRF 점수가 아니다.
R2가 공급하는 `build()` 결과는 `stores[collection]["dense"]`(LangChain FAISS),
`chunks`(chunk_id→Document)를 유지한다. 정규화된 벡터와 질의를 요구한다.

### 웹 계약

`WebEvidenceStore(run_id, root="runs", ...)`는 실행별 수집·예산·저장을 소유한다.
`bind_web_tools(store)`가 검색/수집 두 도구를 반환한다. LLM 입력에는 run_id/root를 노출하지 않는다.
기존 전역 도구도 `web_run(store)` 컨텍스트 안에서 `.invoke()`할 수 있다.
컨텍스트 없이 호출하면 실패하며 다른 실행의 캐시로 대체하지 않는다.

- search_market_signals(query, technology="both", top_k=5): URL·제목·게시일 후보·snippet·조회일·source_type.
  `status="candidate"`. 검색 결과의 본문처럼 보이는 content도 사실 근거가 아니다.
- fetch_web_evidence(url): 접속 status, 요청/최종 URL, 기관·제목·게시일·조회일,
  본문, 인용 구절, 위치, raw/body 저장 경로 및 SHA-256, source/evidence를 반환한다.
  실패는 status="failed", error를 반환하고 source/evidence를 만들지 않는다.
  날짜 미확인은 null이며 조회일로 대체하지 않는다. 리다이렉트와 본문 한도를 검사한다.
- 저장: web/manifest.json, web/events.jsonl, web/search/*.json, web/snapshots/*.
  운영 스냅샷은 Git에 넣지 않는다. 증빙은 별도의 합성 데이터 실행으로 만든다.

### 이해관계자 출력 / R1·R5 연결

`stakeholder(state)`는 state.run_id를 필수로 요구한다. run_config.web에서 수집 한도와
run_config.runs_dir에서 저장 루트를 읽는다. **기존 app.py에는 run_id가 없어 그대로 실행할 수 없다.**
R1이 새 State/초기화/collect_evidence를 구현할 때 연결한다. 그래프를 구버전으로 몰래 우회하지 않는다.
개별 검증은 scripts/r3_smoke.py로 수행한다.

Assessment dict: claims, sources, evidence, gaps, status(completed/partial/failed).
각 Claim: claim_id, text, technology, kind(fact/inference/hypothesis), evidence_ids,
explanation, stakeholder_group, actor, statement_date, context. 날짜 미상은 null.
Source/Evidence에는 run_id, collection="web", allowed_uses=["stakeholder"]를 포함한다.
Evidence는 evidence_id, source_id, quote, location, snapshot_path, sha256로 원문에 연결한다.
Gap은 role, technology, item, reason을 포함한다. trace는 Event dict 목록이며
node/tool, attempt, timestamp, status를 기록한다. 공용 gaps/registry를 직접 쓰지 않는다.

R1의 `src/schema.py`가 아직 없으므로 R3의 LLM 응답 검증용 모델만 별도 파일에 둔다.
공용 Claim/Assessment를 중복 정의하지 않으며 이 문서의 wire dict를 R1 공용 모델에 연결한다.
R5는 status=failed를 통과시키면 안 된다. status=completed는 내용 검토 완료가 아닌
R3의 구조·출처 연결 검사 완료이며 사람의 내용 검토는 여전히 필요하다.

### 기본 한도와 저장 파일

| 설정 | 기본값 | 의미 |
|---|---:|---|
| run_config.web_top_k | 3 | 기술×주체별 검색 후보 수 |
| run_config.web_context_chars | 40,000 | 모델에 전달할 근거 XML 총 문자 수. 레코드 단위 제외, 제외 수 trace 기록 |
| web.max_sources | 24 | 성공한 원문 수 |
| web.max_fetches | 40 | 본문 수집 시도 수, 실패도 집계 |
| web.max_searches | 16 | 두 기술×네 주체 최초 8회 + 미확보 항목 보완 최대 8회 |
| web.max_body_chars | 50,000 | 원문 한 건의 추출 본문 길이 |
| web.max_total_chars | 300,000 | 실행 전체 본문 문자 수 |
| web.max_response_bytes | 2,000,000 | HTTP 한 응답의 디코딩 후 바이트 상한 |
| web.min_body_chars | 120 | 빈 문서·짧은 오류 페이지 차단 기준 |
| web.timeout | 15초 | HTTP 연결/각 read timeout, 전체 실행 시간 한도와 다름 |

예산은 manifest에 저장하며 같은 run_id를 재개할 때 다른 예산으로 바꾸지 않는다.
R1은 한 run_id에 한 프로세스/한 Store 소유자를 연결한다. 객체 내부 스레드 호출은 잠금으로
직렬화하지만 여러 프로세스가 같은 실행 폴더에 동시 쓰는 것은 지원하지 않는다.
HTTP 오류·접근 검증 페이지·본문 부족·비지원 MIME·크기 초과·해시 불일치는 실패한다.
정적 HTML/text/PDF를 지원한다. JS 렌더링/로그인/유료벽은 우회하지 않는다.
metadata 날짜는 원문 값을 보존하고 일반 time 태그는 발언 시점이 아닌 출처 메타데이터로만 다룬다.

stakeholder.json은 해당 노드의 최신 Assessment, stakeholder-validation.json은 구조 오류와
review_status=pending, stakeholder-trace.jsonl은 노드 실행 누적 기록이다. R1은 반환된 trace를
전체 실행 trace에 병합한다. 웹 tool 이벤트는 web/events.jsonl에도 남는다.

보완 검색은 본문 근거가 없는 기술×주체 항목뿐 아니라, 초안에서 필요한 근거가 없다고
판단한 항목에도 수행한다. 같은 항목에는 한 번만 수행하며 전체 검색은 최대 16회다. 내용은 R5가 검토하므로,
본문이 있다는 사실만으로 직접 반응이나 실제 채택이 확인됐다고 간주하지 않는다.
LLM 응답은 claims/gaps를 명시적으로 포함해야 한다. 빈 객체는 구조 오류다.
모든 검색 호출이 실패하면 자료 부재 partial이 아니라 실행 실패 failed로 남긴다.
근거 보완 후 초안 재생성은 최대 1회, LLM 응답 오류/무효 인용 수정은 두 초안 전체에서 최대 1회다.
따라서 모델 호출 상한은 총 3회다. 여전히 실패하면 claims=[]와 status=failed를 반환하고
오류는 stakeholder-validation.json과 trace에 남긴다. 모델 입력에서 제외된 evidence_id도 인용할 수 없다.

### R4 바인딩 예시

```python
from src.tools.docs import bind_document_search
research_search = bind_document_search("research")
market_search = bind_document_search("market")
domain_context_search = bind_document_search("domain", "context")
hits = market_search.invoke({"query": "실제 채택 사례", "technology": "ITME", "top_k": 5})
```

### R1 / R5 연결 예시

```python
from src.agents.stakeholder import stakeholder
update = stakeholder({
    "run_id": "team-run-001",
    "run_config": {
        "domain": "데이터센터/클라우드",
        "runs_dir": "runs",
        "web_top_k": 3,
        "web_context_chars": 40000,
        "web": {"max_sources": 24, "max_searches": 16},
    },
})
assessment = update["stakeholder"]
# R1: 공용 Assessment.model_validate(assessment)와 실제 공용 모델 필드를 맞춰 연결.
# R1: assessment의 sources/evidence만 collect_evidence에 넘긴다.
# R5: claims[*].evidence_ids -> evidence -> source -> 원문을 대조한다.
# status=completed도 사람의 내용 검토 완료를 뜻하지 않는다.
```

**공용 schema.py 합류 전 확인 항목:** Claim 추가 메타데이터(actor/statement_date/context/
stakeholder_group/explanation), Source 웹 메타데이터, Evidence location, Gap item의 허용 형식을
R1 모델에 반영하거나 명시적 어댑터로 매핑한다. Pydantic 모델이 아직 없어 호환 검증을 했다고 주장하지 않는다.
State 표의 묶음 표기(run_id/run_config 등)를 Python 필드로 나누는 판단은 R1 소유다.

### 최소한의 경계 파일 변경

- src/rag/index.py: BM25 생성만 제거, dense 저장 구조 유지. 파서·청킹·코퍼스·모델 revision은 R2 변경 대상.
- eval/retrieval_metrics.py: MODES를 dense 하나로 변경해 제거된 RRF 호출을 막음. 정답 청크 기반
  지표와 20문항 재구성은 R2가 구현해야 하며 이 브랜치가 검색 품질 실측을 완료한 것은 아님.
- pyproject.toml: pytest test extra 추가. .gitignore: runs와 구버전 웹 캐시 제외.
- config/settings.yaml의 rrf_k/dense_weight/sparse_weight와 query_kw.py는 구버전 잔여 항목이며
  R3 검색에서는 읽지 않는다. R1/R2 설정 정리 시 제거 가능하다.

### 남은 역할별 통합 작업

| 역할 | 연결 시 해야 할 일 |
|---|---|
| R1 | 공용 Pydantic schema 확정, run_id 초기화, 새 State/collect_evidence/최종 라우팅 연결 |
| R2 | 3컬렉션 최신 코퍼스, E5 revision/파서·청킹/해시, source_id 별칭과 권한 메타데이터 유지, goldenset 재라벨링 |
| R4 | bind_document_search를 공통 평가 함수에 주입, 새 Assessment 반환 |
| R5 | 웹 Claim도 형식·내용 검토 포함, 실패 Assessment 차단, 사용 Claim에서 REFERENCE 역생성 |

`src/agents/market.py` 등 구버전 본문의 담당 표기는 최신 R4 분장과 다를 수 있다.
R3는 market 노드 내용을 수정하지 않았다. 이전 보고서 생성기는 text/citations를 읽으므로
새 Assessment를 그 경로에 직접 연결하면 안 된다. 구버전 형식으로 이중 출력해 검증을 우회하지 않는다.
