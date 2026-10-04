from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any


def build_image_variants(blob: bytes, *, sha256: str, cache_root: Path) -> dict[str, Any]:
    root = Path(cache_root)
    image = _open(blob)
    variants: dict[str, bytes] = {}
    for name, size in (("thumbnail_384", 384), ("vision_preview_1024", 1024)):
        dest = root / name / sha256[:2] / f"{sha256[2:]}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy = image.copy()
        copy.thumbnail((size, size))
        copy.save(dest, format="PNG")
        variants[name] = dest.read_bytes()
    hud = image.crop((0, 0, image.width, max(1, image.height // 5)))
    hud_path = root / "hud_crop" / sha256[:2] / f"{sha256[2:]}.png"
    hud_path.parent.mkdir(parents=True, exist_ok=True)
    hud.save(hud_path, format="PNG")
    variants["hud_crop"] = hud_path.read_bytes()
    return {"original_kept": True, "variants": variants}


def _open(blob: bytes):
    from PIL import Image

    if blob.startswith(b"P5"):
        header, body = blob.split(b"\n255\n", 1)
        parts = header.split()
        width, height = int(parts[-2]), int(parts[-1])
        return Image.frombytes("L", (width, height), body).convert("RGB")
    return Image.open(BytesIO(blob)).convert("RGB")
