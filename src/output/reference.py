"""REFERENCE 포맷터 — 담당: R5

원칙:
- 실제 Claim에 사용된 Evidence를 따라가 최종 출처를 추출한다(§8).
- 후보로 검색됐지만 Claim에 사용되지 않은 자료는 최종 REFERENCE에서 제외한다(§9).
- 출처마다 번호를 매긴다. 본문의 `[chunk_id]` 는 검증용 내부 ID 라 독자가 읽을 수 없다.
  report 가 이 번호로 바꿔 넣는다.
- 색인 출처의 서지정보는 `run_config.sources`(매니페스트)에서 채운다. 청크 메타데이터에는
  제목·URL·버전이 없어 source_id 만 남는다 — §9 가 요구하는 저자·연도·게재처·인용 페이지·
  URL 를 그것만으로는 채울 수 없다.
"""

GROUPS = [
    ("[A] Doc Pool 논문 (RAG 색인)", ["papers_core"]),
    ("[B] 풀 밖 색인 자료 (ecosystem · context)", ["ecosystem", "context"]),
]

ASSESSMENT_NODES = [
    "research",
    "maturity",
    "market",
    "stakeholder",
    "domain_assessment",
]


def _used_source_ids_from_assessment(assessment: dict) -> set[str]:
    """신형 Assessment에서 실제 Claim이 사용한 source_id만 추출한다."""
    claims = assessment.get("claims") or []
    evidence = assessment.get("evidence") or []

    evidence_by_id = {
        item.get("evidence_id"): item
        for item in evidence
        if item.get("evidence_id")
    }

    used_source_ids = set()

    for claim in claims:
        for evidence_id in claim.get("evidence_ids") or []:
            item = evidence_by_id.get(evidence_id)

            if not item:
                continue

            source_id = item.get("source_id")

            if source_id:
                used_source_ids.add(source_id)

    return used_source_ids


def _manifest_by_id(state: dict) -> dict:
    """setup 이 run_config.sources 에 넣어 둔 매니페스트를 source_id 로 색인한다."""
    sources = (state.get("run_config") or {}).get("sources") or []
    return {entry.get("id"): entry for entry in sources if entry.get("id")}


def _cited_pages(state: dict) -> dict:
    """source_id -> 본문이 실제 인용한 위치 목록 (§9 — 인용 페이지를 적는다)."""
    pages: dict[str, set] = {}
    for node_name in ASSESSMENT_NODES:
        assessment = state.get(node_name) or {}
        used = _used_source_ids_from_assessment(assessment)
        for evidence in assessment.get("evidence") or []:
            source_id = evidence.get("source_id")
            if source_id in used and evidence.get("location"):
                pages.setdefault(source_id, set()).add(evidence["location"])
    return pages


def _used_sources(state: dict) -> list[dict]:
    """신형 Assessment에서 실제 사용된 Source를 모아 중복 제거한다."""
    used = {}

    for node_name in ASSESSMENT_NODES:
        assessment = state.get(node_name) or {}

        if "claims" not in assessment:
            continue

        used_source_ids = _used_source_ids_from_assessment(assessment)

        for source in assessment.get("sources") or []:
            source_id = source.get("source_id")

            if source_id in used_source_ids:
                used[source_id] = source

    return list(used.values())


def _date(value) -> str:
    """ISO 타임스탬프를 날짜까지만 남긴다. §9 는 게시일·조회일을 요구하며 초 단위는 노이즈다."""
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text


def _page_key(location: str):
    """p.5 가 p.15 뒤로 가지 않게 숫자로 정렬한다."""
    digits = "".join(c for c in str(location) if c.isdigit())
    return (int(digits) if digits else 0, str(location))


