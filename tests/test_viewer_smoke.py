"""Phase 4A kernel smoke: raster sizes, zero-point maps, seven regions, max markers."""

from fastapi.testclient import TestClient

from hsrmap.sync import PLANETS, descendant_source_ids
from hsrmap.viewer_app import create_app
from hsrmap.viewer_bind import bind_viewer
import pytest

pytestmark = pytest.mark.data


def test_max_points_on_one_map_is_under_leaflet_dom_threshold():
    ctx = bind_viewer()
    row = ctx.core.conn.execute("SELECT MAX(n) n FROM (SELECT COUNT(*) n FROM points GROUP BY map_id)").fetchone()
    assert int(row["n"]) <= 300
    ctx.close()


def test_kernel_raster_sizes_and_zero_point_map():
    client = TestClient(create_app())
    haiyuan = client.get("/api/v1/maps/842").json()
    assert haiyuan["width"] == 8192 and haiyuan["height"] == 4096
    huge = client.get("/api/v1/maps/1014")
    assert huge.status_code == 200
    assert huge.json()["width"] == 8192 and huge.json()["height"] == 8192
    tiny = client.get("/api/v1/maps/66").json()
    assert tiny["width"] == 512 and tiny["height"] == 512
    zero = client.get("/api/v1/maps/325/points").json()
    assert zero == []
    assert client.get("/api/v1/maps/325").status_code == 200


def test_seven_region_maps_have_raster_assets():
    client = TestClient(create_app())
    ctx = bind_viewer()
    roots = list(ctx.core.conn.execute("SELECT source_id, name FROM map_nodes WHERE parent_source_id IS NULL"))
    found = []
    for planet in PLANETS:
        node = next((n for n in roots if planet in str(n["name"])), None)
        assert node is not None, planet
        for source_id in sorted(descendant_source_ids(ctx.core, node["source_id"])):
            payload = client.get(f"/api/v1/maps/{source_id}")
            if payload.status_code != 200:
                continue
            body = payload.json()
            if body["raster"]["asset"]:
                found.append(planet)
                break
        else:
            raise AssertionError(planet)
    ctx.close()
    assert found == list(PLANETS)
