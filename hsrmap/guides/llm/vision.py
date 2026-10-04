from __future__ import annotations

from typing import Any

ALLOWED = {"map_overview", "puzzle_step", "ui", "other"}


def classify_image(hint: str, client: Any, *, model: str = "deepseek-vision") -> str:
    raw = client.complete(hint, prompt_version="image_classify_v1", model=model)
    label = raw.strip() if isinstance(raw, str) else str(raw.get("label") or raw)
    if label not in ALLOWED:
        return "other"
    return label
