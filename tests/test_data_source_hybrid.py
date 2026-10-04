import pytest
"""HTTP gateway: hybrid raster rollback, force-live 503, PUT without network."""

from pathlib import Path

from fastapi.testclient import TestClient

from hsrmap.http import HttpResponse
from hsrmap.providers.live import LiveSession
from hsrmap.viewer_app import create_app
from tests.test_provider_hybrid import FakeClient, _ok_client


def _session() -> LiveSession:
    return LiveSession(host="https://example.test/sr_map", app_version="test", bundle_sha256="abc")

@pytest.mark.data

def test_put_hybrid_with_injected_client_stays_off_network(tmp_path):
    app = create_app(
        user_path=tmp_path / "user.db",
        live_client=_ok_client(),
        live_session=_session(),
    )
    client = TestClient(app)
    body = client.put("/api/v1/data-source", json={"mode": "hybrid"}).json()
    assert body["mode"] == "hybrid"
    tree = client.get("/api/v1/maps/tree").json()
    assert tree[0]["name"] == "空间站「黑塔」"
    source = client.get("/api/v1/data-source").json()
    assert source["source"] == "live"
    assert source["remote_enabled"] is True


def test_force_live_tree_returns_503(tmp_path, monkeypatch):
    """强开 live、网络又坏时必须 503「官方连接失败」。

    这条要一条真实快照（map-only 进程也得先能绑定 core.db 才会走到 provider），
    所以把运行目录指回仓库 data/；仓库里没有快照就按项目惯例跳过。
    """
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.runtime import repo_data_dir

    repo_data = repo_data_dir(Path(__file__).resolve().parents[1])
    if not (repo_data / "current.json").is_file():
        pytest.skip("SKIPPED: snapshot fixture unavailable")
    monkeypatch.setenv("HSRMAP_DATA_DIR", str(repo_data))
    #: viewer 不再隐式建库（a1-8 四.2）。
    for name in ("guide.db", "published.db"):
        GuideDatabase.create(tmp_path / name).close()
    app = create_app(
        user_path=tmp_path / "user.db",
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
        data_mode="live",
        live_client=FakeClient(error=OSError("dns")),
        live_session=_session(),
    )
    response = TestClient(app).get("/api/v1/maps/tree")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "官方连接失败" in str(detail)

@pytest.mark.data

def test_live_raster_fail_returns_snapshot_map_not_half_live(tmp_path):
    client = _ok_client()

    def boom(url: str, timeout: int = 30):
        raise OSError("cdn down")

    client.get = boom  # type: ignore[method-assign]
    app = create_app(
        user_path=tmp_path / "user.db",
        data_mode="hybrid",
        live_client=client,
        live_session=_session(),
        live_cache=tmp_path / "live-cache",
    )
    http = TestClient(app)
    info = http.get("/api/v1/maps/842").json()
    assert not (info.get("raster") or {}).get("live_url")
    points = http.get("/api/v1/maps/842/points").json()
    assert len(points) == 48

@pytest.mark.data

def test_map_refresh_query_bypasses_ttl(tmp_path):
    live = _ok_client()
    app = create_app(
        user_path=tmp_path / "user.db",
        data_mode="hybrid",
        live_client=live,
        live_session=_session(),
        live_cache=tmp_path / "live-cache",
    )
    http = TestClient(app)
    http.get("/api/v1/maps/842")
    before = live.calls.count("/v1/map/info")
    http.get("/api/v1/maps/842")
    assert live.calls.count("/v1/map/info") == before
    http.get("/api/v1/maps/842?refresh=true")
    assert live.calls.count("/v1/map/info") > before
