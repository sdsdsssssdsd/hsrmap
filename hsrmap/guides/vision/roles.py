from __future__ import annotations

from typing import Any

ALLOWED = {
    "location_map",
    "puzzle_step",
    "route_map",
    "reward",
    "cover",
    "advertisement",
    "unrelated",
    "unknown",
}
WASTE = {"cover", "advertisement", "unrelated"}
USEFUL = {"location_map", "puzzle_step", "route_map"}


def prefilter(*, alt: str = "", src: str = "", size: int = 0) -> dict[str, Any] | None:
    blob = f"{alt} {src}".lower()
    if size and size < 8000:
        return {"role": "unrelated", "confidence": 1.0, "source": "prefilter_tiny"}
    if any(token in blob for token in ("二维码", "qr", "app", "wx.webp", "公众号")):
        return {"role": "unrelated", "confidence": 1.0, "source": "prefilter_qr"}
    if any(
        token in blob
        for token in (
            "广告",
            "advert",
            "novar",
            "new_preview",
            "pe_u_thumb",
            "/upimg/new",
            "related-rec",
            "webgame_oss",
            "webimg13/webgame",
        )
    ):
        return {"role": "advertisement", "confidence": 0.95, "source": "prefilter_ad"}
    return None


def classify_role(
    image_bytes: bytes | None,
    *,
    mime: str = "image/png",
    sha256: str = "",
    alt: str = "",
    src: str = "",
    provider: Any = None,
) -> dict[str, Any]:
    cheap = prefilter(alt=alt, src=src, size=len(image_bytes or b""))
    if cheap:
        return cheap
    if provider is None:
        return {"role": "unknown", "confidence": 0.0, "source": "no_provider"}
    try:
        raw = provider.classify_image(
            hint=f"{alt} {src}",
            image_bytes=image_bytes,
            mime=mime,
            sha256=sha256,
            alt=alt,
            src=src,
        )
    except Exception:
        return {"role": "unknown", "confidence": 0.0, "source": "provider_error"}
    if isinstance(raw, str):
        raw = {"role": raw}
    role = str(raw.get("role") or "unknown")
    if role not in ALLOWED:
        role = "unknown"
    return {"role": role, "confidence": float(raw.get("confidence") or 0), "source": "provider"}
