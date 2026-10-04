"""Adapters parse site fixtures without hitting the network."""

from pathlib import Path

from hsrmap.guides.crawler.base import adapter_for

FIX = Path(__file__).parent / "fixtures" / "guides"


def test_17173_and_taptap_parse_author_and_claim():
    html = (FIX / "sample_17173.html").read_text(encoding="utf-8")
    meta = adapter_for("https://news.17173.com/content/04222026/173231817.shtml").parse_page(
        html, "https://news.17173.com/content/04222026/173231817.shtml?utm=x"
    )
    assert meta["title"].startswith("星铁4.2")
    assert meta["author"] == "祈鸢ya"
    assert meta["source_claim"] == "米游社"
    assert meta["canonical_url"].endswith(".shtml")

    tap = (FIX / "sample_taptap.html").read_text(encoding="utf-8")
    tmeta = adapter_for("https://www.taptap.cn/moment/786205107434815910").parse_page(
        tap, "https://www.taptap.cn/moment/786205107434815910"
    )
    assert tmeta["author"] == "祈鸢ya"
    assert adapter_for("https://www.taptap.cn/moment/1").source_kind == "OriginalCommunityPost"


def test_gamersky_and_3dm_adapters():
    gs = adapter_for("https://www.gamersky.com/handbook/202604/1.shtml")
    meta = gs.parse_page((FIX / "sample_gamersky.html").read_text(encoding="utf-8"), "https://www.gamersky.com/handbook/202604/1.shtml")
    assert gs.name == "GamerSky"
    assert "浮脂" in meta["title"]
    dm = adapter_for("https://www.3dmgame.com/gl/1.html")
    assert dm.name == "3DM"
    assert dm.domain == "3dmgame.com"
