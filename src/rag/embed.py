"""임베딩 — 담당: R2 (설계서 §4)

multilingual-e5-small 단독. revision 과 차원을 고정해 재실행 간 인덱스가 흔들리지
않게 한다. e5 계열은 query: / passage: 접두사와 정규화를 요구한다.
"""

from langchain_huggingface import HuggingFaceEmbeddings

EMBED_MODEL = "intfloat/multilingual-e5-small"
EMBED_REVISION = "main"
# TODO(R2): 재현성을 위해 위 값을 커밋 해시로 고정할 것. 네트워크가 열린 환경에서
# 아래 명령으로 현재 main 이 가리키는 해시를 확인해 대입한다:
#   curl -s https://huggingface.co/api/models/intfloat/multilingual-e5-small \
#     | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])"
# 이 세션은 huggingface.co 로 나가는 네트워크가 막혀 있어 값을 직접 조회하지 못했다.
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
