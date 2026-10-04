"""Rebuilding quarantined guides from the article that really covers them."""

import json

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.audit import audit_entries
from hsrmap.guides.publishing.diff import entry_snapshot
from hsrmap.guides.rebuild import article_steps, rebuild_quarantined, target_keys

ARTICLE = "\n".join([
    "《崩坏星穹铁道》朝露公馆全梦境迷钟解密攻略",
    "《崩坏星穹铁道》朝露公馆是2.1版本新开放的区域，下面请看小编为大家带来的攻略，希望能够帮助大家。",
    "第1个【梦境迷钟】修复解密",
    "（1）将1蓝色模块点击旋转2次，【钟表小子】会从位置1到位置2",
    "（2）将1蓝色模块点击旋转3次，将2镜子往右下移动，解密完成",
    "第2个【梦境迷钟】修复解密",
    "（1）将橙色模块点击旋转1次，【钟表小子】会从位置1到位置2",
    "以上就是朝露公馆全部梦境迷钟的位置。",
])

POINTS = {
    "dream_ticker": [
        {
            "source_point_id": "2124",
            "map_id": "26",
            "map_name": "朝露公馆",
            "region": "匹诺康尼",
            "label": "梦境迷钟",
        }
    ]
}
MAPS = {"origami_bird": [{"map_id": "150", "name": "筑梦边境"}]}
DISPLAYS = {"dream_ticker": "梦境迷钟", "origami_bird": "折纸小鸟"}


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, url, text, *, html="<html><body><p>x</p></body></html>", title="标题"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": title, "author": "作者"})
    text_path = tmp_path_file = db.path.parent / f"page-{page['id']}.txt"
    text_path.write_text(text, encoding="utf-8")
    html_path = db.path.parent / f"page-{page['id']}.html"
    html_path.write_text(html, encoding="utf-8")
    db.conn.execute(
        "UPDATE guide_page SET raw_text_path = ?, raw_html_path = ? WHERE id = ?",
        (str(text_path), str(html_path), page["id"]),
    )
    db.conn.commit()
    return page


def _quarantined(db, url, target, title="攻略"):
    guide = db.create_entry(
        {
            "source_point_id": target,
            "title": title,
            "status": "published",
            "source_url": url,
            "steps": [{"text": "本周5776人已下载", "images": []}],
        }
    )
    db.conn.execute("UPDATE guide_entry SET status = 'QUARANTINED_CHROME' WHERE id = ?", (guide["id"],))
    db.conn.commit()
    return guide


def test_target_keys_are_target_specific():
    point = target_keys({"source_point_id": "2124"}, display="梦境迷钟", points=POINTS["dream_ticker"])
    assert point["strong"] == ["朝露公馆", "匹诺康尼", "梦境迷钟"]
    assert point["weak"] == ["梦境迷钟"]

    # a map target is identified by its map, never by every point of the topic
    amap = target_keys(
        {"source_point_id": "map:150:topic:origami_bird"},
        display="折纸小鸟",
        points=POINTS["dream_ticker"],
        maps=MAPS["origami_bird"],
    )
    assert amap["strong"] == ["筑梦边境"]

    # a one-letter map name is not evidence
    assert target_keys({"source_point_id": "map:9:topic:t"}, maps=[{"map_id": "9", "name": "1"}])["strong"] == []


def test_article_steps_keep_the_heading_and_its_instructions():
    steps = article_steps(ARTICLE, ["朝露公馆", "梦境迷钟"], title="《崩坏星穹铁道》朝露公馆全梦境迷钟解密攻略-游民星空")
    # the page headline is never a step
    assert steps[0] == "第1个【梦境迷钟】修复解密"
    assert steps[1].startswith("（1）将1蓝色模块点击旋转2次")
    assert steps[2].startswith("（2）将1蓝色模块点击旋转3次")
    assert steps[3] == "第2个【梦境迷钟】修复解密"
    # the title and the intro boilerplate are never steps
    assert not any("请看" in step for step in steps)
    assert not any(step.startswith("《崩坏星穹铁道》朝露公馆全梦境迷钟") for step in steps)
    assert not any("以上就是" in step for step in steps)


def test_rebuild_publishes_only_what_the_article_supports(tmp_path):
    db = _db(tmp_path)
    good = _page(db, "https://t.test/good", ARTICLE, title="《崩坏星穹铁道》朝露公馆全梦境迷钟解密攻略-游民星空")
    quiet = _page(db, "https://t.test/quiet", "本页只有站点导航，没有任何攻略内容。" * 4)
    other = _page(db, "https://t.test/other", "流梦礁有两个王下一桶，找到并与其完成对话就可以获得星琼。" * 3)
    first = _quarantined(db, good["canonical_url"], "2124")
    second = _quarantined(db, quiet["canonical_url"], "2448")
    third = _quarantined(db, other["canonical_url"], "map:150:topic:origami_bird")

    dry = rebuild_quarantined(db, points=POINTS, maps=MAPS, displays=DISPLAYS)
    assert dry["applied"] is False and dry["rebuilt"] == 1 and dry["skipped"] == 2
    assert [item["guide_id"] for item in dry["entries"]] == [first["id"]]
    reasons = {item["guide_id"]: item["reason"] for item in dry["unresolved"]}
    # the quiet page has no official metadata to look for, the other one never
    # mentions the map it was matched to
    assert reasons[second["id"]] == "NO_TARGET_KEYS"
    assert reasons[third["id"]] == "NO_ARTICLE_EVIDENCE"

    applied = rebuild_quarantined(db, points=POINTS, maps=MAPS, displays=DISPLAYS, apply=True)
    assert applied["rebuilt"] == 1
    row = db.conn.execute("SELECT status FROM guide_entry WHERE id = ?", (first["id"],)).fetchone()
    assert row["status"] == "published"
    steps = [
        str(item["text"])
        for item in db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ? ORDER BY step_index", (first["id"],))
    ]
    assert steps and all(step in ARTICLE for step in steps)
    still = {
        int(item["id"]): item["status"]
        for item in db.conn.execute("SELECT id, status FROM guide_entry WHERE id IN (?, ?)", (second["id"], third["id"]))
    }
    assert set(still.values()) == {"QUARANTINED_CHROME"}

    # what was rebuilt must pass the published audit unchanged
    report = audit_entries(entry_snapshot(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 0
    assert report["counts"]["CHROME_STEP"] == 0
    db.close()


def test_a_page_without_article_text_is_reported_as_such(tmp_path):
    db = _db(tmp_path)
    empty = _page(db, "https://t.test/empty", "只有标题")
    guide = _quarantined(db, empty["canonical_url"], "2124")
    result = rebuild_quarantined(db, points=POINTS, maps=MAPS, displays=DISPLAYS)
    assert result["rebuilt"] == 0
    assert result["unresolved"][0]["guide_id"] == guide["id"]
    assert result["unresolved"][0]["reason"] == "NO_ARTICLE_TEXT"
    db.close()


def test_cli_rebuild_quarantined_reports(tmp_path, capsys):
    db_path = tmp_path / "guide.db"
    db = GuideDatabase(db_path)
    quiet = _page(db, "https://t.test/quiet", "站点导航" * 20)
    _quarantined(db, quiet["canonical_url"], "5016")
    db.close()
    assert main(["guides", "rebuild-quarantined", "--db", str(db_path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["checked"] == 1 and body["rebuilt"] == 0 and body["skipped"] == 1
    assert body["unresolved"][0]["reason"] in {"NO_TARGET_KEYS", "NO_ARTICLE_TEXT", "NO_ARTICLE_EVIDENCE"}
