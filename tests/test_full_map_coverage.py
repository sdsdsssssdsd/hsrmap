"""Every renderable map is reachable and has a loadable raster."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
from hsrmap.viewer_bind import bind_viewer
from hsrmap.viewer_repo import asset_path
import pytest

pytestmark = pytest.mark.data


def test_full_map_coverage_624():
    client = TestClient(create_app())
    ctx = bind_viewer()
    maps = list(ctx.core.conn.execute("SELECT id, source_id FROM maps ORDER BY id"))
    assert len(maps) == 624
    tree_ids = set()

    def walk(nodes):
        for node in nodes:
            if node["renderable"]:
                tree_ids.add(node["id"])
            walk(node.get("children") or [])

    walk(client.get("/api/v1/maps/tree").json())
    missing_tree = []
    missing_meta = []
    missing_raster = []
    bad_points = []
    for row in maps:
        source_id = row["source_id"]
        if source_id not in tree_ids:
            missing_tree.append(source_id)
            continue
        meta = client.get(f"/api/v1/maps/{source_id}")
        if meta.status_code != 200:
            missing_meta.append(source_id)
            continue
        sha = meta.json()["raster"]["asset"]
        path = asset_path(sha) if sha else None
        if path is None or path.stat().st_size <= 0:
            missing_raster.append(source_id)
        expected = int(ctx.core.conn.execute("SELECT COUNT(*) n FROM points WHERE map_id = ?", (row["id"],)).fetchone()["n"])
        points = client.get(f"/api/v1/maps/{source_id}/points")
        if points.status_code != 200 or len(points.json()) != expected:
            bad_points.append(source_id)
    ctx.close()
    assert missing_tree == []
    assert missing_meta == []
    assert missing_raster == []
    assert bad_points == []
