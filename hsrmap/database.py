from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


#: 核心快照库的 schema 版本（a1-8 十六.7：每个库都要有明确的版本）。
#: v2 = M7.1 Map Graph（a1-8-1 §四/§九/§十三）：map_edges / point_transitions 两张新表，
#: 以及 map_nodes 的 tree_leaf / render_probe_state / discovery_method 三个可空新列。
#: v3 = M7.3 Deep Sync（a1-8-1 §七/§十五）：maps 的 display_name / name_source 两个可空新列
#: （容器 map/info 的 children[].name 是真名的唯一来源；**不覆盖** map_nodes.name）。
#: 迁移是**加性**的（CREATE TABLE IF NOT EXISTS + ADD COLUMN），v1 / v2 的老库照常打开。
SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;

-- v1 的列 + M7.1 的加性新列（老库由 _migrate() 用 ALTER TABLE 补上）：
--   tree_leaf           树事实：这个节点没有 children（与 is_renderable 是两个维度，a1-8-1 §三）
--   render_probe_state  可渲染探测的状态 VALID / INVALID / UNKNOWN（a1-8-1 §九）
--   discovery_method    这个节点是怎么进图谱的：TREE / POINT_JUMP / ...（a1-8-1 §九）
-- is_renderable 的取值域也因此变成 1 / 0 / NULL：NULL = 还没探测过（不猜）。
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

-- M7.3（a1-8-1 §七 / §十五）加的两列：
--   display_name  真名 —— 唯一来源是**容器**（node_type=1）的 map/info → data.info.children[].name；
--                 name 保留 map/info 自己那一份，map_nodes.name 是官方树的原始名，三者互不覆盖；
--   name_source   这个 display_name 是哪来的（见 hsrmap/sync.py 的 NAME_SOURCE_* 常量）。
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
    display_name TEXT,
    name_source TEXT,
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

-- M7.1 Map Graph（a1-8-1 §四）：地图之间怎么过去。edge_type 的取值见 hsrmap/graph.py
-- 的模块级常量（TREE_CHILD / FLOOR / POINT_JUMP / RELATED_MAP / MAP_GROUP / PORTAL / RETURN /
-- UNKNOWN_TRANSITION）；语义不明也必须先记 UNKNOWN_TRANSITION，不许丢 target（§五）。
CREATE TABLE IF NOT EXISTS map_edges (
    id INTEGER PRIMARY KEY,

    source_map_id TEXT NOT NULL,
    target_map_id TEXT NOT NULL,

    edge_type TEXT NOT NULL,

    source_point_id TEXT,
    source_label_id TEXT,

    discovery_source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,

    bidirectional INTEGER NOT NULL DEFAULT 0,

    raw_json TEXT,
    discovered_at TEXT,

    UNIQUE(
        source_map_id,
        target_map_id,
        edge_type,
        source_point_id
    )
);

-- SQLite 的 UNIQUE 认为 NULL 互不相等，所以 source_point_id IS NULL 的那一类边要再压一个
-- 部分唯一索引，否则「同一张地图、同一类、没有点位来源」的边会被反复插进来。
CREATE UNIQUE INDEX IF NOT EXISTS idx_map_edges_unsourced
    ON map_edges(source_map_id, target_map_id, edge_type)
    WHERE source_point_id IS NULL;

