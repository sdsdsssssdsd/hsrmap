"""Canary import QA: title/author/order/assets, ads dropped."""

import json
from pathlib import Path

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.pipeline import import_page
from hsrmap.guides.qa import inspect_import
from hsrmap.guides.store import RawGuideStore

CANARY = Path(__file__).parent / "fixtures" / "guides" / "canary"


def _import(tmp_path, name, url):
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", tmp_path / "guide-assets" / "sha256")
    html = (CANARY / name).read_text(encoding="utf-8")
    result = import_page(html, url, db, store, fetch_asset=lambda src: b"PNG-" + src.encode())
    return inspect_import(result, store)


def test_seven_canary_pages_pass_import_gate(tmp_path):
    manifest = json.loads((CANARY / "manifest.json").read_text(encoding="utf-8"))
    reports = []
    for item in manifest["pages"]:
        report = _import(tmp_path / item["file"], item["file"], item["url"])
        reports.append(report)
        assert report["pass"] is True, report
    assert len(reports) == 7
    assert all(item["raw_html_exists"] for item in reports)
    assert all(item["images_in_guide_assets"] for item in reports)


def test_ads_are_not_mixed_into_guide_images(tmp_path):
    report = _import(tmp_path, "ads_heavy.html", "https://www.gamersky.com/handbook/ads.shtml")
    assert report["pass"] is True
    images = [b for b in report["blocks"] if b["type"] == "image"]
    srcs = [b.get("src", "") for b in images]
    assert all("ads/" not in (b.get("src") or "") and "广告" not in (b.get("alt") or "") for b in images)
    assert any("yaodu-1.png" in src for src in srcs)
    assert report["ad_images_dropped"] >= 1


def test_qa_fails_when_recorded_asset_file_is_missing(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", tmp_path / "guide-assets" / "sha256")
    html = (CANARY / "17173_multipoint.html").read_text(encoding="utf-8")
    result = import_page(
        html,
        "https://news.17173.com/content/04222026/173231817.shtml",
        db,
        store,
        fetch_asset=lambda src: b"PNG-" + src.encode(),
    )
    assert inspect_import(result, store)["pass"] is True
    for path in store.assets_root.rglob("*"):
        if path.is_file():
            path.unlink()
    report = inspect_import(result, store)
    assert report["pass"] is False
    assert report["images_in_guide_assets"] is False
