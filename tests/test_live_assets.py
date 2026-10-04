"""Live images are cached under live-cache and never written to snapshot assets."""

from hsrmap.http import HttpResponse
from hsrmap.providers.live import live_asset_key
from hsrmap.live_assets import LiveAssetStore


def test_live_asset_roundtrip_stays_out_of_snapshot(tmp_path):
    url = "https://act.hoyolab.test/raster.png"
    body = b"\x89PNG-live-bytes"
    calls = {"n": 0}

    class Client:
        def get(self, requested: str, timeout: int = 30):
            calls["n"] += 1
            assert requested == url
            return HttpResponse(url=requested, status=200, body=body, headers={"content-type": "image/png"})

    store = LiveAssetStore(tmp_path)
    key = live_asset_key(url)
    path = store.fetch(Client(), url)
    assert path.read_bytes() == body
    assert key in path.name
    assert "snapshots" not in str(path)
    store.fetch(Client(), url)
    assert calls["n"] == 1
