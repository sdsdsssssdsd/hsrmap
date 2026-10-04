from __future__ import annotations

import random
import urllib.request
from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.inspect_map import compose_map


def golden_point_errors(db: CoreDatabase, golden: list[dict[str, Any]]) -> dict[str, float]:
    errors = []
    for item in golden:
        map_row = db.map_by_source(str(item["source_map_id"]))
        if map_row is None:
            errors.append(999.0)
            continue
        row = db.conn.execute(
            "SELECT raster_x, raster_y FROM points WHERE map_id = ? AND source_id = ?",
            (map_row["id"], str(item["source_point_id"])),
        ).fetchone()
        if row is None:
            errors.append(999.0)
            continue
        dx = float(row["raster_x"]) - float(item["raster_coordinate"]["x"])
        dy = float(row["raster_y"]) - float(item["raster_coordinate"]["y"])
        errors.append((dx * dx + dy * dy) ** 0.5)
    return {
        "count": len(golden),
        "mean": sum(errors) / len(errors) if errors else 999.0,
        "max": max(errors) if errors else 999.0,
        "errors": errors,
    }


def validate_offline(db: CoreDatabase, sample_size: int = 10) -> dict[str, Any]:
    original = urllib.request.urlopen
    hits = {"count": 0}

    def blocked(*_args, **_kwargs):
        hits["count"] += 1
        raise AssertionError("offline validation attempted network access")

    urllib.request.urlopen = blocked  # type: ignore[assignment]
    inspected: list[dict[str, Any]] = []
    try:
        maps = list(db.conn.execute("SELECT * FROM maps ORDER BY source_id"))
        if len(maps) > sample_size:
            maps = random.Random(20261001).sample(maps, sample_size)
        for row in maps:
            image = compose_map(db, row)
            points = db.points_for_map(row["id"])
            inspected.append({
                "map": row["source_id"],
                "name": row["name"],
                "width": image.size[0],
                "height": image.size[1],
                "points": len(points),
            })
        return {"passed": hits["count"] == 0, "network_hits": hits["count"], "inspected": inspected}
    finally:
        urllib.request.urlopen = original
