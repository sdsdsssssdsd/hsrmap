from __future__ import annotations

from html import escape
from typing import Any


def render_layout(layout: dict[str, Any] | None) -> str:
    body = layout or {}
    profile = str(body.get("profile") or "puzzle_steps_v1")
    blocks = list(body.get("blocks") or [])
    if profile == "collection_route_v1":
        title = "收集路线"
        items = "".join(
            f"<li>地点 {index}：{escape(str(block.get('text') or ''))}</li>"
            for index, block in enumerate(blocks, start=1)
        )
        return f"<section data-profile='{escape(profile)}'><h3>{title}</h3><ol>{items}</ol></section>"
    if profile == "challenge_v1":
        title = "挑战步骤"
        items = "".join(
            f"<li>挑战 {index}：{escape(str(block.get('text') or ''))}</li>"
            for index, block in enumerate(blocks, start=1)
        )
        return f"<section data-profile='{escape(profile)}'><h3>{title}</h3><ol>{items}</ol></section>"
    items = "".join(
        f"<li>步骤 {index}：{escape(str(block.get('text') or ''))}</li>"
        for index, block in enumerate(blocks, start=1)
    )
    return f"<section data-profile='{escape(profile)}'><h3>解谜步骤</h3><ol>{items}</ol></section>"
