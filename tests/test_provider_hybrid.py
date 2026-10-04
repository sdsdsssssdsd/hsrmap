"""HybridProvider uses live first and falls back to snapshot as a whole map."""

import json

from hsrmap.http import HttpResponse
from hsrmap.providers.hybrid import TTL_MAP_SEC, TTL_POINT_SEC, TTL_TREE_SEC, HybridProvider
from hsrmap.providers.live import LiveProvider, LiveSession
from hsrmap.providers.snapshot import SnapshotProvider
from hsrmap.viewer_bind import bind_viewer
from tests.test_provider_live import LABEL_TREE, MAP_INFO, POINT_INFO, POINT_LIST, TREE
import pytest

pytestmark = pytest.mark.data


def _resp(payload: dict) -> HttpResponse:
    return HttpResponse(url="https://example.test", status=200, body=json.dumps(payload).encode(), headers={})


class FakeClient:
    def __init__(self, by_path: dict[str, dict] | None = None, error: Exception | None = None):
        self.by_path = by_path or {}
        self.error = error
        self.calls: list[str] = []

    def api_get(self, host: str, path: str, params: dict):
        self.calls.append(path)
        if self.error:
            raise self.error
        return _resp(self.by_path[path])

    def get(self, url: str, timeout: int = 30):
        self.calls.append(url)
        if self.error:
            raise self.error
        return HttpResponse(url=url, status=200, body=b"\x89PNG-live", headers={"content-type": "image/png"})


def _live(client) -> LiveProvider:
    return LiveProvider(
        client,
        LiveSession(host="https://example.test/sr_map", app_version="test", bundle_sha256="abc"),
    )


def _ok_client() -> FakeClient:
    return FakeClient(
        {
            "/v1/map/tree": TREE,
            "/v1/map/info": MAP_INFO,
            "/v1/map/point/list": POINT_LIST,
            "/v1/map/point/info": POINT_INFO,
            "/v1/map/label/tree": LABEL_TREE,
        }
    )


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def test_hybrid_falls_back_to_snapshot_when_live_fails():
    ctx = bind_viewer()
    try:
        snap = SnapshotProvider(ctx)
        hybrid = HybridProvider(_live(FakeClient(error=OSError("dns"))), snap)
        tree = hybrid.get_tree()
        assert sum(1 for _ in _walk(tree)) == 923
        assert hybrid.provenance()["source"] == "snapshot"
        assert hybrid.provenance()["stale"] is True
        assert "官方连接失败" in (hybrid.provenance().get("message") or "")
        info = hybrid.get_map("842")
        assert info["name"] == "海原市"
        assert len(hybrid.get_points("842")) == 48
    finally:
        ctx.close()


def test_hybrid_uses_live_when_ok():
    ctx = bind_viewer()
    try:
        snap = SnapshotProvider(ctx)
        hybrid = HybridProvider(_live(_ok_client()), snap)
        tree = hybrid.get_tree()
        assert tree[0]["name"] == "空间站「黑塔」"
        assert hybrid.provenance()["source"] == "live"
        assert hybrid.get_map("842")["width"] == 8192
        assert hybrid.get_points("842")[0]["source_id"] == "5171"
    finally:
        ctx.close()


def test_hybrid_500_falls_back_to_snapshot():
    ctx = bind_viewer()
    try:
        hybrid = HybridProvider(_live(FakeClient(error=RuntimeError("HTTP 500"))), SnapshotProvider(ctx))
        assert sum(1 for _ in _walk(hybrid.get_tree())) == 923
        assert hybrid.provenance()["source"] == "snapshot"
    finally:
        ctx.close()


def test_hybrid_timeout_falls_back_to_snapshot():
    ctx = bind_viewer()
    try:
        hybrid = HybridProvider(_live(FakeClient(error=TimeoutError("timed out"))), SnapshotProvider(ctx))
        assert hybrid.get_map("842")["name"] == "海原市"
        assert len(hybrid.get_points("842")) == 48
        assert hybrid.provenance()["stale"] is True
    finally:
        ctx.close()


