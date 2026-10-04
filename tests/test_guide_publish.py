"""Review queue and publisher write guide_entry only."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publish.publisher import publish_page
from hsrmap.guides.review.queue import approve, list_pending


def test_low_confidence_goes_to_review_high_publishes(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "17173", "domain": "17173.com"})
    page = db.add_page(
        source["id"],
        {"canonical_url": "https://news.17173.com/a", "title": "海原市", "author": "祈鸢ya"},
    )
    published = publish_page(
        db,
        page,
        [
            {"source_point_id": "5171", "heading": "海原市", "confidence": 0.91, "status": "auto"},
            {"source_point_id": "9999", "heading": "未知", "confidence": 0.2, "status": "review"},
        ],
        [{"text": "对准旋转机关", "images": []}],
    )
    assert published == []
    pending = list_pending(db)
    assert len(pending) == 2
    approved = approve(db, pending[0]["id"], "5260", page, [{"text": "人工确认", "images": []}])
    assert approved["source_point_id"] == "5260"
    assert db.list_for_point("5260")[0]["status"] == "published"
