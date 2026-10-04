"""节点真名（a1-8-1 §6.11 第 1 步）：从 map/info payload 里把「父容器给的名字」收出来。

为什么需要它：树里的 `map_nodes.name` 有 350 个节点是空的或是占位符「特殊房间」，
真名只出现在**父容器**的 `map/info` → `data.info.children[].name`。sync 把真名写进了
`maps.display_name`（只有可渲染地图才有 `maps` 行），于是容器与部分深层节点在 Viewer 的
树里显示成裸 id（1016 / 1018 …）。

三条自我约束：

* **只写派生库**（`<data>/graph/core.db` 的 `node_display_names` 表），不碰 core schema；
* **不改判定层**：`viewer_repo._map_path()` 继续用 `map_nodes.name`，显示用真名、判定用原名；
* 没有名字就**不编** —— 收不到就返回空，让调用方自己决定怎么兜底。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

#: 派生库里的表：节点真名。与 `maps.display_name` 同口径（display_name + name_source）。
TABLE = "node_display_names"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    source_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    name_source TEXT NOT NULL,
    parent_id TEXT
);
"""


def collect(raw_dir: str | Path) -> dict[str, dict[str, Any]]:
    """扫 `<snapshot>/raw/map_info/*.json`，收出 `{child_id: {name, parent_id, source}}`。

    只认 `data.info.children[].name`：那是官方给的显示名。同名冲突时**先到先得**
    （同一个 id 只可能有一个父容器，真出现冲突也不该悄悄覆盖）。
    """
    root = Path(raw_dir)
    names: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return names
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        info = ((payload.get("data") or {}).get("info")) or {}
        parent_id = str(info.get("id") or path.stem)
        for child in info.get("children") or []:
            if not isinstance(child, Mapping):
                continue
            child_id = str(child.get("id") or "").strip()
            child_name = str(child.get("name") or "").strip()
            if not child_id or not child_name:
                continue
            names.setdefault(child_id, {
                "name": child_name,
                "parent_id": parent_id,
                "source": f"map_info:{parent_id}:children[].name",
            })
    return names


def write(conn: sqlite3.Connection, names: Mapping[str, Mapping[str, Any]]) -> int:
    """写进派生库（幂等：同 id 覆盖）。返回写入条数。"""
    conn.executescript(DDL)
    rows = [
        (str(source_id), str(item.get("name") or ""), str(item.get("source") or ""),
         str(item.get("parent_id") or ""))
        for source_id, item in names.items()
        if str(item.get("name") or "").strip()
    ]
    conn.executemany(
        f"INSERT OR REPLACE INTO {TABLE}(source_id, display_name, name_source, parent_id)"
        " VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def load(conn: sqlite3.Connection) -> dict[str, str]:
    """读派生库里的真名；没有表就返回空（老派生库要能照常用）。"""
    try:
        rows = conn.execute(f"SELECT source_id, display_name FROM {TABLE}").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row[0]): str(row[1]) for row in rows if str(row[1] or "").strip()}


def merge_names(
    nodes: Iterable[Mapping[str, Any]],
    names: Mapping[str, str],
) -> list[dict[str, Any]]:
    """给树节点合并显示名：`display_name(派生库) → name(树) → source_id`，并标出名字来源。

    **只加字段、不改其它字段**：调用方拿到的新列表里，原有键值一字不动。
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        row = dict(node)
        source_id = str(row.get("source_id") or row.get("id") or "")
        display = str(names.get(source_id) or "").strip()
        tree_name = str(row.get("name") or "").strip()
        if display:
            row["display_name"] = display
            row["name_source"] = "graph:node_display_names"
        else:
            row["display_name"] = tree_name or source_id
            row["name_source"] = "tree" if tree_name else "id"
        out.append(row)
    return out
