"""평가 노드 공통 헬퍼 — 담당: R1 (계약 소유)

5개 평가 노드가 모두 `검색 → 근거 포맷 → LLM → {text, citations, gaps}` 로
같은 모양이라 여기 한 번만 쓴다. 각 노드는 질의와 지시문만 갖는다.
"""

import re
from datetime import datetime, timezone

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from src.llm import get_llm
from src.schema import TECHS, Claim, Gap, WorkerMeta
from src.settings import settings

# ── 0단계 계약: 공통 함수 (코드 검토 D4~D6) ──────────────────────────────────
# 에이전트 4개·report 에 같은 정규식·Gap 파서·trace 조립이 따로 있다. 여기에 하나로 두고,
# 각 레인이 자기 파일의 사본을 이것으로 바꾼다(B: 에이전트, C: report, A: graph).
# 이 파일은 0단계 이후 담당 B 소유다.

CITATION_RE = re.compile(r"\[([0-9a-f]{12})\]")
_GAP_LINE_RE = re.compile(r"근거\s*공백\s*:\s*(.+)\s*$", re.MULTILINE)
# 모델이 `[ id ]`·`[id, id]`·`[id; id]` 로도 인용한다. CITATION_RE 가 이를 놓치면 인용 0건이 되어
# 근거가 있는 응답 전체가 failed 로 버려진다(20261007 실행의 maturity·TurboQuant).
_LOOSE_CITATION_RE = re.compile(r"\[\s*([0-9a-f]{12}(?:\s*[,;]\s*[0-9a-f]{12})*)\s*\]")


def normalize_citations(text: str) -> str:
    """느슨한 인용 표기를 `[id][id]` 로 맞춘다."""
    return _LOOSE_CITATION_RE.sub(
        lambda m: "".join(f"[{c}]" for c in re.split(r"\s*[,;]\s*", m.group(1))), text or "")


def split_by_tech(text: str) -> dict[str, str]:
    """본문을 기술별 절로 나눈다. 둘 다 찾지 못하면 빈 dict.

    모델마다 제목 모양이 다르다(`1. TurboQuant`, `## TurboQuant`, `### 2. ITME TRL 평가`). 줄 머리에
    기술명이 단어로 오는 줄을 절 시작으로 본다. `TurboQuant는 …` 같은 본문 줄은 단어 경계가 없어 걸리지 않는다.
    """
    starts = {}
    for tech in TECHS:
        match = re.search(rf"^[#>*\s]*(?:\d+[.)]\s*)?\**{tech}\b", text or "", re.MULTILINE | re.IGNORECASE)
        if match:
            starts[tech] = match.start()
    if len(starts) < len(TECHS):
        return {}
    order = sorted(starts, key=starts.get)
    ends = [starts[t] for t in order[1:]] + [len(text)]
    return {tech: text[starts[tech]:end].strip() for tech, end in zip(order, ends)}


def event(node: str, status: str = "ok", attempt: int = 1, **fields) -> dict:
    """trace 한 줄. 노드·상태·시각·개수 같은 가벼운 값만 넣는다(본문·근거 금지)."""
    return dict(node=node, status=status, attempt=attempt,
                timestamp=datetime.now(timezone.utc).isoformat(), **fields)


def gaps_from_text(text: str, role: str, reason: str, kind: str = "evidence_gap",
                   techs: list[str] = TECHS) -> list[Gap]:
    """본문 마지막의 `근거 공백: 항목1 | 항목2` 를 Gap 으로 바꾼다.

    항목에 한 기술 이름만 나오면 그 기술, 둘 다 나오거나 없으면 both 로 둔다.
    techs(Task 대상 기술) 밖 기술의 항목은 버리고, 한 기술만 맡았으면 both 대신 그 기술로 둔다.
    """
    matches = _GAP_LINE_RE.findall(text or "")
    if not matches:
        return []
    payload = matches[-1].strip()
    if payload in ("", "없음", "-", "None"):
        return []
    gaps = []
    for item in (g.strip() for g in payload.split("|") if g.strip()):
        lower = item.lower()
        technology = "both"
        if "turboquant" in lower and "itme" not in lower:
            technology = "TurboQuant"
        elif "itme" in lower and "turboquant" not in lower:
            technology = "ITME"
        if technology == "both":
            technology = claim_technology(techs)
        elif technology not in techs:
            continue
        gaps.append(Gap(role=role, technology=technology, item=item, reason=reason, kind=kind))
    return gaps


def claim_body(text: str) -> str:
    """본문에서 `근거 공백:` 줄을 뺀다. 그 줄은 gaps_from_text 가 Gap 으로 따로 남기므로 Claim 본문이 아니다."""
    return _GAP_LINE_RE.sub("", text or "").strip()


# ── Task 입력 · 조사 이력 (계획서 §4, §11-4 B) ──────────────────────────────────

def task_for(state, default_queries: list) -> tuple[list[str], list, str]:
    """(대상 기술, 질의, 지시문 덧붙임). Task 가 없으면(research·기존 그래프) 기본값.

    덧붙임에 focus·rationale 이 들어가 프롬프트가 달라지므로 LLM 캐시가 이전 라운드 응답을
    재생하지 않는다.
    """
    task = state.get("task")
    if not task:
        return list(TECHS), list(default_queries), ""
    note = (f"\n\n[이번 조사 범위]\n대상 기술: {', '.join(task['technologies'])} "
            f"(다른 기술은 쓰지 않는다)\n조사 초점: {task['focus']}")
    if task.get("rationale"):
        note += f"\n배경: {task['rationale']}"
    # Task 질의는 기본 질의에 더한다. 대체하면 계획 단계의 짧은 질의("TurboQuant 데이터센터 적용")만으로
    # 검색해 핵심 청크(LongBench 등)를 놓치고 본문이 "미확인" 나열이 됐다(20261007 실행 domain).
    queries = list(dict.fromkeys([*default_queries, *task["queries"]]))
    return list(task["technologies"]), queries, note


