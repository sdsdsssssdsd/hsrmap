from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Transform:
    """Official HoYoLAB HSR CRS at zoom 0.

    Bundle: project(latlng) = Point(lng + origin_x, lat + origin_y)
    Marker: L.marker([y_pos, x_pos]) so lat=y_pos, lng=x_pos
    Transformation(1, 0, 1, 0): no Y flip.
    padding is only used for maxBounds, not marker placement.
    """

    map_id: Any = None
    origin_x: float = 0.0
    origin_y: float = 0.0
    canvas_width: float = 0.0
    canvas_height: float = 0.0

    @classmethod
    def from_map_detail(cls, detail: dict[str, Any], map_id: Any = None) -> "Transform":
        origin = detail.get("origin") or [0, 0]
        total = detail.get("total_size") or [0, 0]
        return cls(
            map_id=map_id,
            origin_x=float(origin[0]),
            origin_y=float(origin[1]),
            canvas_width=float(total[0]),
            canvas_height=float(total[1]),
        )

    @property
    def transform(self) -> dict[str, Any]:
        return {
            "type": "affine",
            "scale_x": 1.0,
            "scale_y": 1.0,
            "offset_x": self.origin_x,
            "offset_y": self.origin_y,
            "flip_x": False,
            "flip_y": False,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "source": "hoyolab-hsr",
            "transform": self.transform,
            "source_origin": [self.origin_x, self.origin_y],
            "notes": (
                "Official bundle CRS: raster_x = x_pos + origin_x; "
                "raster_y = y_pos + origin_y. Leaflet marker is [y_pos, x_pos]. "
                "padding is not part of the point transform."
            ),
        }


def source_to_raster(x_pos: float, y_pos: float, transform: Transform) -> tuple[float, float]:
    return (float(x_pos) + transform.origin_x, float(y_pos) + transform.origin_y)


def raster_to_source(x: float, y: float, transform: Transform) -> tuple[float, float]:
    return (float(x) - transform.origin_x, float(y) - transform.origin_y)