def test_hybrid_schema_change_disables_live():
    ctx = bind_viewer()
    try:
        client = _ok_client()
        client.by_path["/v1/map/tree"] = {"retcode": 0, "data": {}}
        hybrid = HybridProvider(_live(client), SnapshotProvider(ctx))
        tree = hybrid.get_tree()
        assert sum(1 for _ in _walk(tree)) == 923
        assert "官方地图接口已变化" in (hybrid.provenance().get("message") or "")
        client.by_path["/v1/map/tree"] = TREE
        again = hybrid.get_tree()
        assert sum(1 for _ in _walk(again)) == 923
    finally:
        ctx.close()


def test_hybrid_raster_fail_rolls_back_whole_map():
    ctx = bind_viewer()
    try:
        hybrid = HybridProvider(
            _live(_ok_client()),
            SnapshotProvider(ctx),
            raster_fetch=lambda url: (_ for _ in ()).throw(OSError("cdn down")),
        )
        info = hybrid.get_map("842")
        assert not (info.get("raster") or {}).get("live_url")
        points = hybrid.get_points("842")
        assert len(points) == 48
        assert hybrid.provenance()["source"] == "snapshot"
    finally:
        ctx.close()


def test_hybrid_point_cache_survives_disconnect():
    ctx = bind_viewer()
    try:
        clock = {"t": 10.0}
        client = _ok_client()
        hybrid = HybridProvider(_live(client), SnapshotProvider(ctx), now=lambda: clock["t"])
        detail = hybrid.get_point(5171)
        assert "浮脂溯源" in (detail["detail"]["text"] or "")
        client.error = OSError("dns")
        still = hybrid.get_point(5171)
        assert still["detail"]["text"] == detail["detail"]["text"]
        clock["t"] += TTL_POINT_SEC + 1
        cached = hybrid.get_point(5171)
        assert cached["detail"]["text"] == detail["detail"]["text"]
        assert hybrid.provenance()["source"] == "live-cache"
    finally:
        ctx.close()


def test_hybrid_retries_live_after_refresh():
    ctx = bind_viewer()
    try:
        client = FakeClient(error=OSError("dns"))
        hybrid = HybridProvider(_live(client), SnapshotProvider(ctx))
        assert sum(1 for _ in _walk(hybrid.get_tree())) == 923
        client.error = None
        client.by_path = _ok_client().by_path
        recovered = hybrid.get_tree(refresh=True)
        assert recovered[0]["name"] == "空间站「黑塔」"
        assert hybrid.provenance()["source"] == "live"
    finally:
        ctx.close()


def test_hybrid_ttl_skips_live_until_refresh():
    ctx = bind_viewer()
    try:
        clock = {"t": 100.0}
        client = _ok_client()
        hybrid = HybridProvider(_live(client), SnapshotProvider(ctx), now=lambda: clock["t"])
        hybrid.get_tree()
        first = len(client.calls)
        hybrid.get_tree()
        assert len(client.calls) == first
        clock["t"] += TTL_TREE_SEC + 1
        hybrid.get_tree()
        assert len(client.calls) > first
        map_calls = len(client.calls)
        hybrid.get_map("842")
        after_map = len(client.calls)
        hybrid.get_map("842")
        assert len(client.calls) == after_map
        hybrid.get_map("842", refresh=True)
        assert len(client.calls) > after_map
        assert TTL_MAP_SEC >= 300
    finally:
        ctx.close()


def test_force_live_raises_instead_of_snapshot():
    ctx = bind_viewer()
    try:
        hybrid = HybridProvider(_live(FakeClient(error=OSError("dns"))), SnapshotProvider(ctx), force_live=True)
        try:
            hybrid.get_tree()
        except OSError:
            return
        raise AssertionError("force live must not swallow network errors")
    finally:
        ctx.close()
