"""Guide DB V2 adds source/page/binding tables without breaking local entries."""

from hsrmap.guide_db import GuideDatabase


def test_v2_source_page_and_binding(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source(
        {
            "name": "17173",
            "domain": "news.17173.com",
            "source_kind": "AttributedReprint",
            "adapter_name": "site17173",
            "priority": 40,
        }
    )
    assert source["id"] >= 1
    assert db.list_sources()[0]["domain"] == "news.17173.com"
    page = db.add_page(
        source["id"],
        {
            "canonical_url": "https://news.17173.com/content/example.shtml",
            "title": "星铁4.2版本，6个浮脂溯源解密攻略",
            "author": "祈鸢",
            "source_claim": "米游社",
        },
    )
    assert page["source_id"] == source["id"]
    assert page["crawl_status"] == "PENDING"
    binding = db.bind_point(page["id"], {"source_point_id": "5171", "confidence": 0.91, "status": "auto"})
    assert binding["source_point_id"] == "5171"
    cluster = db.upsert_cluster({"title": "4.2 海原市浮脂", "canonical_page_id": page["id"]})
    db.attach_page_to_cluster(page["id"], cluster["id"])
    assert db.page_cluster(page["id"]) == cluster["id"]


def test_add_page_unknown_does_not_clobber_author(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "17173", "domain": "news.17173.com"})
    first = db.add_page(
        source["id"],
        {"canonical_url": "https://news.17173.com/content/keep.shtml", "title": "浮脂", "author": "祈鸢ya"},
    )
    assert first["author"] == "祈鸢ya"
    again = db.add_page(
        source["id"],
        {"canonical_url": "https://news.17173.com/content/keep.shtml", "title": "浮脂", "author": "unknown"},
    )
    assert again["id"] == first["id"]
    assert again["author"] == "祈鸢ya"


def test_v2_keeps_local_guide_entry(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    entry = db.create_entry(
        {
            "source_point_id": "5171",
            "title": "本地",
            "source_kind": "Local",
            "steps": [{"text": "转一下", "images": []}],
        }
    )
    listed = db.list_for_point("5171")
    assert listed[0]["id"] == entry["id"]
    assert listed[0]["steps"][0]["text"] == "转一下"
