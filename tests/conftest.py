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


@pytest.fixture(autouse=True)
def no_probe_search(monkeypatch):
    """사전 조사가 실제 색인·임베딩 모델을 부르지 않게 막는다 — 사전 조사 없이 기본 개수로 계획한다.

    설정의 τ 가 보정돼 있어도 CI 에는 원문·모델이 없다. 사전 조사를 시험하는 테스트는
    `planner.probe` 를 덮어쓰거나 `probe(state, search=...)` 로 검색을 넘긴다.
    """
    from src.orchestrator import planner

    def blocked(*args, **kwargs):
        raise RuntimeError("테스트에서는 사전 조사 검색을 부르지 않는다")
    monkeypatch.setattr(planner, "PROBE_SEARCH", blocked)


@pytest.fixture(autouse=True)
def uncalibrated_tau(monkeypatch):
    """보정된 τ(설정값)가 단위 테스트 픽스처의 유사도를 가르지 않게 한다.

    `common.worker_meta` 의 retrieved 집계와 계획의 사전 조사가 모두 설정의 τ 를 읽는다.
    픽스처 점수는 보정 코퍼스와 무관하므로 테스트에서는 τ 를 비운다(보정 전과 같은 동작).
    τ 를 시험하는 테스트는 `planner._cfg` 나 `common.settings` 를 직접 덮어쓴다.
    """
    from src.settings import settings

    monkeypatch.setitem(settings()["orchestrator"]["split"], "tau", None)


@pytest.fixture(autouse=True)
def no_judge_llm(monkeypatch):
    """품질 평가 Judge 가 실제 LLM 을 부르지 않게 막는다 — 막히면 Judge 실패(partial)로 처리된다.

    통합 테스트는 `run_config.quality_judge=False`(코드 기반 평가)로 돌리고, Judge 경로를 시험하는
    테스트는 `evaluator.judge_llm` 을 대역 모델로 다시 덮어쓴다.
    """
    from src.orchestrator import evaluator

    def blocked():
        raise RuntimeError("테스트에서는 Judge LLM 을 부르지 않는다")
    monkeypatch.setattr(evaluator, "judge_llm", blocked)
