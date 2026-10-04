"""P6.6 Viewer 集成：进度接口是**只读**的，而且这个进程不发外网。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app

pytestmark = pytest.mark.data


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(user_path=tmp_path / "user.db"))


def test_progress_status_is_read_only_and_never_networked(client) -> None:
    body = client.get("/api/v1/progress/status").json()
    assert body["available"] is True
    assert body["viewer_network"] == 0
    assert body["store"]["stores_cookie"] is False
    #: 界面不提供合并入口：合并只走 CLI。
    assert body["merge"]["available"] is False
    assert "progress merge" in body["merge"]["how"]


def test_progress_points_covers_every_collectible_and_uses_four_states(client) -> None:
    body = client.get("/api/v1/progress/points").json()
    assert body["available"] is True
    assert body["totals"]["collectible"] == 1006
    assert len(body["states"]) == 1006
    assert set(body["states"].values()) <= {"completed", "remaining", "conflict", "unclear"}
    #: Gate 0 之前：远端没有推导权，所以「有效完成」就是本地勾选。
    assert body["gate"]["allowed_remote_semantics"] == []
    assert body["totals"]["effective_completed"] == 0


def test_marking_a_point_locally_flips_it_to_completed(client) -> None:
    point = next(pid for pid, state in client.get("/api/v1/progress/points").json()["states"].items()
                 if state == "remaining")
    client.put(f"/api/v1/user/points/{point}", json={"completed": True})
    after = client.get("/api/v1/progress/points").json()
    assert after["states"][point] == "completed"
    assert after["totals"]["effective_completed"] == 1


def test_accept_map_mark_still_does_not_count_as_completed(client) -> None:
    """开关只影响展示口径；没有观察数据时一个点都不该变成完成。"""
    body = client.get("/api/v1/progress/points?accept_map_mark=true").json()
    assert body["totals"]["effective_completed"] == 0
    assert body["totals"]["remaining"] == 1006


def test_progress_atlas_groups_by_zone_and_marks_guide_gaps(client) -> None:
    body = client.get("/api/v1/progress/atlas").json()
    assert body["available"] is True
    assert body["viewer_network"] == 0
    assert body["totals"]["collectible"] == 1006
    assert body["regions"], "剩余清单必须按大区分组"
    region = body["regions"][0]
    assert {"zone", "collectible", "remaining", "maps"} <= set(region)
    point = region["maps"][0]["points"][0]
    assert {"source_point_id", "label", "state", "locate_evidence", "solve_evidence"} <= set(point)
    assert point["state"] != "completed"


def test_progress_atlas_can_be_filtered_to_one_zone(client) -> None:
    zone = client.get("/api/v1/progress/atlas").json()["regions"][0]["zone"]
    filtered = client.get("/api/v1/progress/atlas", params={"region": zone}).json()
    assert [r["zone"] for r in filtered["regions"]] == [zone]
    assert client.get("/api/v1/progress/atlas", params={"region": "不存在的区域"}).json()["regions"] == []


def test_progress_endpoints_make_no_outbound_connection(client, monkeypatch) -> None:
    """「Viewer 不联网」要是可执行的断言，不是一句口号。

    把进程里所有真正出网的入口掐掉，再问三个进度接口一次：它们必须照常回答
    （因为它们只读本地 SQLite）。
    """
    import urllib.request

    import httpx

    def boom(*_args, **_kwargs):
        raise AssertionError("progress 接口不该发起任何网络连接")

    #: 堵真正的出网传输（不是 socket：TestClient 自己的事件循环也要用 socket）。
    monkeypatch.setattr(urllib.request, "urlopen", boom, raising=True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom, raising=True)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom, raising=True)

    assert client.get("/api/v1/progress/status").status_code == 200
    points = client.get("/api/v1/progress/points")
    atlas = client.get("/api/v1/progress/atlas")
    assert points.status_code == 200 and atlas.status_code == 200
    assert points.json()["viewer_network"] == 0
    assert atlas.json()["viewer_network"] == 0


def test_there_is_no_progress_write_endpoint(client) -> None:
    """写接口不存在（404/405），不是「有但不让点」。"""
    assert client.post("/api/v1/progress/merge").status_code in {404, 405}
    assert client.put("/api/v1/progress/points").status_code in {404, 405}
    assert client.post("/api/v1/progress/points").status_code in {404, 405}
    assert client.post("/api/v1/progress/atlas").status_code in {404, 405}
