"""import-page archives HTML and never writes official assets."""

from pathlib import Path

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.pipeline import import_page
from hsrmap.guides.store import RawGuideStore

FIX = Path(__file__).parent / "fixtures" / "guides" / "sample_17173.html"


def test_import_page_writes_raw_and_blocks(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", tmp_path / "guide-assets" / "sha256")
    html = FIX.read_text(encoding="utf-8")
    result = import_page(
        html,
        "https://news.17173.com/content/04222026/173231817.shtml",
        db,
        store,
        fetch_asset=lambda src: b"PNG-" + src.encode(),
    )
    page = result["page"]
    assert page["crawl_status"] == "FETCHED"
    assert page["author"] == "祈鸢ya"
    assert Path(page["raw_html_path"]).exists()
    assert any(b["type"] == "heading" for b in result["blocks"])
    assert "snapshots" not in str(page["raw_html_path"])
    assert (tmp_path / "guide-assets" / "sha256").exists()
    images = [b for b in result["blocks"] if b["type"] == "image"]
    assert len(images) == 2
