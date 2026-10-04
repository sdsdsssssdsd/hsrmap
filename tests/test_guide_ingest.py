"""ingest imports + queues review and never publishes."""

from pathlib import Path

from hsrmap.cli import main
import pytest

pytestmark = pytest.mark.data

CANARY = Path(__file__).parent / "fixtures" / "guides" / "canary" / "17173_multipoint.html"


def test_ingest_does_not_publish(tmp_path, capsys):
    code = main(
        [
            "guides",
            "ingest",
            str(CANARY),
            "--url",
            "https://news.17173.com/content/04222026/173231817.shtml",
            "--provider",
            "fake",
            "--db",
            str(tmp_path / "guide.db"),
            "--raw",
            str(tmp_path / "guides"),
            "--assets",
            str(tmp_path / "ga"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "APPROVED" not in out
    assert "publish" in out.lower()
    assert "NEEDS_REVIEW" in out or "AUTO_SUGGEST" in out or "review" in out.lower()
    assert "pending" not in out.lower()


def test_ingest_cli_attaches_official_points_for_topic(tmp_path, capsys):
    import json

    from hsrmap.guide_db import GuideDatabase

    html = Path(__file__).parent / "fixtures" / "guides" / "canary" / "ticker_pinocchio.html"
    code = main(
        [
            "guides",
            "ingest",
            str(html),
            "--url",
            "https://www.gamersky.com/handbook/202402/1708650.shtml",
            "--topic",
            "dream-ticker",
            "--provider",
            "fake",
            "--db",
            str(tmp_path / "guide.db"),
            "--raw",
            str(tmp_path / "guides"),
            "--assets",
            str(tmp_path / "ga"),
        ]
    )
    assert code == 0
    db = GuideDatabase(tmp_path / "guide.db")
    rows = list(db.conn.execute("SELECT draft_json FROM review_item"))
    db.close()
    drafts = [json.loads(row["draft_json"] or "{}") for row in rows]
    assert drafts
    assert all(d.get("topic_key") == "dream_ticker" for d in drafts)
    hotel = next((d for d in drafts if "酒店-梦境" in str(d.get("map_name") or "") and "1层" in str(d.get("map_name") or "")), None)
    assert hotel is not None
    cands = [str(c.get("source_point_id")) for c in (hotel.get("candidate_points") or []) if isinstance(c, dict)]
    assert "1709" in cands
