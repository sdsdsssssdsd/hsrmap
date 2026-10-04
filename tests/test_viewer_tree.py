"""Map tree and per-map labels are Viewer-ready, not raw DB dumps."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
from hsrmap.viewer_bind import bind_viewer
import pytest

pytestmark = pytest.mark.data


def client() -> TestClient:
    return TestClient(create_app())


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def test_map_tree_exposes_923_nodes_without_raw_json():
    body = client().get("/api/v1/maps/tree").json()
    assert isinstance(body, list)
    flat = list(_walk(body))
    assert len(flat) == 923
    assert sum(1 for node in flat if node["renderable"]) == 624
    roots = {node["name"] for node in body}
    assert "空间站「黑塔」" in roots
    assert "二相乐园" in roots
    station = next(node for node in body if node["name"] == "空间站「黑塔」")
    names = [child["name"] for child in station["children"]]
    assert names == ["主控舱段", "基座舱段", "收容舱段", "支援舱段", "禁闭舱段"]
    cabin = station["children"][0]
    assert cabin["id"] == "73"
    assert cabin["renderable"] is False
    assert cabin["children"][0]["id"] == "38"
    assert cabin["children"][0]["renderable"] is True
    sample = flat[0]
    assert set(sample) >= {"id", "name", "type", "renderable", "children"}
    assert "raw_json" not in sample
    assert "raw" not in sample


def test_tree_reaches_every_renderable_map():
    ctx = bind_viewer()
    expected = {row["source_id"] for row in ctx.core.conn.execute("SELECT source_id FROM maps")}
    ctx.close()
    reached = {node["id"] for node in _walk(client().get("/api/v1/maps/tree").json()) if node["renderable"]}
    assert reached == expected


def test_map_842_labels_are_grouped_and_only_present_labels():
    groups = client().get("/api/v1/maps/842/labels").json()
    assert groups
    names = [label["name"] for group in groups for label in group["labels"]]
    assert "浮脂溯源·二次元ROTATE！" in names
    assert all(label["count"] > 0 for group in groups for label in group["labels"])
    grease = next(label for group in groups for label in group["labels"] if label["id"] == "686")
    assert grease["count"] == 3
    assert grease["icon"].startswith("/assets/")
    assert grease["semantic_key"] == "floating_grease_origin_retrace"
    categories = [group["category"]["name"] for group in groups]
    assert "地标" in categories
    assert len(names) < 1006
