"""REFERENCE 포맷터 — 담당: R5

원칙:
- 실제 Claim에 사용된 Evidence를 따라가 최종 출처를 추출한다.
- 후보로 검색됐지만 Claim에 사용되지 않은 자료는 최종 REFERENCE에서 제외한다.
- 아직 구형 Assessment 구조를 사용하는 노드가 있으므로,
  기존 manifest 기반 fallback도 유지한다.
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


def _format_structured_sources(sources: list[dict]) -> str:
    """신형 Source 구조를 Markdown REFERENCE로 변환한다."""
    lines = ["## REFERENCE", ""]

    rag_sources = []
    web_sources = []

    for source in sources:
        collection = source.get("collection")

        if collection in {"papers_core", "ecosystem", "context"}:
            rag_sources.append(source)
        else:
            web_sources.append(source)

    for heading, collections in GROUPS:
        rows = [
            source
            for source in rag_sources
            if source.get("collection") in collections
        ]

        lines += [f"### {heading}", ""]

        if not rows:
            lines.append("- 해당 없음")
            lines.append("")
            continue

        for source in sorted(
            rows,
            key=lambda item: (
                item.get("title", ""),
                item.get("source_id", ""),
            ),
        ):
            title = source.get("title") or source.get("source_id") or "제목 미확인"
            published = source.get("published") or source.get("published_at") or "날짜 미확인"
            url = source.get("url") or ""
            venue = source.get("venue") or source.get("site") or ""
            version = source.get("version") or ""

            suffix = []
            if venue:
                suffix.append(venue)
            if version:
                suffix.append(str(version))

            extra = f" · {' · '.join(suffix)}" if suffix else ""

            lines.append(
                f"- {title} ({published}){extra}"
                + (f"  \n  {url}" if url else "")
            )

        lines.append("")

    lines += ["### [C] 웹 조회 자료", ""]

    if not web_sources:
        lines.append("- 해당 없음")
    else:
        for source in sorted(
            web_sources,
            key=lambda item: item.get("url", ""),
        ):
            title = source.get("title") or "제목 미확인"
            institution = (
                source.get("institution")
                or source.get("organization")
                or source.get("source_type")
                or "출처 미확인"
            )
            published = source.get("published_at") or "게시일 미확인"
            retrieved = source.get("retrieved_at") or "조회일 미확인"
            url = source.get("url") or ""

            lines.append(
                f"- {institution} ({published}). {title}. "
                f"조회 {retrieved}"
                + (f"  \n  {url}" if url else "")
            )

    return "\n".join(lines)


def _legacy_build(manifest: list[dict], web_sources: list[dict]) -> str:
    """구형 State를 위한 기존 fallback."""
    lines = ["## REFERENCE", ""]

    for heading, cols in GROUPS:
        rows = [
            item
            for item in manifest
            if item.get("collection") in cols
        ]

        lines += [f"### {heading}", ""]

        for item in rows:
            tag = f" · {item['role']}" if item.get("role") else ""

            lines.append(
                f"- {item.get('title', '제목 미확인')}. "
                f"*{item.get('venue', '')}*{tag} "
                f"({item.get('published', '날짜 미확인')}). "
                f"{item.get('pages', '?')}쪽 / "
                f"청크 {item.get('chunks', '?')}개. "
                f"SHA-256 `{str(item.get('sha256', ''))[:16]}…`  \n"
                f"  수집 {item.get('retrieved_at', '미확인')} · "
                f"{item.get('url', '')}"
            )

        if not rows:
            lines.append("- 해당 없음")

        lines.append("")

    lines += ["### [C] 웹 조회 자료", ""]

    seen = set()

    for item in sorted(
        web_sources or [],
        key=lambda value: value.get("url", ""),
    ):
        url = item.get("url", "")

        if not url or url in seen:
            continue

        seen.add(url)

        lines.append(
            f"- [{item.get('source_type', '미확인')}] "
            f"{item.get('title', '제목 미확인')}. "
            f"게시 {item.get('published_at', '미확인')} · "
            f"수집 {item.get('retrieved_at', '미확인')}  \n"
            f"  {url}"
        )

    if not seen:
        lines.append("- 확인된 웹 자료 없음")

    return "\n".join(lines)


def build(
    manifest: list[dict] | None = None,
    web_sources: list[dict] | None = None,
    state: dict | None = None,
) -> str:
    """최종 REFERENCE 생성.

    state에 신형 Assessment가 있으면 실제 Claim 사용 출처만 역추적한다.
    그렇지 않으면 기존 manifest/web_sources 방식으로 fallback한다.
    """
    if state:
        used_sources = _used_sources(state)

        if used_sources:
            return _format_structured_sources(used_sources)

    return _legacy_build(
        manifest or [],
        web_sources or [],
    )