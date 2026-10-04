from __future__ import annotations

from typing import Any


def slice_long_image(blob: bytes | None, *, ratio: float = 3.0) -> dict[str, Any]:
    data = blob or b""
    width, height = _size(data)
    if width <= 0 or height <= 0 or height / width <= ratio:
        return {"sliced": False, "original": data, "segments": []}
    window = max(int(width * ratio), 1)
    segments = []
    y = 0
    while y < height:
        y1 = min(height, y + window)
        segments.append({"bbox": {"x0": 0, "y0": y, "x1": width, "y1": y1}})
        if y1 >= height:
            break
        y = y1
    return {"sliced": True, "original": data, "segments": segments}


def _size(blob: bytes | None) -> tuple[int, int]:
    if not blob:
        return 0, 0
    if blob.startswith(b"P5") or blob.startswith(b"P6"):
        return _pgm_size(blob)
    try:
        from PIL import Image
        from io import BytesIO

        image = Image.open(BytesIO(blob))
        return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _pgm_size(blob: bytes) -> tuple[int, int]:
    try:
        header, _body = blob.split(b"\n255\n", 1)
        parts = header.split()
        return int(parts[-2]), int(parts[-1])
    except Exception:
        return 0, 0
