"""Text-only approval: grounded steps are enough, chrome and stubs are not."""

import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.review.service import (
    approve_grounded,
    grounded_steps,
    substantive_steps,
)

ARTICLE = "\n".join([
    "第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯",
    "第2个若虫需要从右侧的传送锚点走过去，跳上平台，然后沿着边缘绕到后面即可看到",
])
MAPS = [{"map_id": "364", "name": "雅努萨波利斯_中下房间（黎明）", "path": "翁法罗斯 / 雅努萨波利斯"}]


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, text=ARTICLE, url="https://t.test/a"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "若虫位置", "author": "作者"})
    path = db.path.parent / f"page-{page['id']}.txt"
    path.write_text(text, encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_text_path = ? WHERE id = ?", (str(path), page["id"]))
    db.conn.commit()
    return page


def _item(db, page, *, steps, kind="MAP_LABEL", target_key="map:364:topic:nymph", map_name=None, candidates=None, status="NEEDS_REVIEW"):
    draft = {
        "schema_version": 2,
        "topic_key": "nymph",
        "target_type": kind,
        "target_key": target_key,
        "map_name": map_name if map_name is not None else MAPS[0]["name"],
        "steps": [{"text": text, "images": []} for text in steps],
        "images": [],
        "candidate_points": candidates or [],
    }
    cur = db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, draft_json) VALUES (?, 'ingest', ?, datetime('now'), ?)",
        (page["id"], status, json.dumps(draft, ensure_ascii=False)),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def test_substantive_steps_drop_bylines_and_short_labels():
    steps = [
        {"text": "来源：米游社"},
        {"text": "作者：祈鸢ya"},
        {"text": "铁卫禁区"},
        {"text": "第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯"},
    ]
    kept = substantive_steps(steps)
    assert kept == ["第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯"]


    grounded, invented = grounded_steps(kept + ["凭空捏造的第三步"], ARTICLE)
    assert grounded == kept and invented == ["凭空捏造的第三步"]


def test_substantive_steps_drop_the_embedded_route_data():
    """九游 ships its map's edge list inside the article: data, not instructions."""
    steps = [
        {"text": "t2627_2_2627_1:5.0"},
        {"text": "t52-t0:947.0"},
        {"text": "tgamedetail_ff_2-tgamedetail_ff_1:31.0"},
        {"text": "黄金替罪羊①：按照左左右右右的顺序操作机关即可点亮祭坛"},
        {"text": "影子出现后：右、右、左、右、右、右。"},
    ]
    kept = substantive_steps(steps)
    assert kept == [
        "黄金替罪羊①：按照左左右右右的顺序操作机关即可点亮祭坛",
        "影子出现后：右、右、左、右、右、右。",
    ]


def test_text_only_draft_with_map_name_binding_is_approved(tmp_path):
    db = _db(tmp_path)
    page = _page(db)
    item_id = _item(
        db,
        page,
        steps=[
            "第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯",
            "第2个若虫需要从右侧的传送锚点走过去，跳上平台，然后沿着边缘绕到后面即可看到",
        ],
    )
    dry = approve_grounded(db, topic="nymph", maps=MAPS)
    assert dry["applied"] is False and dry["targets"] == 1
    assert dry["approved"][0]["binding"] == "MAP_NAME"
    assert dry["approved"][0]["item_id"] == item_id

    applied = approve_grounded(db, topic="nymph", maps=MAPS, apply=True)
    entry_id = applied["approved"][0]["entry_id"]
    assert entry_id
    entry = dict(db.conn.execute("SELECT * FROM guide_entry WHERE id = ?", (entry_id,)).fetchone())
    assert entry["source_point_id"] == "map:364:topic:nymph"
    steps = [row["text"] for row in db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ? ORDER BY step_index", (entry_id,))]
    assert steps == [
        "第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯",
        "第2个若虫需要从右侧的传送锚点走过去，跳上平台，然后沿着边缘绕到后面即可看到",
    ]
    draft = json.loads(db.conn.execute("SELECT draft_json FROM review_item WHERE id = ?", (item_id,)).fetchone()["draft_json"])
    assert draft["binding_method"] == "MAP_NAME"
    db.close()


def test_point_draft_needs_exactly_one_candidate(tmp_path):
    db = _db(tmp_path)
    page = _page(db)
    long_step = "第2个若虫需要从右侧的传送锚点走过去，跳上平台，然后沿着边缘绕到后面即可看到"
    single = _item(
        db,
        page,
        steps=[long_step],
        kind="POINT",
        target_key="",
        candidates=[{"source_point_id": "9001", "score": 0.9}],
    )
    _item(
        db,
        page,
        steps=[long_step],
        kind="POINT",
        target_key="",
        candidates=[{"source_point_id": "9002"}, {"source_point_id": "9003"}],
    )
    report = approve_grounded(db, topic="nymph", maps=MAPS, apply=True)
    assert report["targets"] == 1
    assert report["approved"][0]["item_id"] == single
    assert report["approved"][0]["target_key"] == "9001"
    assert report["approved"][0]["binding"] == "CANDIDATE_POINT"
    db.close()


def test_invented_steps_and_stubs_are_rejected(tmp_path):
    db = _db(tmp_path)
    page = _page(db)
    invented = _item(db, page, steps=["第9个若虫在月球背面", "第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯"])
    stub = _item(db, page, steps=["第1个若虫在雕像后"])
    chrome = _item(db, page, steps=["来源：米游社", "作者：祈鸢ya"])
    report = approve_grounded(db, topic="nymph", maps=MAPS)
    assert report["targets"] == 0
    reasons = {item["item_id"]: item["reason"] for item in report["rejected"]}
    assert reasons[invented] == "UNGROUNDED_STEPS"
    assert reasons[stub] == "STEPS_TOO_THIN"
    assert reasons[chrome] == "NO_SUBSTANTIVE_STEPS"
    rows = {int(row["id"]): row["status"] for row in db.conn.execute("SELECT id, status FROM review_item")}
    assert set(rows.values()) == {"NEEDS_REVIEW"}
    db.close()


def test_one_guide_per_target_and_the_richest_draft_wins(tmp_path):
    db = _db(tmp_path)
    page = _page(db)
    thin = _item(db, page, steps=["第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯"])
    rich = _item(
        db,
        page,
        steps=["第1个若虫在雅努萨波利斯的雕像后面，靠近楼梯", "第2个若虫需要从右侧的传送锚点走过去，跳上平台"],
    )
    # the thin draft is a stub (< 30 normalized chars) and never competes
    report = approve_grounded(db, topic="nymph", maps=MAPS, apply=True)
    assert report["targets"] == 1
    assert report["approved"][0]["item_id"] == rich and report["approved"][0]["steps"] == 2
    rows = {int(row["id"]): row["status"] for row in db.conn.execute("SELECT id, status FROM review_item")}
    assert rows[rich] == "APPROVED" and rows[thin] == "NEEDS_REVIEW"
    db.close()
