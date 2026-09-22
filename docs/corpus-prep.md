<!-- 담당: R2. 프로젝트 전체 문서는 ../README.md -->

# RAG 코퍼스 준비

설계서 3절(문서 풀과 RAG 적용)의 수집·기록 절차를 구현한다.

## 실행 (uv)

```bash
uv venv
uv pip install -r requirements.txt
uv run python scripts/prepare_sources.py
```

다운로드만 할 거면 `requirements.txt` 의 [1] 블록만 설치해도 된다.
torch/sentence-transformers는 용량이 커서 수집 단계에는 필요 없다.

재현용 잠금 파일은 아래로 만들어 커밋한다.

```bash
uv pip compile requirements.txt -o requirements.lock.txt
uv pip sync requirements.lock.txt
```

설계서 재현성 요건이 "명시적 준비 명령으로 내려받고 해시를 검사한다"이므로,
잠금 파일과 `data/manifest.json` 의 해시가 함께 있어야 재현이 성립한다.

`data/raw/` 에 원문이, `data/manifest.json` 에 출처별 해시·분량 기록이 생성된다.

## 무엇을 기록하는가

| 항목 | 설계서 근거 |
|---|---|
| `sha256`, `retrieved_at`, `url`, `version` | "원문 URL, 문서 ID, 버전, 해시를 보존한다" |
| `pdf_pages` | "PDF는 실제 페이지 수를 센다" |
| `pages_counted`, `page_basis` | "웹 문서는 A4 기준 환산 쪽수를 산출한다" |
| `total_pages`, `within_budget` | "합계가 200쪽을 넘으면 적재를 중단한다" |
| `collection` | papers_core / ecosystem / context 분리 |
| `scope`, `perspectives`, `applies_to` | 관점별 자료 범위 표시 |

## 환산 기준

웹 문서에는 고유 페이지가 없으므로, 본문만 추출한 뒤 **A4·9.5pt 기준 1쪽 = 3,200자**로 환산한다.
값은 `sources.json` 의 `chars_per_page_a4_95pt` 에서 바꿀 수 있고, 매니페스트의
`page_basis` 필드에 사용한 기준이 함께 기록된다.

## 주의

- `kv-cache-survey` 는 `excerpt_only: true` 다. 전문을 색인하지 말고 분류 체계 장만
  발췌해 `context` 컬렉션에 넣는다. 성능 주장의 근거로는 인용하지 않는다.
- `scope: ecosystem` 자료는 선정 기술의 **직접 채택 근거가 아니다.** 보고서 인용 시
  인접 생태계 자료임을 표시한다.
- 내려받은 원문은 Git 배포에서 제외하고(`.gitignore`), 재현 시 이 스크립트로 다시
  받아 해시를 대조한다.

## 토크나이저 주의

청크를 400토큰으로 자를 때는 **임베딩 모델과 동일한 토크나이저**를 써야 한다.

```python
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("intfloat/multilingual-e5-small")
```

범용 토크나이저나 글자 수로 자르면 실제 입력이 모델 한도(512토큰)를 넘을 수 있고,
그러면 뒷부분이 조용히 잘린 채 임베딩된다. 설계서 4절이 "모델 입력 한도를 검사한다"고
한 부분이 이것이다.

## 다음 단계

매니페스트가 만들어지고 **총 쪽수가 한도 안이라는 게 확인된 뒤에** 청킹을 확정하고,
그다음에 평가셋 라벨링을 시작한다. 청킹 파라미터를 바꾸면 청크 ID가 전부 달라져
라벨링을 다시 해야 한다.
