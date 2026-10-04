"""Content-addressed assets reuse the same SHA even if remote URLs differ."""

from pathlib import Path

from hsrmap.assets import AssetStore


def test_same_bytes_from_two_urls_share_one_sha(tmp_path: Path):
    from io import BytesIO

    from PIL import Image

    store = AssetStore(tmp_path / "sha256")
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(buffer, format="PNG")
    png = buffer.getvalue()
    first = store.ingest_bytes(png, remote_url="https://a.example/x.png")
    second = store.ingest_bytes(png, remote_url="https://b.example/y.png")

    assert first.sha256 == second.sha256
    assert first.local_path == second.local_path
    assert first.local_path.exists()
    assert first.local_path.read_bytes() == png
    assert len(store.sources_for(first.sha256)) == 2
