"""원문 로더 — 담당: R2

data/manifest.json 에 이미 수집된 원문을 (source_id, url, version) 으로 찾아
파일 경로·버전·페이지 수·해시·적재 상태를 돌려준다. 실제 다운로드·해시·쪽수
계산은 scripts/prepare_sources.py 가 하고, 이 모듈은 그 결과를 조회만 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path("data/manifest.json")


def _empty(source_id: str, version: str | None, status: str) -> dict:
    return {
        "source_id": source_id,
        "file_path": None,
        "version": version,
        "pages": 0,
        "sha256": None,
        "status": status,
    }


def load_paper(source_id: str, url: str | None = None, version: str | None = None) -> dict:
    """source_id 로 매니페스트 항목을 찾아 적재 상태를 돌려준다.

    url/version 이 주어지면 매니페스트에 기록된 값과 비교한다. 값이 다르면
    sources.json 이 바뀐 뒤 아직 재수집(prepare_sources.py)을 하지 않은 것이므로
    status 를 "stale" 로 표시한다 — 호출자가 오래된 파일을 새 것으로 오인하지
    않게 하기 위함이다.

    반환:
        {
            "source_id": str,
            "file_path": str | None,  # 저장소 루트 기준 상대경로
            "version": str | None,
            "pages": int | float,     # PDF 실제 쪽수 또는 웹 A4 환산 쪽수 (pages_counted)
            "sha256": str | None,
            "status": "loaded" | "stale" | "missing",
        }
    """
    if not MANIFEST.exists():
        return _empty(source_id, version, "missing")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next((e for e in manifest.get("sources", []) if e["id"] == source_id), None)
    if entry is None:
        return _empty(source_id, version, "missing")

    file_path = entry.get("local_path") or entry.get("text_path")
    if not file_path or not Path(file_path).exists():
        return {
            "source_id": source_id,
            "file_path": file_path,
            "version": entry.get("version"),
            "pages": entry.get("pages_counted", 0),
            "sha256": entry.get("sha256"),
            "status": "missing",
        }

    status = "loaded"
    if url and entry.get("url") != url:
        status = "stale"
    if version and entry.get("version") and entry.get("version") != version:
        status = "stale"

    return {
        "source_id": source_id,
        "file_path": file_path,
        "version": entry.get("version"),
        "pages": entry.get("pages_counted", 0),
        "sha256": entry.get("sha256"),
        "status": status,
    }
