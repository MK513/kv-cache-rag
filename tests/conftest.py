"""테스트가 저장소의 검토 판정 원장을 건드리지 않게 막는다.

`reviews/verdicts.json` 은 사람이 들인 검토 시간이 쌓이는 파일이라 커밋된다.
테스트가 여기에 합성 판정을 올리면 다음 실행이 그걸 진짜 검토로 착각한다.
"""

import pytest

from src.output import review


@pytest.fixture(autouse=True)
def isolated_review_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "LEDGER", tmp_path / "verdicts.json")


@pytest.fixture(autouse=True)
def no_llm_planning(monkeypatch):
    """계획 분해가 실제 LLM 을 부르지 않게 막는다 — 결정적 기본 계획으로 폴백한다.

    분해 자체를 시험하는 테스트는 `planner.get_llm` 을 다시 덮어쓴다.
    """
    from src.orchestrator import planner

    def blocked():
        raise RuntimeError("테스트에서는 계획 LLM 을 부르지 않는다")
    monkeypatch.setattr(planner, "get_llm", blocked)
