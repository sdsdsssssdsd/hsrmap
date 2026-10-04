"""Thin drafts are re-ingested before they are written off."""

import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.revive import revive_candidates, revive_thin
from hsrmap.guides.review.service import MIN_REGION_SET_STEPS

POINTS = [
    {"source_point_id": "8101", "map_id": "960", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
    {"source_point_id": "8102", "map_id": "960", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
    {"source_point_id": "8103", "map_id": "960", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
]

ARTICLE = "\n".join([
    "「甲区」一处共有3个浮脂溯源解密，下面逐个说明",
    "第1个：把黄色模块推到右上角的凹槽里，浮脂就会顺着轨道滑下去",
    "第2个：转动中间的镜子两次，让光束照到对面的浮脂上",
    "第3个：把左侧的方块移到最下方，再踩一次机关即可完成",
])


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, text, url="https://t.test/thin"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "甲区浮脂溯源", "author": "作者"})
    path = db.path.parent / ("page-%d.txt" % page["id"])
    path.write_text(text, encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_text_path = ? WHERE id = ?", (str(path), page["id"]))
    db.conn.commit()
    return page


def _item(db, page, steps, topic="floating_grease", heading="「甲区」一处"):
    draft = {
        "schema_version": 2,
        "topic_key": topic,
        "map_name": heading,
        "steps": [{"text": text, "images": []} for text in steps],
        "images": [],
        "candidate_points": [],
    }
    cursor = db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, draft_json)"
        " VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), ?)",
        (page["id"], json.dumps(draft, ensure_ascii=False)),
    )
    db.conn.commit()
    return int(cursor.lastrowid)


def test_a_thin_page_with_a_known_binding_is_a_candidate(tmp_path):
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    _item(db, page, [ARTICLE.split("\n")[0]])  # only the scope line made it into a step

    found = revive_candidates(db, points_by_topic={"floating_grease": POINTS})
    assert len(found) == 1
    assert found[0]["page_id"] == int(page["id"])
    assert found[0]["new_points"] == 3
    assert found[0]["steps"] < MIN_REGION_SET_STEPS
    assert found[0]["target"] == "set:8101-8102-8103:topic:floating_grease"
    db.close()


def test_a_page_that_already_has_a_rich_draft_is_not_a_candidate(tmp_path):
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    _item(db, page, [line for line in ARTICLE.split("\n") if line])
    assert revive_candidates(db, points_by_topic={"floating_grease": POINTS}) == []
    db.close()


def test_apply_reingests_and_reports_what_it_unlocked(tmp_path, monkeypatch):
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    item_id = _item(db, page, [ARTICLE.split("\n")[0]])
    calls = []

    def fake_reingest(database, page_id, *, topic="", **kwargs):
        calls.append((int(page_id), topic))
        draft = json.loads(database.conn.execute(
            "SELECT draft_json FROM review_item WHERE id = ?", (item_id,)
        ).fetchone()["draft_json"])
        draft["steps"] = [{"text": line, "images": []} for line in ARTICLE.split("\n") if line]
        database.conn.execute(
            "UPDATE review_item SET draft_json = ? WHERE id = ?",
            (json.dumps(draft, ensure_ascii=False), item_id),
        )
        database.conn.commit()
        return {"qa_status": "QA_PASS", "review_items": 1, "assets": 7}

    monkeypatch.setattr("hsrmap.guides.revive.reingest_page", fake_reingest)
    report = revive_thin(db, apply=True, points_by_topic={"floating_grease": POINTS})

    assert calls == [(int(page["id"]), "floating_grease")]
    assert report["candidates"] == 1 and report["revived"] == 1 and report["still_thin"] == 0
    assert report["revived_points"] == 3
    assert report["entries"][0]["after"]["steps"] >= MIN_REGION_SET_STEPS
    db.close()