def _format_structured_sources(sources: list[dict], numbers: dict, pages: dict) -> str:
    """신형 Source 구조를 Markdown REFERENCE 로 변환한다. 번호는 본문 인용과 맞춘다."""
    lines = ["## REFERENCE", ""]

    rag_sources = [s for s in sources
                   if s.get("collection") in {"papers_core", "ecosystem", "context"}]
    web_sources = [s for s in sources if s not in rag_sources]

    def entry(source: dict) -> str:
        number = numbers.get(source.get("source_id"), "?")
        title = source.get("title") or source.get("source_id") or "제목 미확인"
        version = str(source.get("version") or "")
        # 논문은 게시일 대신 arXiv 버전이 연도를 담는다(§9 — "연도 ... 또는 arXiv 버전").
        # 버전을 날짜 자리에 쓰면 뒤에서 다시 적지 않는다.
        published = _date(source.get("published") or source.get("published_at"))
        head = published or version or "날짜 미확인"
        bits = [b for b in (source.get("venue") or source.get("publisher") or "",
                            version if published else "") if b]
        cited = sorted(pages.get(source.get("source_id"), []), key=_page_key)
        if cited:
            bits.append("인용 " + ", ".join(cited))
        url = source.get("url") or ""
        return (f"- [{number}] {title} ({head})"
                + (f" · {' · '.join(bits)}" if bits else "")
                + (f"  \n  {url}" if url else ""))

    for heading, collections in GROUPS:
        rows = [s for s in rag_sources if s.get("collection") in collections]
        lines += [f"### {heading}", ""]
        if not rows:
            lines += ["- 해당 없음", ""]
            continue
        lines += [entry(s) for s in sorted(rows, key=lambda i: numbers.get(i["source_id"], 0))]
        lines.append("")

    lines += ["### [C] 웹 조회 자료", ""]
    if not web_sources:
        lines.append("- 해당 없음")
    else:
        for source in sorted(web_sources, key=lambda i: numbers.get(i["source_id"], 0)):
            number = numbers.get(source.get("source_id"), "?")
            institution = (source.get("institution") or source.get("organization")
                           or source.get("source_type") or "출처 미확인")
            lines.append(
                f"- [{number}] {institution} ({_date(source.get('published_at')) or '게시일 미확인'}). "
                f"{source.get('title') or '제목 미확인'}. "
                f"조회 {_date(source.get('retrieved_at')) or '조회일 미확인'}"
                + (f"  \n  {source.get('url')}" if source.get("url") else ""))

    return "\n".join(lines)


def build(state: dict) -> tuple[str, dict]:
    """최종 REFERENCE 와 `evidence_id -> 번호` 지도를 함께 돌려준다.

    §8 — 참고문헌은 실제 포함된 Claim 의 근거에서 역으로 만든다.
    §9 — 본문에서 실제 사용한 자료만 적고 후보로만 조회한 자료는 제외한다.
    """
    manifest = _manifest_by_id(state)
    # 청크에는 제목·URL·버전이 없어 R4 가 source_id 를 title 자리에 넣는다. 서지 항목만
    # 매니페스트 값으로 덮는다(§9). run_id·collection·allowed_uses 는 Source 것을 지킨다.
    biblio = ("title", "url", "version", "publisher", "published_at", "landing")
    used = []
    for source in _used_sources(state):
        entry = manifest.get(source.get("source_id"), {})
        used.append(source | {k: entry[k] for k in biblio if entry.get(k)})

    order = sorted(used, key=lambda s: ({"papers_core": 0, "ecosystem": 1, "context": 1}
                                        .get(s.get("collection"), 2),
                                        s.get("title") or s.get("source_id") or ""))
    numbers = {s["source_id"]: i for i, s in enumerate(order, 1)}

    marks = {}
    for node_name in ASSESSMENT_NODES:
        assessment = state.get(node_name) or {}
        for evidence in assessment.get("evidence") or []:
            number = numbers.get(evidence.get("source_id"))
            if number:
                marks[evidence["evidence_id"]] = number

    return _format_structured_sources(order, numbers, _cited_pages(state)), marks
