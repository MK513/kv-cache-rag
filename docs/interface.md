# 팀 인터페이스 계약

담당자끼리 **먼저 고정해야 하는 것만** 모았다. 여기 정의된 형식은 소유자 승인 없이 바꾸지 않는다.
구현 세부는 각 파일의 docstring, 코퍼스 수집 절차는 [corpus-prep.md](corpus-prep.md) 참고.

| 계약 | 소유 | 소비 |
|---|---|---|
| ① State 17키 | R1 | 전원 |
| ② 관점 dict `{text, citations, gaps}` | R1 · R5 | R3 · R4 |
| ③ chunk 스키마 + 검색 도구 시그니처 | R2 | R3 · R4 |
| ④ `validation_errors` | R5 | R1 (라우팅) |
| ⑤ `sources.json` 의 `scope` · `perspectives` 값 | R2 (선별 R3) | R3 · R4 |

①②③만 정해지면 **R3~R5는 인덱스 완성 전에도 목업 청크로 개발을 시작할 수 있다** (§6).

---

## ① State 17키 — `src/state.py` (R1)

`TypedDict(total=False)`. **각 키는 단독 쓰기 주체를 갖는다.** 병렬 fan-out에서 공통 키에
두 노드가 쓰면 LangGraph가 `InvalidUpdateError`를 낸다. 누적 reducer는 `trace` 하나뿐이다.

| 키 | 형식 | 단독 쓰기 주체 |
|---|---|---|
| `domain` | `str` | 입력 (`app.py`) |
| `sources` | `list[dict]` | `app.py` ← `data/manifest.json` |
| `web_sources` | `list[dict]` | `stakeholder` |
| `evidence` | `list[dict]` | **`research` 만** |
| `research` | `dict` (관점 dict) | `research` |
| `tech_status` | `dict[str, str]` | `research` |
| `gaps` | `list[str]` | `research` |
| `retrieval_round` | `int` | `research` |
| `maturity` | `dict` (관점 dict) | `maturity` |
| `market` | `dict` (관점 dict) | `market` |
| `stakeholder` | `dict` (관점 dict) | `stakeholder` |
| `domain_assessment` | `dict` (관점 dict) | `domain_assessment` |
| `synthesis` | `dict` | `synthesis` |
| `validation_errors` | `list[dict]` | **`output/validate.py` 만** |
| `validation_round` | `int` | **`output/validate.py` 만** |
| `report` | `str` | `report` 또는 `abort` |
| `trace` | `Annotated[list[dict], operator.add]` | 전 노드 (누적) |

```python
tech_status = {"TurboQuant": "ok", "ITME": "평가 보류"}
```

`evidence` 단일 쓰기 주체 원칙 — `maturity`·`domain_assessment`도 인덱스를 조회하지만
`evidence`에 쓰지 않고 자기 키에만 반영한다. 동시 쓰기가 구조적으로 불가능해진다.

### trace 한 줄 형식

```python
{"node": "maturity", "chunks": 14}          # 노드가 남기는 기록
{"tool": "query_kw", "ko": "...", "en": "..."}   # 도구가 남기는 기록
```

`node` 또는 `tool` 키 하나는 반드시 넣는다. 나머지 필드는 자유.

---

## ② 관점 dict — `src/agents/common.py` (R1 · R5)

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

## ④ `validation_errors` — `src/output/validate.py` (R5 → R1)

R5가 쓰고 R1이 라우팅에 쓰는 **유일한 키**다.

```python
[{"node": "maturity", "kind": "없는 인용 ID", "ids": ["deadbeef0000"]},
 {"node": "market",   "kind": "인용 누락",     "ids": []},
 {"node": "synthesis","kind": "관점 상충 0건",  "ids": []}]
```

`kind` 는 위 3종만 쓴다. 늘릴 때는 R1과 함께 정한다 — 라우팅이 이 값을 본다.

검증 대상은 `CITED_NODES = ["research", "maturity", "market", "domain_assessment"]`.
`stakeholder` 는 인용이 url이라 chunk_id 검증 대상이 아니다.

### 두 지점에서 검사한다

```
fan-in 직후   validate_eval    stage="post_eval"        → 평가 4노드 재실행 (1회)
synthesis 직후 validate_final   stage="post_synthesis"   → 종합 재작성 (1회)
```

앞쪽이 없으면 평가 노드가 만든 가짜 인용 ID가 보고서 §4 본문에 그대로 실린다 —
종합 재작성만으로는 고쳐지지 않는다. 한도 초과 시 `abort` 노드가 실패를 남긴다
(조용히 통과시키지 않는다).

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
