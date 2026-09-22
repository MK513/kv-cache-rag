"""REFERENCE 포맷터 — 담당: R5 (설계서 §9, v13 3구분)

설계서 §9 는 "Doc Pool 논문만" 이라 쓰고 §3·§6 은 웹 자료도 인용한다고 쓴다.
그대로 두면 본문 인용이 참고문헌에 연결되지 않는다. 출처 성격대로 3구분한다.
  [A] Doc Pool 논문 — 가이드가 지정한 풀 안의 논문
  [B] 풀 밖 색인 자료 — ecosystem·context. 색인 대상이라 200쪽 가드에 포함된다
  [C] 웹 조회 자료 — 이해관계자 근거. 색인하지 않으므로 200쪽 계산과 무관
"""

GROUPS = [
    ("[A] Doc Pool 논문 (RAG 색인)", ["papers_core"]),
    ("[B] 풀 밖 색인 자료 (ecosystem · context)", ["ecosystem", "context"]),
]


def build(manifest: list[dict], web_sources: list[dict]) -> str:
    lines = ["## REFERENCE", ""]

    for heading, cols in GROUPS:
        rows = [m for m in manifest if m["collection"] in cols]
        lines += [f"### {heading}", ""]
        for m in rows:
            tag = f" · {m['role']}" if m.get("role") else ""
            lines.append(
                f"- {m['title']}. *{m['venue']}*{tag} ({m['published']}). "
                f"{m['pages']}쪽 / 청크 {m['chunks']}개. SHA-256 `{m['sha256'][:16]}…`  \n"
                f"  수집 {m['retrieved_at']} · {m['url']}"
            )
        if not rows:
            lines.append("- 해당 없음")
        lines.append("")

    lines += ["### [C] 웹 조회 자료 (색인 제외 · 이해관계자 근거)", ""]
    seen = set()
    for c in sorted(web_sources or [], key=lambda x: x["url"]):
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        lines.append(
            f"- [{c['source_type']}] {c['title']}. 게시 {c['published_at']} · "
            f"수집 {c['retrieved_at']}  \n  {c['url']}"
        )
    if not seen:
        lines.append("- 확인된 웹 자료 없음 (미확인)")

    return "\n".join(lines)
