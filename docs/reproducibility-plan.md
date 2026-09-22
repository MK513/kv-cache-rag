# 재현성 계획

목표 — **같은 코퍼스로 다시 돌리면 같은 주장이 나오고, 검토 판정이 그대로 이월된다.**

지금은 `--carry-review <run_id>` 가 주장 문장과 근거가 완전히 같은 Claim 의 판정만
옮긴다. 안전하지만 적중률이 낮다. 적중률을 올리려면 실행을 결정적으로 만들어야 한다.

**100% 는 불가능하다.** OpenAI 는 `seed` 를 best-effort 로만 보장하고 웹 검색 결과는
매일 바뀐다. 목표는 "완전 결정적" 이 아니라 **흔들리는 축을 하나씩 고정하고, 남은 축은
기록·재생으로 덮는 것**이다.

---

## 흔들리는 축 (영향 큰 순)

| # | 축 | 지금 | 결과 |
|---|---|---|---|
| A1 | 임베딩 모델 revision | `EMBED_REVISION = "main"` | HF 가 모델을 올리면 벡터가 바뀐다 → 검색 결과 → 인용 청크 → **모든 주장** |
| A2 | 코퍼스 | `prepare_sources` 가 매번 재다운로드 | 원문이 바뀌면 chunk_id 가 전부 바뀐다. 실제로 `turboquant-blog` 해시가 이미 한 번 바뀌었다 |
| A3 | LLM 샘플링 | `temperature: 0`, seed 없음 | 같은 프롬프트에도 문장이 달라질 수 있다 |
| A4 | 웹 근거 | 매 실행 Tavily 재검색 | stakeholder 는 **원리적으로** 재현 불가 |
| A5 | 컬렉션 순서 | `for cid in cited_ids_set` | Source/Evidence 목록 순서가 실행마다 달라진다 |
| A6 | claim_id | `f"..._{run_id[:8]}"` = 날짜 | 같은 날 다른 내용이 같은 ID. 이월 키로 못 쓴다 |

A1·A2 가 먼저다. 이 둘이 흔들리면 아래를 아무리 고정해도 소용없다.

---

## Phase 1 — 입력을 동결한다 (A1·A2)

**A1. 임베딩 revision 을 커밋 해시로 핀**

`src/rag/embed.py:10` 이 `"main"` 이다. 파일 docstring 은 *"revision 과 차원을 고정해
재실행 간 인덱스가 흔들리지 않게"* 라고 적혀 있는데 `main` 은 고정이 아니라 움직이는
포인터다. 같은 파일 14행에 sha 를 뽑는 명령이 주석으로 남아 있다.

```python
EMBED_REVISION = "<huggingface commit sha>"
```

한 줄이고 효과가 가장 크다. **소유: R2.**

**A2. 코퍼스를 해시로 동결**

- `scripts/prepare_sources.py` 의 기본 동작을 `--verify` 로 바꾸고, 재다운로드는
  `--refresh` 를 명시해야 하게 한다.
- `data/manifest.json` 의 `sha256` 과 로컬 파일이 다르면 **실패**시킨다. 지금은 조용히
  덮어쓰고 매니페스트를 갱신한다.
- `setup` 노드가 적재 시 해시를 대조하고 어긋나면 실행을 멈춘다(§3 — *적재할 때 실제
  페이지 수, 버전, SHA-256 해시를 다시 확인한다*).

**소유: R2.** 주의 — 원문이 정말 바뀌었으면 `eval/goldenset.json` 라벨을 다시 해야 한다.

---

## Phase 2 — 실행을 고정한다 (A3·A5·A6)

**A3. LLM seed 고정 + fingerprint 기록**

```yaml
llm:
  temperature: 0
  seed: 20260922        # 설정에 고정
```

`src/llm.py` 가 `seed` 를 넘기고, 응답의 `system_fingerprint` 를 trace 에 남긴다.
fingerprint 가 달라졌으면 **모델이 바뀐 것**이므로 재현을 기대하면 안 된다 — 그 사실을
`run.json` 에 남겨 두면 "왜 결과가 다르지" 를 추적할 수 있다. §6 이 요구하는
*실행 시 모델 식별자와 버전 기록* 도 이걸로 채워진다. **소유: R1.**

**A5. 순서를 정렬한다**

`research`·`maturity`·`domain`·`market` 이 `for cid in cited_ids_set:` 로 집합을
순회한다. 집합 순서는 실행마다 다를 수 있어 `Assessment.sources`/`evidence` 목록 순서가
흔들리고 `state.json` diff 가 매번 달라진다. `sorted(cited_ids_set)` 로 바꾼다.
내용은 그대로이고 산출물만 바이트 단위로 안정된다. **소유: R4.**

**A6. claim_id 를 내용 해시로**

```python
claim_id = "claim-" + sha256(node|technology|kind|text|evidence_ids)[:12]
```

R3 stakeholder 가 이미 이 방식이다. R4 만 날짜를 쓴다. 바꾸면 **claim_id 자체가
이월 키**가 되어 지금의 지문 비교가 단순해지고, 같은 날 다른 내용이 같은 ID 를 갖는
위험이 사라진다. **소유: R4.**

---

