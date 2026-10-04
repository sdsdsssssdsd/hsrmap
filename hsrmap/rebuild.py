from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.normalize import flatten_label_nodes, flatten_map_nodes, normalize_map_info, normalize_point
from hsrmap.render_probe import refresh_render_probes


def rebuild_from_raw(raw_dir: Path, db_path: Path) -> CoreDatabase:
    raw_dir = Path(raw_dir)
    db = CoreDatabase(db_path)
    tree = json.loads((raw_dir / "map_tree.json").read_text(encoding="utf-8"))
    labels = json.loads((raw_dir / "label_tree.json").read_text(encoding="utf-8"))
    map_nodes = flatten_map_nodes((tree.get("data") or {}).get("tree") or [])
    db.insert_map_nodes(map_nodes)
    label_nodes, bindings = flatten_label_nodes((labels.get("data") or {}).get("tree") or [])
    db.insert_label_nodes(label_nodes)
    db.insert_semantic_bindings(bindings)
    for path in sorted((raw_dir / "map_info").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        db.insert_map(normalize_map_info(payload), map_info_sha256=digest)
    for path in sorted((raw_dir / "point_list").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        map_row = db.map_by_source(path.stem)
        if map_row is None:
            raise ValueError(f"point list {path.name} has no map")
        points = [
            normalize_point(
                point,
                map_source_id=path.stem,
                origin_x=float(map_row["origin_x"]),
                origin_y=float(map_row["origin_y"]),
            )
            for point in ((payload.get("data") or {}).get("point_list") or [])
        ]
        db.insert_points(points)
    #: map/info 都落库之后再判可渲染（a1-8-1 §九）：证据就是刚写进去的 maps + map_fragments。
    known_ids = {n["source_id"] for n in map_nodes}
    known_ids.update(str(row["source_id"]) for row in db.conn.execute("SELECT source_id FROM maps"))
    refresh_render_probes(db.conn, sorted(known_ids))
    return db
