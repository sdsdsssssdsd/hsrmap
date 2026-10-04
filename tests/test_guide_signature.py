"""Canonicalization, unit signature and evidence merge (a1-6 §10–14)."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.review.merge import backfill_signatures, duplicate_groups, merge_duplicates
from hsrmap.guides.review.service import create_item
from hsrmap.guides.signature import (
    article_family,
    canonical_host,
    canonical_url,
    instruction_key,
    normalize_text,
    unit_signature,
)


def test_mirror_hosts_and_subdomains_collapse():
    assert canonical_host("m.3dmgame.com") == "3dmgame.com"
    assert canonical_host("app.3dmgame.com") == "3dmgame.com"
    assert canonical_host("www.9game.cn") == "9game.cn"
    assert canonical_host("news.17173.com") == "news.17173.com"
    desktop = article_family("https://ol.3dmgame.com/gl/319072.html")
    mobile = article_family("https://m.3dmgame.com/ol/gl/319072.html")
    app = article_family("https://app.3dmgame.com/gl/319072.html")
    assert desktop == mobile == app == "3dmgame.com/319072"


def test_pagination_and_tracking_collapse_into_one_family():
    assert article_family("https://www.gamersky.com/handbook/202304/1592527.shtml") == "gamersky.com/1592527"
    assert article_family("https://www.gamersky.com/handbook/202304/1592527_3.shtml") == "gamersky.com/1592527"
    assert (
        article_family("https://news.17173.com/content/04022026/111240224.shtml?spm_id_from=333.788")
        == "17173.com/111240224"
    )
    assert canonical_url("https://x.test/a.html?utm_source=y#frag") == "x.test/a.html"


def test_normalization_ignores_width_case_space_and_punctuation():
    assert normalize_text("  第 一 步： 转！ ") == normalize_text("第一步:转!")
    assert normalize_text("ＡＢＣ") == "abc"
    assert instruction_key([{"text": "逆时针转"}, {"text": "再向右"}]) == instruction_key(
        [{"text": "逆时针转再向右"}]
    )


def test_signature_is_deterministic_and_sensitive():
    base = unit_signature("jump", "point:1", [{"text": "转"}], ["a" * 64])
    assert base == unit_signature("jump", "point:1", [{"text": " 转！ "}], ["a" * 64])
    assert base == unit_signature("jump", "point:1", [{"text": "转"}], ["a" * 64, "a" * 64])
    assert base != unit_signature("jump", "point:2", [{"text": "转"}], ["a" * 64])
    assert base != unit_signature("jump", "point:1", [{"text": "推"}], ["a" * 64])
    assert base != unit_signature("jump", "point:1", [{"text": "转"}], ["b" * 64])
    assert len(base) == 64


def _page(db, url, title="攻略"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    return db.add_page(source["id"], {"canonical_url": url, "title": title})


def _item(db, page_id, *, status="NEEDS_REVIEW", target="point:1", text="转"):
    return create_item(
        db,
        {
            "page_id": page_id,
            "source_point_id": "1",
            "status": status,
            "draft": {
                "topic_key": "jump",
                "target_key": target,
                "steps": [{"text": text}],
                "images": ["a" * 64],
                "evidence": {"region_match": {"map": "海原市"}},
            },
        },
    )


def test_duplicate_groups_span_articles_and_pages(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    p1 = _page(db, "https://ol.3dmgame.com/gl/319072.html")
    p2 = _page(db, "https://m.3dmgame.com/ol/gl/319072.html")
    p3 = _page(db, "https://news.17173.com/content/04022026/111240224.shtml")
    first = _item(db, p1["id"], status="AUTO_SUGGEST")
    _item(db, p2["id"])
    _item(db, p3["id"])
    assert first["signature"]
    groups = duplicate_groups(db)
    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 3
    db.close()


def test_merge_keeps_one_object_with_all_evidence(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    p1 = _page(db, "https://ol.3dmgame.com/gl/319072.html")
    p2 = _page(db, "https://m.3dmgame.com/ol/gl/319072.html")
    plain = _item(db, p1["id"], status="NEEDS_REVIEW")
    suggestion = _item(db, p2["id"], status="AUTO_SUGGEST")
    report = merge_duplicates(db, apply=True)
    assert report["merged_items"] == 1
    rows = {int(row["id"]): dict(row) for row in db.conn.execute("SELECT * FROM review_item")}
    folded = rows[int(plain["id"])]
    survivor = rows[int(suggestion["id"])]
    assert folded["status"] == "MERGED"
    assert folded["reason"] == f"merged_into:{suggestion['id']}"
    # a suggestion is never folded into a plain review item
    assert survivor["status"] == "AUTO_SUGGEST"
    import json

    evidence = json.loads(survivor["evidence_json"])
    assert evidence["merged_count"] == 2
    assert {entry["item_id"] for entry in evidence["merged_sources"]} == {int(plain["id"]), int(suggestion["id"])}
    assert json.loads(survivor["draft_json"])["topic_key"] == "jump"
    second = merge_duplicates(db, apply=True)
    assert second["merged_items"] == 0
    db.close()


def test_merge_prefers_the_strongest_status_as_keeper(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    p1 = _page(db, "https://ol.3dmgame.com/gl/319072.html")
    p2 = _page(db, "https://m.3dmgame.com/ol/gl/319072.html")
    plain = _item(db, p1["id"], status="NEEDS_REVIEW")
    suggestion = _item(db, p2["id"], status="AUTO_SUGGEST")
    merge_duplicates(db, apply=True)
    rows = {int(row["id"]): dict(row) for row in db.conn.execute("SELECT * FROM review_item")}
    assert rows[int(suggestion["id"])]["status"] == "AUTO_SUGGEST"
    assert rows[int(plain["id"])]["status"] == "MERGED"
    assert sum(1 for row in rows.values() if row["status"] == "MERGED") == 1
    db.close()


def test_approved_items_are_never_merged(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    p1 = _page(db, "https://ol.3dmgame.com/gl/319072.html")
    p2 = _page(db, "https://m.3dmgame.com/ol/gl/319072.html")
    approved = _item(db, p1["id"], status="APPROVED")
    _item(db, p2["id"], status="NEEDS_REVIEW")
    report = merge_duplicates(db, apply=True)
    assert report["merged_items"] == 0
    row = db.conn.execute("SELECT status FROM review_item WHERE id = ?", (approved["id"],)).fetchone()
    assert row["status"] == "APPROVED"
    db.close()


def test_backfill_signatures_for_legacy_rows(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://ol.3dmgame.com/gl/319072.html")
    item = _item(db, page["id"])
    db.conn.execute("UPDATE review_item SET signature = NULL WHERE id = ?", (item["id"],))
    db.conn.commit()
    dry = backfill_signatures(db, apply=False)
    assert dry["missing"] == 1
    assert db.conn.execute("SELECT signature FROM review_item WHERE id = ?", (item["id"],)).fetchone()["signature"] is None
    backfill_signatures(db, apply=True)
    assert db.conn.execute("SELECT signature FROM review_item WHERE id = ?", (item["id"],)).fetchone()["signature"]
    db.close()
