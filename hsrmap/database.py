from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


#: 核心快照库的 schema 版本（a1-8 十六.7：每个库都要有明确的版本）。
SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS map_nodes (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    parent_source_id TEXT,
    node_type INTEGER,
    name TEXT,
    depth INTEGER,
    sort_order INTEGER,
    is_renderable INTEGER,
    raw_sha256 TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    node_id INTEGER,
    name TEXT,
    canvas_width REAL,
    canvas_height REAL,
    origin_x REAL,
    origin_y REAL,
    padding_json TEXT,
    fragment_count INTEGER,
    map_info_sha256 TEXT,
    coordinate_transform TEXT,
    FOREIGN KEY(node_id) REFERENCES map_nodes(id)
);

CREATE TABLE IF NOT EXISTS map_fragments (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL,
    source_index INTEGER,
    remote_url TEXT,
    asset_sha256 TEXT,
    source_width INTEGER,
    source_height INTEGER,
    position_json TEXT,
    metadata_json TEXT,
    raw_json TEXT,
    FOREIGN KEY(map_id) REFERENCES maps(id)
);

CREATE TABLE IF NOT EXISTS label_nodes (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    parent_source_id TEXT,
    name TEXT,
    is_category INTEGER,
    is_selectable INTEGER,
    sort_order INTEGER,
    icon_remote_url TEXT,
    icon_asset_sha256 TEXT,
    raw_sha256 TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS semantic_labels (
    semantic_key TEXT PRIMARY KEY,
    canonical_name TEXT
);

CREATE TABLE IF NOT EXISTS semantic_label_bindings (
    semantic_key TEXT NOT NULL,
    source_label_id TEXT NOT NULL,
    confidence REAL,
    resolver TEXT,
    PRIMARY KEY(semantic_key, source_label_id),
    FOREIGN KEY(semantic_key) REFERENCES semantic_labels(semantic_key)
);

CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    map_id INTEGER NOT NULL,
    x_pos REAL NOT NULL,
    y_pos REAL NOT NULL,
    z_pos REAL,
    raster_x REAL NOT NULL,
    raster_y REAL NOT NULL,
    raw_sha256 TEXT,
    raw_json TEXT,
    UNIQUE(map_id, source_id),
    FOREIGN KEY(map_id) REFERENCES maps(id)
);

CREATE TABLE IF NOT EXISTS point_labels (
    point_id INTEGER NOT NULL,
    label_id INTEGER NOT NULL,
    PRIMARY KEY(point_id, label_id),
    FOREIGN KEY(point_id) REFERENCES points(id),
    FOREIGN KEY(label_id) REFERENCES label_nodes(id)
);

CREATE TABLE IF NOT EXISTS assets (
    sha256 TEXT PRIMARY KEY,
    mime_type TEXT,
    extension TEXT,
    byte_size INTEGER,
    width INTEGER,
    height INTEGER,
    local_relpath TEXT
);

CREATE TABLE IF NOT EXISTS asset_sources (
    remote_url TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    FOREIGN KEY(sha256) REFERENCES assets(sha256)
);

