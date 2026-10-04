from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image


def read_inset_anchor(image_bytes: bytes | None) -> str | None:
    if not image_bytes:
        return None
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None
    panel = _map_panel(image)
    if panel is None:
        return None
    pin = _pin_xy(panel)
    box = _ink_bbox(panel)
    if pin is None or box is None:
        return None
    left, top, right, bottom = box
    if right - left < 8 or bottom - top < 8:
        return None
    mid_x = (left + right) / 2
    mid_y = (top + bottom) / 2
    x_dir = "右" if pin[0] > mid_x else "左"
    y_dir = "下" if pin[1] > mid_y else "上"
    return f"{x_dir}{y_dir}角"


def _map_panel(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    pixels = image.load()

    def mostly_white(y: int) -> bool:
        step = max(1, width // 80)
        white = 0
        samples = 0
        for x in range(0, width, step):
            red, green, blue = pixels[x, y]
            samples += 1
            if red > 245 and green > 245 and blue > 245:
                white += 1
        return samples > 0 and white / samples > 0.85

    top = 0
    while top < height and mostly_white(top):
        top += 1
    bottom = top
    while bottom < height and not mostly_white(bottom):
        bottom += 1
    if bottom - top < 24:
        return None
    return image.crop((0, top, max(40, width // 2), bottom))


def _pin_xy(panel: Image.Image) -> tuple[float, float] | None:
    arr = _as_array(panel)
    if arr is None:
        return None
    red = arr[:, :, 0].astype("int32")
    green = arr[:, :, 1].astype("int32")
    blue = arr[:, :, 2].astype("int32")
    yellow = (red > 180) & (green > 140) & (blue < 140) & ((red + green) > (2 * blue + 30))
    if not yellow.any():
        return None
    ys, xs = _where(yellow)
    return float(xs.mean()), float(ys.mean())


def _ink_bbox(panel: Image.Image) -> tuple[int, int, int, int] | None:
    arr = _as_array(panel)
    if arr is None:
        return None
    tone = arr[:, :, 0].astype("int32") + arr[:, :, 1].astype("int32") + arr[:, :, 2].astype("int32")
    dark = tone < 210
    if not dark.any():
        return None
    ys, xs = _where(dark)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _as_array(image: Image.Image):
    try:
        import numpy as np
    except ImportError:
        return None
    return np.asarray(image)


def _where(mask: Any):
    import numpy as np

    return np.where(mask)
