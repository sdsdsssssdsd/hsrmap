from __future__ import annotations

from typing import Any

from hsrmap.guides.matching.inset import read_inset_anchor
from hsrmap.guides.matching.spatial import parse_spatial_anchor

BANNED = {"4.2版本", "4.2", "版本", "祈鸢", "作者"}


def read_region(
    image_bytes: bytes | None,
    *,
    mime: str = "image/png",
    sha256: str = "",
    provider: Any,
    whitelist: list[str] | None = None,
    alt: str = "",
    src: str = "",
    nearby: str = "",
) -> dict[str, Any]:
    raw = provider.read_region(
        image_bytes=image_bytes or b"",
        mime=mime,
        sha256=sha256,
        whitelist=whitelist or [],
        hint=nearby or alt,
        alt=alt,
        src=src,
        nearby=nearby,
    )
    name = raw.get("map_name_raw")
    if name in (None, "", "null"):
        name = None
    else:
        name = str(name).strip()
    if name in BANNED:
        name = None
    visible = [str(item) for item in (raw.get("visible_text") or []) if item]
    if name and whitelist and name not in whitelist and not any(name in item or item in name for item in whitelist):
        name = None
    blobs = [raw.get("spatial_anchor"), alt, *visible]
    anchor = next((parse_spatial_anchor(item) for item in blobs if parse_spatial_anchor(item)), None)
    if not anchor:
        anchor = read_inset_anchor(image_bytes)
    return {
        "map_name_raw": name,
        "visible_text": visible,
        "article_ordinal": raw.get("article_ordinal"),
        "ordinal_raw": raw.get("ordinal_raw"),
        "spatial_anchor": anchor,
        "instruction_text": [str(item) for item in (raw.get("instruction_text") or []) if item],
        "grounded": bool(name) and bool(raw.get("grounded", True)),
    }
