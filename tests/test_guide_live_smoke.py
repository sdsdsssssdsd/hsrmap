"""Live smoke: the smallest real-network check (a1-6 §二十, Sprint 4).

These tests are skipped unless `pytest --run-live` is passed, because they talk
to real hosts. They are deliberately tiny: one host, two pages, no full crawl.
"""

from pathlib import Path

import pytest

from hsrmap.guide_db import GuideDatabase

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[1]
GUIDE_DB = ROOT / "data" / "guides" / "guide.db"


def test_live_asset_smoke_on_one_host():
    """The stored pages of one host still yield fetchable images."""
    from hsrmap.guides.assets.smoke import smoke_matrix, verdict

    if not GUIDE_DB.exists():
        pytest.skip("SKIPPED: data/guides/guide.db unavailable")
    db = GuideDatabase(GUIDE_DB)
    try:
        matrix = smoke_matrix(db, ["3dmgame.com"], limit=2)
    finally:
        db.close()
    row = matrix["hosts"][0]
    assert row["pages"] >= 1, "no stored 3dmgame page to smoke"
    assert row["images"] >= 1, "no image URL found on the stored pages"
    case = verdict(matrix)["3dmgame.com"]
    assert case.startswith(("A:", "B:")), f"assets are blocked: {case}"


def test_live_robots_and_page_fetch():
    """robots.txt is readable and one article page comes back as HTML."""
    from hsrmap.guides.crawler.fetch import fetch_page
    from hsrmap.guides.crawler.robots import path_allowed
    from hsrmap.http import RateLimitedClient

    client = RateLimitedClient(min_interval=1.0)
    robots = client.get("https://www.gamersky.com/robots.txt")
    text = robots.body.decode("utf-8", errors="replace")
    assert "User-agent" in text or "user-agent" in text.lower()
    assert isinstance(path_allowed(text, "/handbook/"), bool)

    fetched = fetch_page("https://www.gamersky.com/handbook/202404/1729233.shtml", client)
    assert fetched["status"] == "ok"
    assert len(fetched["html"]) > 500