-- M7.1（a1-8-1 §十三）：点位上的跳转。point_id = points.id（core 主键）。
CREATE TABLE IF NOT EXISTS point_transitions (
    point_id INTEGER NOT NULL,
    target_map_source_id TEXT NOT NULL,

    transition_type TEXT NOT NULL,
    action_label TEXT,
    raw_json TEXT,

    PRIMARY KEY(point_id, target_map_source_id)
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
CREATE INDEX IF NOT EXISTS idx_map_edges_source ON map_edges(source_map_id);
CREATE INDEX IF NOT EXISTS idx_map_edges_target ON map_edges(target_map_id);
CREATE INDEX IF NOT EXISTS idx_point_transitions_target ON point_transitions(target_map_source_id);
"""

#: M7.1 的加性迁移：老库缺这几列时用 ALTER TABLE 补上（可空，不猜默认值）。
MAP_NODE_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tree_leaf", "INTEGER"),
    ("render_probe_state", "TEXT"),
    ("discovery_method", "TEXT"),
)

#: M7.3 的加性迁移（maps 表，同样是可空 + 不猜默认值）。
#: v2 的老库（例如 M7.2 的旁挂图库 data/graph/core.db）打开后这两列是 NULL，
#: 读取端必须按「还没有真名」处理，回退到 maps.name / map_nodes.name。
MAP_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("display_name", "TEXT"),
    ("name_source", "TEXT"),
)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """表的列名集合；表不存在时返回空集合（老库兼容要靠它判断，不靠 try/except）。"""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _tri_state(value: Any) -> int | None:
    """三态落库：True → 1，False → 0，None → NULL。

    NULL 表示「还没探测过」，与 0（有证据说不是）必须分开（a1-8-1 §九）。
    """
    if value is None:
        return None
    return 1 if value else 0


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
        self._migrate()
        #: 明确写进 SQLite 自己的版本位（a1-8 十六.7）：任何工具都能一眼看出这个库的 schema 版本。
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate(self) -> None:
        """加性迁移（M7.1 map_nodes / M7.3 maps）：只 ADD COLUMN 补新列，绝不改/删老列、
        绝不给老行编造探测结论，也绝不编造真名。

        v1 / v2 的老库（例如 data/snapshots/20261001T105105Z/core.db 与 M7.2 的
        data/graph/core.db）打开后新列是 NULL，读取端必须按「老库回退」处理
        （见 hsrmap/graph.py::load_map_nodes 与 hsrmap/graph_nav.py::_name_index）。
        """
        for table, columns in (("map_nodes", MAP_NODE_ADDED_COLUMNS), ("maps", MAP_ADDED_COLUMNS)):
            existing = table_columns(self.conn, table)
            for name, decl in columns:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def insert_map_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_map_nodes(nodes)

    def _insert_map_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        for node in nodes:
            #: UPSERT 而不是 INSERT OR REPLACE：REPLACE 靠「删旧行 + 插新行」实现，rowid 会变，
            #: 而 maps.node_id 指着它 → 带 PRAGMA foreign_keys=ON 时直接 FK 约束失败。
            #: 这条路只在 sync --resume（maps 已经落库）时会走到，实测炸过一次。
            self.conn.execute(
                """
                INSERT INTO map_nodes
                (source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable,
                 tree_leaf, render_probe_state, discovery_method, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    parent_source_id = excluded.parent_source_id,
                    node_type = excluded.node_type,
                    name = excluded.name,
                    depth = excluded.depth,
                    sort_order = excluded.sort_order,
                    is_renderable = excluded.is_renderable,
                    tree_leaf = excluded.tree_leaf,
                    render_probe_state = excluded.render_probe_state,
                    discovery_method = excluded.discovery_method,
                    raw_json = excluded.raw_json
                """,
                (
                    node["source_id"],
                    node.get("parent_source_id"),
                    node.get("node_type"),
                    node.get("name"),
                    node.get("depth"),
                    node.get("sort_order"),
                    _tri_state(node.get("is_renderable")),
                    _tri_state(node.get("tree_leaf")),
                    node.get("render_probe_state"),
                    node.get("discovery_method"),
                    _dump(node.get("raw_json")),
                ),
            )
        self.conn.commit()

    def insert_label_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_label_nodes(nodes)

    def _insert_label_nodes(self, nodes: Iterable[dict[str, Any]]) -> None:
        for node in nodes:
            #: 同 map_nodes：UPSERT 保 rowid（point_labels.label_id 指着它），
            #: 而且不碰 icon_asset_sha256 —— 那是资源阶段写的，重跑不该把它抹掉。
            self.conn.execute(
                """
                INSERT INTO label_nodes
                (source_id, parent_source_id, name, is_category, is_selectable, sort_order, icon_remote_url, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    parent_source_id = excluded.parent_source_id,
                    name = excluded.name,
                    is_category = excluded.is_category,
                    is_selectable = excluded.is_selectable,
                    sort_order = excluded.sort_order,
                    icon_remote_url = excluded.icon_remote_url,
                    raw_json = excluded.raw_json
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
        #: 同 map_nodes：UPSERT 保 rowid（points.map_id 指着它）。
        #: 特意**不更新** display_name / name_source：那是 M7.3 的真名，由 update_map_names 管，
        #: 重跑 map/info 不该把它冲成 NULL。
        cur = self.conn.execute(
            """
            INSERT INTO maps
            (source_id, node_id, name, canvas_width, canvas_height, origin_x, origin_y,
             padding_json, fragment_count, map_info_sha256, coordinate_transform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                node_id = excluded.node_id,
                name = excluded.name,
                canvas_width = excluded.canvas_width,
                canvas_height = excluded.canvas_height,
                origin_x = excluded.origin_x,
                origin_y = excluded.origin_y,
                padding_json = excluded.padding_json,
                fragment_count = excluded.fragment_count,
                map_info_sha256 = excluded.map_info_sha256,
                coordinate_transform = excluded.coordinate_transform
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

    def update_map_names(self, items: Iterable[dict[str, Any]]) -> int:
        """把真名写进 maps.display_name（M7.3）。**只动这两列**：

        - 不碰 maps.name（那是 map/info 自己那一份）；
        - 不碰 map_nodes.name（官方树的原始名，a1-8-1 §七 明令不许覆盖）；
        - 没被点名的 maps 行原样不动；旧值不会被空串冲掉。

        返回真正被更新的行数（不在 maps 里的 id 不算）。
        """
        with self._lock:
            updated = 0
            for item in items:
                display = str(item.get("display_name") or "").strip()
                if not display:
                    continue
                cur = self.conn.execute(
                    "UPDATE maps SET display_name = ?, name_source = ? WHERE source_id = ?",
                    (display, item.get("name_source"), str(item["source_id"])),
                )
                updated += int(cur.rowcount or 0)
            self.conn.commit()
            return updated

    def insert_points(self, points: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._insert_points(points)

    def _insert_points(self, points: Iterable[dict[str, Any]]) -> None:
        for point in points:
            map_row = self.conn.execute("SELECT id FROM maps WHERE source_id = ?", (point["map_source_id"],)).fetchone()
            if not map_row:
                raise ValueError(f"missing map {point['map_source_id']}")
            #: 同 map_nodes：UPSERT 保 rowid（point_labels.point_id 指着它）。
            cur = self.conn.execute(
                """
                INSERT INTO points
                (source_id, map_id, x_pos, y_pos, z_pos, raster_x, raster_y, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(map_id, source_id) DO UPDATE SET
                    x_pos = excluded.x_pos,
                    y_pos = excluded.y_pos,
                    z_pos = excluded.z_pos,
                    raster_x = excluded.raster_x,
                    raster_y = excluded.raster_y,
                    raw_json = excluded.raw_json
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
            #: 老库（M7.1 之前）没有 map_edges / point_transitions：那是「还没有边」，
            #: 不是错误——只读快照上绝不能因为多了两个计数就炸（a1-8-1 §二十四）。
            if not has_table(self.conn, table):
                return 0
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        return {
            "map_nodes": n("map_nodes"),
            "renderable_maps": n("maps"),
            "labels": n("label_nodes"),
            "points": n("points"),
            "fragments": n("map_fragments"),
            "assets": n("assets"),
            "map_edges": n("map_edges"),
            "point_transitions": n("point_transitions"),
        }
