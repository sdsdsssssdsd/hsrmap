"""Raw guide store never writes official snapshot assets."""

from hsrmap.guides.store import RawGuideStore


def test_html_and_image_stay_in_guide_dirs(tmp_path):
    store = RawGuideStore(tmp_path / "guides", tmp_path / "guide-assets" / "sha256")
    run = store.start_run("floating_grease")
    html_path = store.save_html(run, "p1", "<html><p>海原市</p></html>")
    text_path = store.save_text(run, "p1", "海原市")
    blocks_path = store.save_extracted(run, "p1", [{"type": "heading", "text": "海原市"}])
    asset = store.save_asset(b"\x89PNG-guide", "https://cdn.example/a.png")
    assert html_path.exists()
    assert text_path.exists()
    assert blocks_path.exists()
    assert asset.sha256
    assert store.assets_root in asset.path.parents
    assert "snapshots" not in str(asset.path)
    assert (tmp_path / "guides" / "raw" / run / "manifests" / "run.json").exists()
