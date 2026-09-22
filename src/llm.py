"""LLM 팩토리 — 담당: R1

모델은 config/settings.yaml 기본값을 쓰되 .env 의 LLM_MODEL 이 우선한다.
gpt-5.6-luna 가 팀 계정에서 호출되지 않거나 structured output(tool calling)을
지원하지 않으면 .env 한 줄로 fallback 으로 바꾼다 — synthesis 의 conflicts
최소 1건 강제가 structured output 에 달려 있다.
"""

import os

from langchain.chat_models import init_chat_model

from src.settings import settings


def model_name() -> str:
    return os.getenv("LLM_MODEL") or settings()["llm"]["model"]


def get_llm(temperature: float | None = None):
    """seed 를 함께 넘긴다. temperature=0 은 같은 출력을 보장하지 않는다 — 배치 구성이
    달라지면 문장이 달라진다. 제공자 측 best-effort 라 완전 보장은 아니다."""
    cfg = settings()["llm"]
    extra = {"seed": cfg["seed"]} if cfg.get("seed") is not None else {}
    return init_chat_model(
        model_name(),
        model_provider="openai",
        temperature=cfg["temperature"] if temperature is None else temperature,
        **extra,
    )