def retrieve(search, pairs, top_k: int, found: dict, attempted: list) -> None:
    """(질의, 기술) 마다 검색해 found(chunk_id → 청크, 첫 등장 순)와 attempted 를 채운다."""
    for query, technology in pairs:
        attempted.append(query)
        for chunk in search.invoke({"query": query, "technology": technology, "top_k": top_k}):
            if chunk.get("chunk_id"):
                found.setdefault(chunk["chunk_id"], chunk)


def worker_meta(found: dict, attempted: list, techs: list[str]) -> dict:
    """schema.WorkerMeta. retrieved 는 대상 기술에 해당하는 유사도 τ 이상 청크 수.

    τ 보정 전(null)이면 점수로 거르지 않는다. 다른 기술 청크는 세지 않는다 — "대상 기술을
    조사해 찾은 근거 수" 여야 evidence_gap 판정에 쓸 수 있다. 배경 검색 청크는 호출자가 빼고 넘긴다.
    """
    tau = (settings().get("orchestrator") or {}).get("split", {}).get("tau")
    retrieved = sum(1 for c in found.values()
                    if set(c.get("applies_to", [])) & set(techs)
                    and (tau is None or c.get("cosine_score", 0) >= tau))
    return WorkerMeta(attempted_queries=list(dict.fromkeys(attempted)), retrieved=retrieved).model_dump()


def invalid_gaps(role: str, techs: list[str], invalid: list[str]) -> list[Gap]:
    """근거에 없는 인용 ID 가 있으면 대상 기술마다 invalid_evidence.

    인용 위조로 failed 가 된 칸을 "조사했으나 근거 없음"(evidence_gap) 과 구분해,
    커버리지 통과 근거가 되지 못하게 한다 (계획서 §3, §7-1).
    """
    if not invalid:
        return []
    return [Gap(role=role, technology=tech, item=f"{tech} 인용 무효",
                reason=f"검색 결과에 없는 인용 ID {len(set(invalid))}개", kind="invalid_evidence")
            for tech in techs]


_TAG = {"TurboQuant": "tq", "ITME": "itme"}


def section_claims(role: str, sections: dict, techs: list[str], cited: set, run_id: str):
    """기술별 절 → (Claim, Gap). 대상 기술만 만든다. 인용이 없는 절은 Claim 이 아니라 Gap (§6)."""
    claims, gaps = [], []
    for tech in techs:
        text = sections.get(tech, "")
        ids = list(dict.fromkeys(c for c in CITATION_RE.findall(text) if c in cited))
        if ids:
            claims.append(Claim(claim_id=f"claim_{role}_{_TAG[tech]}_{run_id[:8]}", text=text,
                                technology=tech, kind="fact", evidence_ids=ids))
        else:
            gaps.append(Gap(role=role, technology=tech, item=f"{tech} 절 인용 근거 미확보",
                            reason="본문 해당 절에 유효한 인용 ID 가 없다"))
    return claims, gaps


def claim_technology(techs: list[str], default: str = "both") -> str:
    """한 기술만 맡은 Task 면 그 기술, 아니면 default."""
    return techs[0] if len(techs) == 1 else default

GROUND_RULES = """공통 규칙:
- 아래 <document> 근거에 있는 내용만 쓴다. 없는 내용을 추론으로 채우지 않는다.
- 근거가 없는 항목은 "미확인" 이라고 쓰고, 그 항목을 마지막 "근거 공백" 목록에 적는다.
- 인용은 대괄호 ID 로 문장 끝에 붙인다. 예: ... 이다 [a1b2c3d4e5f6]
- 특정 기술을 승자로 선정하거나 우열을 판정하지 않는다.
- 서로 다른 실험의 수치를 직접 비교하지 않는다. 비교 대상과 실험 조건을 함께 적는다.
- 성능 향상뿐 아니라 잔여 비용(정확도 손실, 전송 지연, 시스템 복잡도)도 함께 보고한다.

마지막 줄은 반드시 다음 형식으로 끝낸다:
근거 공백: 항목1 | 항목2      (없으면 "근거 공백: 없음")"""

_TEMPLATE = PromptTemplate.from_template(
    "{instruction}\n\n{rules}\n\n#평가 도메인:\n{domain}\n\n#근거:\n{context}\n\n#작성:"
)


NO_CITATION_RETRY = """
[재작성 지시]
이전 응답에 인용 ID 가 하나도 없었다. 근거로 쓴 문장마다 끝에 [12자리 ID] 를 붙여 다시 작성한다.
여러 근거는 [a1b2c3d4e5f6][b2c3d4e5f6a1] 처럼 대괄호를 따로 쓴다."""


def run_node(instruction: str, domain: str, context: str) -> str:
    """인용 표기를 정규화한다. 인용이 하나도 없으면 한 번만 다시 쓰게 한다.

    인용 없는 응답은 status=failed 로 Claim 이 전부 버려진다. 근거 문서가 있는데도 모델이 인용을
    빠뜨린 경우(20261007 실행의 market)를 형식 문제로 보고 한 번 수리한다.
    """
    chain = _TEMPLATE | get_llm() | StrOutputParser()
    inputs = {"instruction": instruction, "rules": GROUND_RULES, "domain": domain, "context": context}
    text = normalize_citations(chain.invoke(inputs))
    if not CITATION_RE.search(text):
        text = normalize_citations(chain.invoke(inputs | {"instruction": instruction + NO_CITATION_RETRY}))
    return text
