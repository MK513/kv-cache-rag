"""인덱스 점검 — 담당: R2

LLM 을 호출하지 않으므로 API 키 없이 돌아간다.
수집·200쪽 가드·청킹·컬렉션 분리·키워드 변환을 한 번에 검증한다.

    uv run python -m scripts.ingest

('python scripts/ingest.py' 로 직접 실행하면 sys.path[0] 이 scripts/ 가 되어
 src 를 찾지 못한다. -m 은 현재 디렉토리를 경로에 넣는다.)
"""

from src.rag.index import build
from src.rag.query_kw import translated
from src.tools.docs import search_source_documents


def main():
    idx = build()

    print("\n=== 매니페스트 ===")
    for m in idx["manifest"]:
        print(f"  {m['collection']:12s} {m['id']:20s} {m['pages']:3d}쪽 "
              f"청크 {m['chunks']:4d}  sha256={m['sha256'][:12]}…")
    print(f"  합계 {sum(m['pages'] for m in idx['manifest'])}쪽 / "
          f"청크 {len(idx['chunks'])}개")

    print("\n=== 키워드 변환 (BM25 축 전용) ===")
    kw, changed = translated("KV 캐시 양자화 처리량 지연")
    assert changed, "용어사전이 적용되지 않았다 — src/rag/query_kw.py 확인"
    print(f"  ko: KV 캐시 양자화 처리량 지연\n  en: {kw}")

    print("\n=== 컬렉션별 검색 ===")
    for col, q in [("papers_core", "KV 캐시 메모리 병목"),
                   ("ecosystem", "시장 규모 채택 현황"),
                   ("context", "데이터센터 추론 비용")]:
        hits = search_source_documents.invoke({"query": q, "collection": col, "top_k": 3})
        if not hits:
            print(f"  {col:12s} ⚠ 근거 0건 — sources.yaml 미확정")
            continue
        assert all(h["collection"] == col for h in hits), f"{col} 컬렉션 누수"
        print(f"  {col:12s} {len(hits)}건  예: {hits[0]['source']} p{hits[0]['page']} "
              f"[{hits[0]['chunk_id']}]")

    print("\n=== 기술별 필터 ===")
    for tech in ("TurboQuant", "ITME"):
        hits = search_source_documents.invoke(
            {"query": "처리량 정확도", "collection": "papers_core", "technology": tech, "top_k": 3})
        assert hits, f"{tech} 검색 결과 없음"
        print(f"  {tech}: {len(hits)}건")

    if idx.get("pending"):
        print(f"\n⚠ 미확정 {len(idx['pending'])}건: {', '.join(idx['pending'])}")
        print("  papers_core 파이프라인은 정상. R3 가 sources.yaml 을 채우면 전체가 돈다.")
    print("\nOK")


if __name__ == "__main__":
    main()
