"""LiveProvider maps official public JSON into Viewer shapes without using core.db."""

import json

from hsrmap.http import HttpResponse
from hsrmap.providers.live import LiveIncompatible, LiveProvider, LiveSession


def _resp(payload: dict) -> HttpResponse:
    return HttpResponse(url="https://example.test", status=200, body=json.dumps(payload).encode(), headers={})


class FakeClient:
    def __init__(self, by_path: dict[str, dict]):
        self.by_path = by_path
        self.calls: list[tuple[str, dict]] = []

    def api_get(self, host: str, path: str, params: dict):
        self.calls.append((path, params))
        return _resp(self.by_path[path])


TREE = {
    "retcode": 0,
    "data": {
        "tree": [
            {
                "id": 1,
                "name": "空间站「黑塔」",
                "parent_id": 0,
                "node_type": 1,
                "children": [
                    {
                        "id": 73,
                        "name": "主控舱段",
                        "parent_id": 1,
                        "node_type": 1,
                        "children": [{"id": 38, "name": "主控舱段", "parent_id": 73, "node_type": 2, "children": []}],
                    }
                ],
            }
        ]
    },
}

MAP_INFO = {
    "retcode": 0,
    "data": {
        "info": {
            "id": 842,
            "name": "海原市",
            "detail": {
                "total_size": [8192, 4096],
                "origin": [3827, 2217],
                "padding": [1706, 219],
                "slices": [[{"url": "https://act.hoyolab.test/raster.png"}]],
            },
        }
    },
}

POINT_LIST = {
    "retcode": 0,
    "data": {"point_list": [{"id": 5171, "x_pos": 85.5, "y_pos": -81.5, "label_id": 686, "label_name": "浮脂溯源"}]},
}

POINT_INFO = {
    "retcode": 0,
    "data": {"info": {"id": 5171, "content": "完成此处浮脂溯源解谜获得。", "img": "https://act.hoyolab.test/d.png"}},
}

LABEL_TREE = {"retcode": 0, "data": {"tree": [{"id": 1, "name": "地标", "children": [{"id": 686, "name": "浮脂溯源"}]}]}}


def _provider(extra: dict | None = None) -> LiveProvider:
    routes = {
        "/v1/map/tree": TREE,
        "/v1/map/info": MAP_INFO,
        "/v1/map/point/list": POINT_LIST,
        "/v1/map/point/info": POINT_INFO,
        "/v1/map/label/tree": LABEL_TREE,
    }
    if extra:
        routes.update(extra)
    session = LiveSession(
        host="https://sg-act-public-api-static.hoyolab.com/common/srmap/sr_map",
        app_version="test",
        bundle_sha256="abc",
        compatible=True,
    )
    return LiveProvider(FakeClient(routes), session)


def test_live_map_and_points_use_official_xy():
    provider = _provider()
    info = provider.get_map("842")
    assert info["name"] == "海原市"
    assert info["width"] == 8192
    assert info["origin"] == [3827.0, 2217.0]
    points = provider.get_points("842")
    assert points[0]["x"] == 85.5
    assert points[0]["y"] == -81.5
    assert points[0]["source_id"] == "5171"
    detail = provider.get_point(5171)
    assert detail["detail"]["state"] == "NONEMPTY"
    assert "浮脂溯源" in (detail["detail"]["text"] or "")
    tree = provider.get_tree()
    assert tree[0]["name"] == "空间站「黑塔」"
    assert provider.provenance()["source"] == "live"


def test_live_point_hash_changes_when_list_changes():
    provider = _provider()
    first = provider.point_list_hash("842")
    provider.client.by_path["/v1/map/point/list"] = {
        "retcode": 0,
        "data": {"point_list": [{"id": 1, "x_pos": 0, "y_pos": 0, "label_id": 1}]},
    }
    provider._map_cache.clear()
    second = provider.point_list_hash("842")
    assert first != second


def test_live_schema_change_is_incompatible():
    provider = _provider({"/v1/map/tree": {"retcode": 0, "data": {}}})
    try:
        provider.get_tree()
    except LiveIncompatible:
        return
    raise AssertionError("expected LiveIncompatible")
