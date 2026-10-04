from __future__ import annotations

from typing import Any, Sequence


def get_raster_bounds(origin_x: float, origin_y: float, width: float, height: float) -> dict[str, list[float]]:
    origin_x = float(origin_x)
    origin_y = float(origin_y)
    width = float(width)
    height = float(height)
    return {
        "south_west": [-origin_y, -origin_x],
        "north_east": [height - origin_y, width - origin_x],
    }


def get_max_bounds(
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    padding: Sequence[Any] | None = None,
) -> dict[str, list[float]]:
    raster = get_raster_bounds(origin_x, origin_y, width, height)
    pad_x = float(padding[0]) if padding else 0.0
    pad_y = float(padding[1]) if padding and len(padding) > 1 else 0.0
    return {
        "south_west": [raster["south_west"][0] - pad_y, raster["south_west"][1] - pad_x],
        "north_east": [raster["north_east"][0] + pad_y, raster["north_east"][1] + pad_x],
    }
