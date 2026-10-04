from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.paths import BASELINE


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_statistics(db: CoreDatabase) -> dict[str, Any]:
    maps = list(db.conn.execute("SELECT * FROM maps"))
    points = list(db.conn.execute("SELECT * FROM points"))
    fragments = list(db.conn.execute("SELECT * FROM map_fragments"))
    labels = list(db.conn.execute("SELECT * FROM label_nodes"))
    frag_counts = Counter(int(m["fragment_count"] or 0) for m in maps)
    sizes = Counter()
    for frag in fragments:
        sizes[f"{frag['source_width']}x{frag['source_height']}"] += 1
    origins = [(float(m["origin_x"]), float(m["origin_y"])) for m in maps]
    inside = outside = 0
    per_map = []
    for m in maps:
        n = int(db.conn.execute("SELECT COUNT(*) AS n FROM points WHERE map_id = ?", (m["id"],)).fetchone()["n"])
        per_map.append(n)
        for p in db.points_for_map(m["id"]):
            x, y = float(p["raster_x"]), float(p["raster_y"])
            if 0 <= x <= float(m["canvas_width"]) and 0 <= y <= float(m["canvas_height"]):
                inside += 1
            else:
                outside += 1
    per_label = {}
    for row in db.conn.execute(
        """
        SELECT l.source_id, l.name, COUNT(*) AS n
        FROM point_labels pl
        JOIN label_nodes l ON l.id = pl.label_id
        GROUP BY l.id
        """
    ):
        per_label[row["source_id"]] = {"name": row["name"], "count": row["n"]}

    def semantic_stats(key: str) -> dict[str, Any]:
        source_id = db.semantic_source_id(key)
        if not source_id:
            return {"resolved": False}
        row = db.conn.execute(
            """
            SELECT COUNT(DISTINCT p.id) AS points, COUNT(DISTINCT p.map_id) AS maps
            FROM points p
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE l.source_id = ?
            """,
            (source_id,),
        ).fetchone()
        maps_rows = db.conn.execute(
            """
            SELECT DISTINCT m.source_id, m.name
            FROM maps m
            JOIN points p ON p.map_id = m.id
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE l.source_id = ?
            """,
            (source_id,),
        ).fetchall()
        return {
            "resolved": True,
            "source_label_id": source_id,
            "point_count": row["points"],
            "map_count": row["maps"],
            "maps": [{"source_id": r["source_id"], "name": r["name"]} for r in maps_rows],
        }

    zero_maps = sum(1 for n in per_map if n == 0)
    return {
        "maps": {
            "raw_tree_nodes": int(db.conn.execute("SELECT COUNT(*) n FROM map_nodes").fetchone()["n"]),
            "folder_nodes": int(db.conn.execute("SELECT COUNT(*) n FROM map_nodes WHERE is_renderable = 0").fetchone()["n"]),
            "renderable_maps": len(maps),
        },
        "labels": {
            "total": len(labels),
            "categories": int(db.conn.execute("SELECT COUNT(*) n FROM label_nodes WHERE is_category = 1").fetchone()["n"]),
            "selectable": int(db.conn.execute("SELECT COUNT(*) n FROM label_nodes WHERE is_selectable = 1").fetchone()["n"]),
        },
        "points": {
            "total": len(points),
            "maps_with_points": sum(1 for n in per_map if n > 0),
            "maps_with_zero_points": zero_maps,
            "per_map_min": min(per_map) if per_map else 0,
            "per_map_max": max(per_map) if per_map else 0,
            "per_map_mean": (sum(per_map) / len(per_map)) if per_map else 0,
            "per_label": per_label,
        },
        "raster": {
            "total_fragments": len(fragments),
            "fragment_count_distribution": dict(sorted(frag_counts.items())),
            "single_fragment_maps": frag_counts.get(1, 0),
            "multi_fragment_maps": sum(v for k, v in frag_counts.items() if k > 1),
            "fragment_size_distribution": dict(sizes),
        },
        "origin": {
            "min_x": min((o[0] for o in origins), default=None),
            "max_x": max((o[0] for o in origins), default=None),
            "min_y": min((o[1] for o in origins), default=None),
            "max_y": max((o[1] for o in origins), default=None),
            "zero": sum(1 for x, y in origins if x == 0 and y == 0),
            "negative": sum(1 for x, y in origins if x < 0 or y < 0),
            "positive": sum(1 for x, y in origins if x > 0 or y > 0),
        },
        "coordinates": {
            "inside": inside,
            "outside": outside,
            "outside_ratio": (outside / max(inside + outside, 1)),
        },
        "floating_grease": semantic_stats("floating_grease_origin_retrace"),
        "floating_grease_notes": semantic_stats("floating_grease_notes"),
        "baseline": BASELINE,
    }
