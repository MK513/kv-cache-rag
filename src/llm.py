"""LLM 팩토리 — 담당: R1

모델은 config/settings.yaml 기본값을 쓰되 .env 의 LLM_MODEL 이 우선한다.

**재현성은 모델이 아니라 캐시가 담당한다.** 기본 모델 `gpt-4.1-mini` 는 temperature=0·seed 를
전달받지만 OpenAI 는 seed 를 best-effort 로만 지킨다. 이전 기본값이던 추론형 모델
(`gpt-5.6-luna`)은 langchain 이 temperature 를 아예 보내지 않아, 실측에서 인용 근거가 완전히
같은데도 본문이 74% 달랐다. 그래서 프롬프트 전문을 키로 응답을 캐시한다 — 같은 코퍼스·같은
지시문이면 같은 응답이 나온다. 모델의 결정성에 기대지 않는다.

캐시 적중은 "이번 실행에서 모델이 새로 판단하지 않았다" 는 뜻이라 기록해서 보고서에
밝힌다. 설계서 §6 — 실제 비용은 토큰 사용량과 실행 기록으로 확인한다.
"""

import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.globals import set_llm_cache

from src.settings import settings

CACHE_PATH = ".cache/llm.sqlite"

_cache = None
_effective: dict = {}


class _CountingCache:
    """적중·미적중을 세는 SQLite 캐시. langchain 캐시 인터페이스는 lookup/update 둘뿐이다."""

    def __init__(self, database_path: str):
        from langchain_community.cache import SQLiteCache

        self._inner = SQLiteCache(database_path=database_path)
        self.hits = 0
        self.misses = 0

    def lookup(self, prompt: str, llm_string: str):
        found = self._inner.lookup(prompt, llm_string)
        if found is None:
            self.misses += 1
        else:
            self.hits += 1
        return found

    def update(self, prompt: str, llm_string: str, return_val) -> None:
        self._inner.update(prompt, llm_string, return_val)

    def clear(self, **kwargs) -> None:
        self._inner.clear(**kwargs)


def enable_cache(path: str = CACHE_PATH):
    """프롬프트 전문을 키로 응답을 재사용한다. 프롬프트가 바뀌면 자동으로 새로 생성한다."""
    global _cache
    if _cache is None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _cache = _CountingCache(path)
        set_llm_cache(_cache)
    return _cache


def model_name() -> str:
    return os.getenv("LLM_MODEL") or settings()["llm"]["model"]


def judge_model_name() -> str:
    """품질 평가 Judge 모델. `llm.judge_model` 이 비어 있으면 생성 모델과 같다."""
    return settings()["llm"].get("judge_model") or model_name()


def _chat_model(name: str, temperature: float | None):
    cfg = settings()["llm"]
    extra = {"seed": cfg["seed"]} if cfg.get("seed") is not None else {}
    # 호출 하나가 끝나지 않으면 step 상한으로는 끊을 수 없다. timeout 과 SDK 내부 재시도
    # 횟수를 명시해 실제 호출 횟수를 설명할 수 있게 한다(그래프 재시도와 별개).
    for key in ("timeout", "max_retries"):
        if cfg.get(key) is not None:
            extra[key] = cfg[key]
    return init_chat_model(
        name,
        model_provider="openai",
        temperature=cfg["temperature"] if temperature is None else temperature,
        **extra,
    )


def get_llm(temperature: float | None = None):
    """설정값이 아니라 **실제로 적용된** 파라미터를 기록해 둔다.

    langchain 은 모델에 따라 temperature 를 조용히 버린다. 설정 파일의 값을 그대로
    run.json 에 적으면 거짓말이 된다(§6 — 온도 등 지원되는 설정도 저장한다).
    """
    llm = _chat_model(model_name(), temperature)
    _effective.update(
        model=model_name(),
        temperature=getattr(llm, "temperature", None),
        seed=getattr(llm, "seed", None),
    )
    return llm


def judge_llm():
    """품질 평가(Judge) 전용. 실효 모델명을 run.json 에 따로 남긴다."""
    llm = _chat_model(judge_model_name(), None)
    _effective.update(judge_model=judge_model_name())
    return llm


def llm_report() -> dict:
    """실효 파라미터와 캐시 적중. LLM 을 한 번도 부르지 않았으면 빈 dict 다."""
    report = dict(_effective)
    if _cache is not None and (_cache.hits or _cache.misses):
        report |= {"cache_hits": _cache.hits, "cache_misses": _cache.misses}
    return report
