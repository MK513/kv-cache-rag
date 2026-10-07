"""평가 노드 공통 헬퍼 — 담당: R1 (계약 소유)

5개 평가 노드가 모두 `검색 → 근거 포맷 → LLM → {text, citations, gaps}` 로
같은 모양이라 여기 한 번만 쓴다. 각 노드는 질의와 지시문만 갖는다.
"""

import re
from datetime import datetime, timezone

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from src.llm import get_llm
from src.schema import Gap

# ── 0단계 계약: 공통 함수 (코드 검토 D4~D6) ──────────────────────────────────
# 에이전트 4개·report 에 같은 정규식·Gap 파서·trace 조립이 따로 있다. 여기에 하나로 두고,
# 각 레인이 자기 파일의 사본을 이것으로 바꾼다(B: 에이전트, C: report, A: graph).
# 이 파일은 0단계 이후 담당 B 소유다.

CITATION_RE = re.compile(r"\[([0-9a-f]{12})\]")
_GAP_LINE_RE = re.compile(r"근거\s*공백\s*:\s*(.+)\s*$", re.MULTILINE)


def event(node: str, status: str = "ok", attempt: int = 1, **fields) -> dict:
    """trace 한 줄. 노드·상태·시각·개수 같은 가벼운 값만 넣는다(본문·근거 금지)."""
    return dict(node=node, status=status, attempt=attempt,
                timestamp=datetime.now(timezone.utc).isoformat(), **fields)


def gaps_from_text(text: str, role: str, reason: str, kind: str = "evidence_gap") -> list[Gap]:
    """본문 마지막의 `근거 공백: 항목1 | 항목2` 를 Gap 으로 바꾼다.

    항목에 한 기술 이름만 나오면 그 기술, 둘 다 나오거나 없으면 both 로 둔다.
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
        gaps.append(Gap(role=role, technology=technology, item=item, reason=reason, kind=kind))
    return gaps

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


def run_node(instruction: str, domain: str, context: str) -> str:
    chain = _TEMPLATE | get_llm() | StrOutputParser()
    return chain.invoke({"instruction": instruction, "rules": GROUND_RULES,
                         "domain": domain, "context": context})