## Phase 3 — 못 고정하는 것은 재생한다 (A4)

웹 검색은 결정적일 수 없다. 대신 **이전 실행이 수집한 원문을 그대로 다시 쓴다.**

R3 는 이미 `runs/<run_id>/web/` 에 `manifest.json`(검색 결과·본문·해시)과
`snapshots/` 를 남긴다. 재생에 필요한 건 다 있다.

```bash
uv run python app.py --replay-web <이전 run_id>
```

- `WebEvidenceStore` 가 새 검색·수집 대신 이전 실행의 manifest 를 읽는다.
- 스냅샷 해시를 대조해 파일이 바뀌었으면 실패시킨다.
- `run.json` 에 `replayed_web_from` 을 남긴다. **이번 실행이 웹을 새로 조사하지
  않았다는 사실**이 보고서 §6 의 조사 시점 해석에 필요하다.

§7 의 *노드 실행 전에는 해당 실행의 자료만 사용하며* 와 충돌하지 않게, 재생한 근거의
`run_id` 는 원본 run 을 유지하고 검증에서 예외로 인정할지 **R3 와 합의가 필요하다.**
이게 이 계획에서 유일하게 계약 변경이 필요한 지점이다.

**소유: R3 + R1.**

---

## Phase 4 — 재현됐는지 확인한다

```bash
uv run python -m scripts.compare_runs <run_a> <run_b>
```

두 실행의 `state.json` 을 비교해 어디서 갈라졌는지 한 줄로 보여 준다 —
코퍼스 해시 / 임베딩 revision / system_fingerprint / 인용 청크 집합 / 주장 문장.
**"재현이 됐다" 를 주장하려면 이걸로 증명해야 한다.** §10 도 미측정 값을 결과로 적지
말라고 한다. **소유: R1.**

---

## 적용 순서와 기대치

| 단계 | 하고 나면 |
|---|---|
| Phase 1 | 같은 코퍼스·같은 인덱스가 보장된다. chunk_id 가 안 흔들린다 |
| Phase 2 | 논문 기반 네 노드(research·maturity·market·domain)의 주장이 대체로 재현된다 → `--carry-review` 적중률이 오른다 |
| Phase 3 | stakeholder 까지 재현된다. 전 Claim 이월이 가능해진다 |
| Phase 4 | 재현 여부를 말이 아니라 데이터로 확인한다 |

Phase 1 만 해도 값어치가 있다. Phase 2 까지 하면 이번 실행의 16개 중 논문 기반 7개가
이월 후보가 된다. 나머지 9개(stakeholder)는 Phase 3 이 필요하다.

**이월은 재현성의 대체재가 아니라 안전망이다.** 재현이 깨졌을 때 `--carry-review` 는
조용히 옛 판정을 붙이지 않고 미판정으로 되돌린다. 두 장치를 같이 둔다.


---

## 실측 결과 (2026-09-22) — 모델 결정성에 기댈 수 없다

Phase 1 + seed 적용 후 같은 코퍼스로 재실행했더니 **이월 0건**이었다.

| 노드 | 인용 근거 | 본문 유사도 |
|---|---|---|
| research | 9건 → 11건 | 0.282 |
| **maturity** | **5건 동일** | **0.258** |
| market | 3건 → 2건 | 0.081 |

`maturity` 가 결정적이다. **인용 근거가 완전히 같은데 본문이 74% 달랐다.** 코퍼스·인덱스·
검색은 고정됐고(`verify_corpus` 통과) 남은 변수는 모델뿐이다.

원인: `langchain_openai` 가 `gpt-5.6-luna` 에 대해 **`temperature` 를 아예 보내지 않는다**
(추론형 모델로 취급). `settings.yaml` 의 `temperature: 0` 이 조용히 무시되고 기본 샘플링으로
돌았다. 추론형은 `seed` 도 대개 무시한다.

```
gpt-5.6-luna  → temperature=None  seed=7
gpt-4.1-mini  → temperature=0.0   seed=7
```

### 그래서 A3 를 응답 캐시로 대체했다

`set_llm_cache(SQLiteCache(".cache/llm.sqlite"))`. 키가 **프롬프트 전문 + 모델 파라미터**라
같은 코퍼스·같은 지시문이면 같은 응답이 나온다. 모델의 결정성에 기대지 않고 구조적으로
재현한다. 프롬프트가 바뀌면 자동으로 새로 생성한다.

- 캐시는 `.cache/` (gitignore 대상)라 **기계마다 따로 쌓인다.** 팀 전체 재현이 필요하면
  캐시를 공유하거나 각자 한 번씩 돌려 채워야 한다.
- 적중 수를 세어 `run.json` 의 `llm` 과 보고서 §6 에 남긴다. 적중은 *이번 실행에서 모델이
  새로 판단하지 않았다* 는 뜻이고, §6 의 *실제 비용은 토큰 사용량과 실행 기록으로 확인한다*
  를 지키려면 밝혀야 한다.
- `run_config.model` 은 설정값, `run.json` 의 `llm` 은 **실효값**이다. 설정에 `temperature: 0`
  이라 적어 놓고 실제로는 안 보낸 상태를 그대로 기록하면 §6 위반이다.
