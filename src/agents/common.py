"""평가 노드 공통 헬퍼 — 담당: R1 (계약 소유)

5개 평가 노드가 모두 `검색 → 근거 포맷 → LLM → {text, citations, gaps}` 로
같은 모양이라 여기 한 번만 쓴다. 각 노드는 질의와 지시문만 갖는다.
"""

import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from src.llm import get_llm

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


def extract_citations(text: str, valid_ids: set[str]) -> tuple[list[str], list[str]]:
    """본문의 [id] 를 (실재 ID, 존재하지 않는 ID) 로 가른다."""
    found = set(re.findall(r"\[([0-9a-f]{12})\]", text))
    return sorted(found & valid_ids), sorted(found - valid_ids)


def extract_gaps(text: str) -> list[str]:
    """본문 마지막의 '근거 공백:' 줄을 목록으로 뽑는다."""
    m = re.search(r"근거\s*공백\s*:\s*(.+)\s*$", text, re.MULTILINE)
    if not m or m.group(1).strip() in ("없음", "-"):
        return []
    return [g.strip() for g in m.group(1).split("|") if g.strip()]
