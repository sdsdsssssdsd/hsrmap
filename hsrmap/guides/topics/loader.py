from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES = Path(__file__).with_name("profiles")


@lru_cache(maxsize=1)
def _all() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not PROFILES.exists():
        return out
    for path in sorted(PROFILES.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key = str(payload.get("topic_key") or path.stem)
        payload["topic_key"] = key
        out[key] = payload
    return out


def list_topics(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    items = list(_all().values())
    if enabled_only:
        items = [item for item in items if item.get("enabled", True)]
    return sorted(items, key=lambda item: (-int(item.get("priority") or 0), item["topic_key"]))


def get_topic(topic_key: str) -> dict[str, Any]:
    key = topic_key.replace("-", "_")
    hit = _all().get(key)
    if hit is None:
        raise KeyError(topic_key)
    return hit
