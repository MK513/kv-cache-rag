"""설정 로더 — 담당: R1

config/settings.yaml 을 읽는 단일 진입점.
(v13 파일 목록에는 없으나, R2·R4 가 top_k·청킹 상수를 R1 의 llm.py 를 거쳐
 가져오는 역참조를 피하려고 분리했다.)
"""

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def settings() -> dict:
    return yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
