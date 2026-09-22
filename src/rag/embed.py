"""임베딩 — 담당: R2 (설계서 §4)

multilingual-e5-small 단독. revision 과 차원을 고정해 재실행 간 인덱스가 흔들리지
않게 한다. e5 계열은 query: / passage: 접두사와 정규화를 요구한다.
"""

from langchain_huggingface import HuggingFaceEmbeddings

EMBED_MODEL = "intfloat/multilingual-e5-small"
# 설계서 §4 — 모델 revision 을 기록·고정한다. "main" 은 움직이는 포인터라 고정이 아니다.
# 모델이 갱신되면 벡터가 바뀌고 chunk 회수 결과가 통째로 달라진다.
# 값의 출처: 이 저장소가 실제로 내려받아 인덱스를 만든 스냅샷
# (~/.cache/huggingface/hub/models--intfloat--multilingual-e5-small/refs/main).
# 올릴 때는 goldenset 재라벨링을 함께 검토한다.
EMBED_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBED_DIM = 384


class E5Embeddings(HuggingFaceEmbeddings):
    def embed_query(self, text: str) -> list[float]:
        return super().embed_query(f"query: {text}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([f"passage: {t}" for t in texts])


def get_embeddings() -> E5Embeddings:
    emb = E5Embeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"revision": EMBED_REVISION},
        encode_kwargs={"normalize_embeddings": True},
    )
    dim = len(emb.embed_query("차원 확인"))
    assert dim == EMBED_DIM, f"임베딩 차원 불일치: {dim} != {EMBED_DIM}"
    return emb