CREATE INDEX IF NOT EXISTS idx_maps_source ON maps(source_id);
CREATE INDEX IF NOT EXISTS idx_label_source ON label_nodes(source_id);
CREATE INDEX IF NOT EXISTS idx_label_parent ON label_nodes(parent_source_id);
CREATE INDEX IF NOT EXISTS idx_points_map ON points(map_id);
CREATE INDEX IF NOT EXISTS idx_points_source ON points(source_id);
CREATE INDEX IF NOT EXISTS idx_pl_point ON point_labels(point_id);
CREATE INDEX IF NOT EXISTS idx_pl_label ON point_labels(label_id);
CREATE INDEX IF NOT EXISTS idx_frag_map ON map_fragments(map_id);
"""


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class CoreDatabase:
    def __init__(self, path: Path, readonly: bool = False, immutable: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        self._lock = threading.Lock()
        if readonly:
            query = "mode=ro"
            if immutable:
                query += "&immutable=1"
            uri = f"file:{self.path.as_posix()}?{query}"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        #: 明确写进 SQLite 自己的版本位（a1-8 十六.7）：任何工具都能一眼看出这个库的 schema 版本。
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_map_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_map_nodes(nodes)

    def _insert_map_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        for node in nodes:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO map_nodes
                (source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node["source_id"],
                    node.get("parent_source_id"),
                    node.get("node_type"),
                    node.get("name"),
                    node.get("depth"),
                    node.get("sort_order"),
                    1 if node.get("is_renderable") else 0,
                    _dump(node.get("raw_json")),
                ),
            )
        self.conn.commit()

    def insert_label_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_label_nodes(nodes)

    def _insert_label_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        for node in nodes:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO label_nodes
                (source_id, parent_source_id, name, is_category, is_selectable, sort_order, icon_remote_url, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node["source_id"],
                    node.get("parent_source_id"),
                    node.get("name"),
                    1 if node.get("is_category") else 0,
                    1 if node.get("is_selectable") else 0,
                    node.get("sort_order"),
                    node.get("icon_remote_url"),
                    _dump(node.get("raw_json")),
                ),
            )
        self.conn.commit()

    def insert_semantic_bindings(self, bindings: Iterable[dict[str, Any]]) -> None:
        for item in bindings:
            self.conn.execute(
                "INSERT OR REPLACE INTO semantic_labels(semantic_key, canonical_name) VALUES (?, ?)",
                (item["semantic_key"], item.get("canonical_name")),
            )
            self.conn.execute(
                """
                INSERT OR REPLACE INTO semantic_label_bindings
                (semantic_key, source_label_id, confidence, resolver)
                VALUES (?, ?, ?, ?)
                """,
                (item["semantic_key"], item["source_label_id"], item.get("confidence"), item.get("resolver")),
            )
        self.conn.commit()

    def _ensure_unknown_label(self, source_id: str) -> int:
        row = self.conn.execute("SELECT id FROM label_nodes WHERE source_id = ?", (source_id,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            """
            INSERT INTO label_nodes (source_id, name, is_category, is_selectable, sort_order, raw_json)
            VALUES (?, ?, 0, 1, 0, ?)
            """,
            (source_id, None, _dump({"id": source_id, "unknown_label_reference": True})),
        )
        return cur.lastrowid

    def insert_map(self, mapped: dict[str, Any], map_info_sha256: str | None = None) -> int:
        with self._lock:
            return self._insert_map(mapped, map_info_sha256)

    def _insert_map(self, mapped: dict[str, Any], map_info_sha256: str | None = None) -> int:
        node = self.conn.execute("SELECT id FROM map_nodes WHERE source_id = ?", (mapped["source_id"],)).fetchone()
        cur = self.conn.execute(
            """
            INSERT OR REPLACE INTO maps
            (source_id, node_id, name, canvas_width, canvas_height, origin_x, origin_y,
             padding_json, fragment_count, map_info_sha256, coordinate_transform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mapped["source_id"],
                node["id"] if node else None,
                mapped.get("name"),
                mapped.get("canvas_width"),
                mapped.get("canvas_height"),
                mapped.get("origin_x"),
                mapped.get("origin_y"),
                _dump(mapped.get("padding_json")),
                mapped.get("fragment_count"),
                map_info_sha256,
                mapped.get("coordinate_transform"),
            ),
        )
        map_id = cur.lastrowid
        existing = self.conn.execute("SELECT id FROM maps WHERE source_id = ?", (mapped["source_id"],)).fetchone()
        map_id = existing["id"]
        self.conn.execute("DELETE FROM map_fragments WHERE map_id = ?", (map_id,))
        for fragment in mapped.get("fragments") or []:
            self.conn.execute(
                """
                INSERT INTO map_fragments
                (map_id, source_index, remote_url, asset_sha256, source_width, source_height, position_json, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    map_id,
                    fragment.get("index"),
                    fragment.get("remote_url"),
                    fragment.get("asset_sha256"),
                    fragment.get("source_width"),
                    fragment.get("source_height"),
                    _dump({k: fragment.get(k) for k in ("x", "y", "width", "height")}),
                    _dump(fragment),
                ),
            )
        self.conn.commit()
        return map_id

    def insert_points(self, points: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_points(points)

    def _insert_points(self, points: Iterable[dict[str, Any]]) -> None:
        for point in points:
            map_row = self.conn.execute("SELECT id FROM maps WHERE source_id = ?", (point["map_source_id"],)).fetchone()
            if not map_row:
                raise ValueError(f"missing map {point['map_source_id']}")
            cur = self.conn.execute(
                """
                INSERT OR REPLACE INTO points
                (source_id, map_id, x_pos, y_pos, z_pos, raster_x, raster_y, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    point["source_id"],
                    map_row["id"],
                    point["x_pos"],
                    point["y_pos"],
                    point.get("z_pos"),
                    point["raster_x"],
                    point["raster_y"],
                    _dump(point.get("raw_json")),
                ),
            )
            point_row = self.conn.execute(
                "SELECT id FROM points WHERE map_id = ? AND source_id = ?",
                (map_row["id"], point["source_id"]),
            ).fetchone()
            self.conn.execute("DELETE FROM point_labels WHERE point_id = ?", (point_row["id"],))
            if point.get("label_id") is not None:
                label_id = self._ensure_unknown_label(str(point["label_id"]))
                self.conn.execute(
                    "INSERT OR IGNORE INTO point_labels(point_id, label_id) VALUES (?, ?)",
                    (point_row["id"], label_id),
                )
        self.conn.commit()

    def count_points_for_map(self, source_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM points p
            JOIN maps m ON m.id = p.map_id
            WHERE m.source_id = ?
            """,
            (source_id,),
        ).fetchone()
        return int(row["n"])

    def semantic_source_id(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT source_label_id FROM semantic_label_bindings WHERE semantic_key = ?",
            (key,),
        ).fetchone()
        return None if row is None else row["source_label_id"]

    def map_by_source(self, source_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM maps WHERE source_id = ?", (source_id,)).fetchone()

    def map_by_name(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM maps WHERE name = ?", (name,)).fetchone()

    def points_for_map(self, map_pk: int) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM points WHERE map_id = ?", (map_pk,)))

    def fragments_for_map(self, map_pk: int) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM map_fragments WHERE map_id = ?", (map_pk,)))

    def counts(self) -> dict[str, int]:
        def n(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "map_nodes": n("map_nodes"),
            "renderable_maps": n("maps"),
            "labels": n("label_nodes"),
            "points": n("points"),
            "fragments": n("map_fragments"),
            "assets": n("assets"),
        }
