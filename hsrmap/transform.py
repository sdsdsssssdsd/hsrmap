from __future__ import annotations

TRANSFORM_VERSION = "hoyolab_hsr_origin_translation_v1"


def source_to_raster(x_pos: float, y_pos: float, origin_x: float, origin_y: float) -> tuple[float, float]:
    return (float(x_pos) + float(origin_x), float(y_pos) + float(origin_y))


def transform_invariant_ok(x_pos: float, y_pos: float, raster_x: float, raster_y: float, origin_x: float, origin_y: float) -> bool:
    return (
        abs(float(raster_x) - float(x_pos) - float(origin_x)) < 1e-9
        and abs(float(raster_y) - float(y_pos) - float(origin_y)) < 1e-9
    )
