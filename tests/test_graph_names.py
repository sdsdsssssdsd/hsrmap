"""节点真名（§6.11 第 1 步）：只收名字、只写派生库、不改判定层。"""

from __future__ import annotations

import json
import sqlite3

from hsrmap.graph_names import collect, load, merge_names, write


def _raw(tmp_path, payloads: dict[str, dict]) -> "object":
    raw = tmp_path / "raw" / "map_info"
    raw.mkdir(parents=True, exist_ok=True)
    for map_id, children in payloads.items():
        (raw / f"{map_id}.json").write_text(
            json.dumps({"retcode": 0, "data": {"info": {"id": int(map_id), "children": children}}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    return raw


def test_collect_reads_children_names(tmp_path) -> None:
    raw = _raw(tmp_path, {
        "955": [{"id": 979, "name": "1"}, {"id": 980, "name": ""}],
        "938": [{"id": 955, "name": "千星城中心区7"}],
    })
    names = collect(raw)
    assert names["979"]["name"] == "1"
    assert names["955"]["name"] == "千星城中心区7"
    assert "980" not in names, "空名字不许进库（不编）"
    assert names["979"]["source"] == "map_info:955:children[].name"


def test_missing_directory_is_not_an_error(tmp_path) -> None:
    assert collect(tmp_path / "nope") == {}


def test_write_and_load_roundtrip_is_idempotent(tmp_path) -> None:
    raw = _raw(tmp_path, {"955": [{"id": 979, "name": "1"}]})
    names = collect(raw)
    conn = sqlite3.connect(tmp_path / "graph.db")
    assert write(conn, names) == 1
    assert write(conn, names) == 1, "重复写不新增行"
    assert load(conn) == {"979": "1"}
    assert conn.execute("SELECT COUNT(*) FROM node_display_names").fetchone()[0] == 1
    conn.close()


def test_load_without_the_table_returns_empty(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "empty.db")
    assert load(conn) == {}
    conn.close()


def test_merge_names_prefers_display_then_tree_then_id() -> None:
    nodes = [
        {"source_id": "979", "name": ""},
        {"source_id": "943", "name": "2层"},
        {"source_id": "1016", "name": ""},
    ]
    merged = merge_names(nodes, {"979": "1"})
    assert merged[0]["display_name"] == "1" and merged[0]["name_source"] == "graph:node_display_names"
    assert merged[1]["display_name"] == "2层" and merged[1]["name_source"] == "tree"
    assert merged[2]["display_name"] == "1016" and merged[2]["name_source"] == "id"
    #: 只加字段：原来的键值不动。
    assert merged[1]["name"] == "2层" and merged[0]["name"] == ""


def test_merge_names_does_not_mutate_the_input() -> None:
    nodes = [{"source_id": "979", "name": ""}]
    merge_names(nodes, {"979": "1"})
    assert "display_name" not in nodes[0]
