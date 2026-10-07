"""품질 평가 테스트 공용 State — 담당 C

계약 픽스처 `state_after_collect.json`(읽기 전용)에 종합 결과를 붙이고 실제 `report` 노드로
보고서·manifest 를 만든다. LLM·PDF 의존성 없이 돈다. 값은 합성 데이터다.
"""

import copy
import json
from pathlib import Path

from pypdf import PdfWriter

FIXTURES = Path(__file__).parent / "fixtures" / "contract"


def collected(tmp_path) -> dict:
    state = json.loads((FIXTURES / "state_after_collect.json").read_text(encoding="utf-8"))
    state = copy.deepcopy(state)
    state["run_config"] = state["run_config"] | {"runs_dir": str(tmp_path), "quality_judge": False,
                                                 "started_at": "2026-10-07T00:00:00+00:00"}
    state["synthesis"] = {
        "agreements": [{"text": "두 기술 모두 KV cache 메모리 병목을 다룬다",
                        "claim_ids": ["r0-maturity-both:claim_maturity_turboquant_contract",
                                      "r0-maturity-both:claim_maturity_itme_contract"]}],
        "conflicts": [{"perspective": "시장성", "why": "채택 근거의 범위가 기술마다 다르다",
                       "claim_ids": ["r0-market-both:claim_market_turboquant_contract"]}],
        "gaps": ["ITME 상용 채택 사례"],
        "combination_hypothesis": "결합 효과는 공개 실측이 없어 가설이다",
        "synthesis_version": 1, "round": 0,
    }
    state.setdefault("claim_flags", {})
    return state


def reported(state: dict) -> dict:
    from src.agents import report

    return state | report.report(state)


def write_pdf(path: Path, pages: int):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        writer.write(f)


def fake_renderer(pages_for):
    """render_pdf 대역. Markdown 내용으로 쪽수를 정한다(압축 단계별 쪽수를 흉내 낸다)."""
    calls = []

    def render(markdown_path, pdf_path):
        text = Path(markdown_path).read_text(encoding="utf-8")
        write_pdf(Path(pdf_path), pages_for(text))
        calls.append(Path(pdf_path))
    render.calls = calls
    return render
