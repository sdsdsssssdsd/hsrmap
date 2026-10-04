"""Region guides become POINT_SET entries, and coverage counts their members."""

import json
import sqlite3

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ledger import expand_point_key, published_point_ids, topic_ledger
from hsrmap.guides.publishing.diff import validate_bindings
from hsrmap.guides.review.service import (
    anchored_count,
    approve_grounded,
    declared_count,
    distributive_count,
    enumeration_count,
    label_variants,
    line_ordinal_count,
    location_statement_count,
    near_count,
    partition_counts,
    region_candidates,
    region_scope_count,
)

POINTS = [
    {"source_point_id": "4139", "map_id": "900", "map_name": "哀丽秘榭", "region": "哀丽秘榭", "label": "黄金替罪羊"},
    {"source_point_id": "4142", "map_id": "900", "map_name": "哀丽秘榭", "region": "哀丽秘榭", "label": "黄金替罪羊"},
    {"source_point_id": "3632", "map_id": "901", "map_name": "1层", "region": "「半神议院」黎明云崖", "label": "黄金替罪羊"},
    {"source_point_id": "4048", "map_id": "902", "map_name": "1层", "region": "「无晖祈堂」黎明云崖", "label": "黄金替罪羊"},
]

ARTICLE = "\n".join([
    "崩坏星穹铁道哀丽秘榭地图共两个黄金替罪羊，玩家只需按照正确步骤移动即可通关",
    "第1个：先向左移动两次，再向右移动一次即可点亮祭坛",
    "第2个：先向下移动一次，再向左移动三次即可点亮祭坛",
])


def test_a_bracketed_region_count_wins_over_the_page_total():
    """「【稚子的梦】2个位置」——区域自己的数字优先于页面的「共8个」。

    页面总数说的是四个区域加起来；用 8 去对一个只有 2 个点位的区域，永远对不上，
    于是那条分节一条也绑不出来（王下一桶的稚子的梦就是这么被挡住的）。
    """
    from hsrmap.guides.review.service import bracketed_count

    assert bracketed_count("【稚子的梦】2个位置", "稚子的梦") == 2
    assert bracketed_count("3.【稚子的梦】2处: ⑤有点绕的路", "稚子的梦") == 2
    assert bracketed_count("「白日梦」酒店-梦境2个", "「白日梦」酒店-梦境") == 2
    # 没有括号不认：那可能是别人的句子
    assert bracketed_count("稚子的梦有2个", "稚子的梦") == 0
    # 名字对不上不认
    assert bracketed_count("【筑梦边境】2个", "稚子的梦") == 0

    regions = {"稚子的梦": ["1619", "1667"]}
    article = (
        "任务描述:挑战「白日梦」酒店、黄金的时刻、稚子的梦、筑梦边境中全部的【王下一桶】(共8个)"
        " 【稚子的梦】2个位置 位置1 位置2"
    )
    assert region_scope_count(article, regions, ["王下一桶"]) == (2, "anchored")


def test_a_heading_scoped_enumeration_counts_the_region(tmp_path):
    """小标题点名区域、正文「一、第1个 … 四、第4个」逐个编号：编号完整就是计数。

    游侠的「全世矩阵无名泰坦大墓黄金替罪羊」就是这么写的——主题名在标题里，
    旧规则要求每个编号紧贴主题名，于是那 4 个点位一条也绑不上。
    """
    from hsrmap.guides.review.service import _region_set_binding

    points = [
        {"source_point_id": "43%02d" % i, "map_id": "469", "map_name": "1层", "region": "「全世矩阵」无名泰坦大墓", "label": "黄金替罪羊"}
        for i in range(78, 82)
    ]
    article = "\n".join([
        "崩坏星穹铁道全世矩阵无名泰坦大墓黄金替罪羊怎么过",
        "一、第1个：先向左移动两次再向右移动一次",
        "二、第2个：下下右右上上",
        "三、第3个：左右左右",
        "四、第4个：上上下下左左右右",
    ])
    draft = {
        "map_name": "崩坏星穹铁道全世矩阵无名泰坦大墓黄金替罪羊怎么过",
        "steps": [{"text": line} for line in article.split("\n")[1:]],
    }
    binding, target = _region_set_binding(
        draft, "golden_scapegoat", points, article, draft["map_name"] + " " + " ".join(s["text"] for s in draft["steps"])
    )
    assert binding == "REGION_SET"
    assert target == "set:4378-4379-4380-4381:topic:golden_scapegoat"


def test_an_incomplete_enumeration_still_counts_as_nothing():
    """缺号（1、2、4）说明这不是在数这一区的谜题，不能当计数用。"""
    from hsrmap.guides.review.service import _region_set_binding

    points = [
        {"source_point_id": "43%02d" % i, "map_id": "469", "map_name": "1层", "region": "「全世矩阵」无名泰坦大墓", "label": "黄金替罪羊"}
        for i in range(78, 82)
    ]
    article = "\n".join([
        "全世矩阵无名泰坦大墓黄金替罪羊",
        "一、第1个：左左右右",
        "二、第2个：下下右右",
        "四、第4个：上上左左",
    ])
    draft = {"map_name": "全世矩阵无名泰坦大墓黄金替罪羊", "steps": [{"text": line} for line in article.split("\n")[1:]]}
    binding, target = _region_set_binding(
        draft, "golden_scapegoat", points, article, draft["map_name"] + " " + " ".join(s["text"] for s in draft["steps"])
    )
    assert (binding, target) == ("", "")


def test_a_slice_of_a_globally_numbered_page_counts_its_own_run():
    """一页把整张图的谜题编号 1..10，分节只拿到连续一段（苏乐达热砂海选会场=第4..6个）。

    游民星空 2.2 那篇就是这种写法：编号是全文的，区域名是小标题。默认仍然不认这种
    编号（见 test_enumeration_count_reads_a_walkthroughs_own_numbering），只有
    「小标题已经点名区域」的分节才允许按连续段计数——缺号或重复依旧不算。
    """
    assert enumeration_count("第4个 第5个 第6个", ["梦境迷钟"], require_label_adjacent=False) == 0
    assert (
        enumeration_count("第4个 第5个 第6个", ["梦境迷钟"], require_label_adjacent=False, allow_run=True) == 3
    )
    assert (
        enumeration_count("第4个 第5个 第7个", ["梦境迷钟"], require_label_adjacent=False, allow_run=True) == 0
    )

    from hsrmap.guides.review.service import _region_set_binding

    points = [
        {"source_point_id": pid, "map_id": "905", "map_name": "1层", "region": region, "label": "梦境迷钟"}
        for pid, region in (("2245", "苏乐达-1号-左"), ("2253", "苏乐达-1号-右"), ("2300", "苏乐达-2号-右"))
    ]
    article = "\n".join([
        "苏乐达热砂海选会场",
        "第4个",
        "1.将1蓝色模块点击旋转2次，将2黄色模块移到右下，【钟表小子】会从1位置到2位置。",
        "第5个",
        "1.将1蓝色模块点击旋转3次，将2橙色模块点击旋转3次，【钟表小子】会从1位置到2位置。",
        "第6个",
        "1.将1镜子移到左下，将2黄色模块移到左下，【钟表小子】会从1位置到2位置。",
    ])
    draft = {"map_name": "苏乐达热砂海选会场", "steps": [{"text": line} for line in article.split("\n")[1:]]}
    binding, target = _region_set_binding(
        draft,
        "dream_ticker",
        points,
        article,
        draft["map_name"] + " " + " ".join(step["text"] for step in draft["steps"]),
    )
    assert binding == "REGION_SET"
    assert target == "set:2245-2253-2300:topic:dream_ticker"


def test_a_region_count_written_with_you_binds_only_with_the_topic_name():
    """「晖长石号中有4个梦境迷钟」算 4；「甲区有3个宝箱」不算——数字后面得是主题名。"""
    from hsrmap.guides.review.service import named_topic_count

    assert named_topic_count("2.3版本新地图晖长石号中有4个梦境迷钟", "晖长石号", ["梦境迷钟"]) == 4
    assert named_topic_count("晖长石号共有4个梦境迷钟", "晖长石号", ["梦境迷钟"]) == 4
    assert named_topic_count("甲区有3个宝箱", "甲区", ["梦境迷钟"]) == 0
    assert named_topic_count("乙区的宝箱有3个", "乙区", ["梦境迷钟"]) == 0


def test_declared_count_reads_arabic_and_chinese_numbers():
    assert declared_count("一、黄金替罪羊解谜宝箱(一共3个)") == 3
    assert declared_count("哀丽秘榭地图共两个黄金替罪羊") == 2
    assert declared_count("合计二十三个") == 23
    assert declared_count("共十二个") == 12
    assert declared_count("共十个") == 10
    assert declared_count("这里没有数量") == 0


def test_count_comes_from_the_line_that_names_the_topic():
    page = " ".join([
        "下方收集包含：",
        "①普通战利品(5星琼+30金表钞)*12",
        "②王下一桶(20星琼+120金表钞)*2",
        "③梦境迷钟 ×3",
    ])
    # without a label the page states several numbers and no answer is possible
    assert declared_count(page) == 0
    assert declared_count(page, label="王下一桶") == 2
    assert declared_count("③梦境迷钟 ×3", label="梦境迷钟") == 3
    assert declared_count("王下一桶位置(2个)", label="王下一桶") == 2
    # a page that never names the topic states nothing about its scope
    assert declared_count("位置(2个)", label="王下一桶") == 0

def test_region_candidates_match_bracketed_names_and_their_tail():
    # the bracketed name (without the bracket characters) wins over the loose tail
    found = region_candidates(POINTS, "无晖祈堂黎明云崖的黄金替罪羊关卡现已更新")
    assert list(found) == ["「无晖祈堂」黎明云崖"]
    assert found["「无晖祈堂」黎明云崖"] == ["4048"]
    both = region_candidates(POINTS, "黎明云崖的黄金替罪羊")
    assert set(both) == {"「无晖祈堂」黎明云崖", "「半神议院」黎明云崖"}
    assert region_candidates(POINTS, "完全无关的文本") == {}


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, text, url="https://t.test/aili"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "哀丽秘榭黄金替罪羊", "author": "作者"})
    path = db.path.parent / f"page-{page['id']}.txt"
    path.write_text(text, encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_text_path = ? WHERE id = ?", (str(path), page["id"]))
    db.conn.commit()
    return page


def _item(db, page, steps, map_name="崩坏星穹铁道哀丽秘榭黄金替罪羊攻略", topic="golden_scapegoat"):
    draft = {
        "schema_version": 2,
        "topic_key": topic,
        "target_type": None,
        "target_key": "",
        "map_name": map_name,
        "steps": [{"text": text, "images": []} for text in steps],
        "images": [],
        "candidate_points": [],
    }
    cur = db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, draft_json) VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), ?)",
        (page["id"], json.dumps(draft, ensure_ascii=False)),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def test_region_guide_binds_a_point_set_and_covers_its_members(tmp_path):
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    steps = [line for line in ARTICLE.split("\n") if line]
    item_id = _item(db, page, steps)

    dry = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS)
    assert dry["targets"] == 1
    assert dry["approved"][0]["binding"] == "REGION_SET"
    assert dry["approved"][0]["target_key"] == "set:4139-4142:topic:golden_scapegoat"

    applied = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS, apply=True)
    entry_id = applied["approved"][0]["entry_id"]
    entry = dict(db.conn.execute("SELECT * FROM guide_entry WHERE id = ?", (entry_id,)).fetchone())
    assert entry["source_point_id"] == "set:4139-4142:topic:golden_scapegoat"
    draft = json.loads(db.conn.execute("SELECT draft_json FROM review_item WHERE id = ?", (item_id,)).fetchone()["draft_json"])
    assert draft["target_type"] == "POINT_SET"
    assert draft["member_points"] == ["4139", "4142"]
    assert draft["binding_method"] == "REGION_SET"

    # the ledger expands the set, so both points stop being NEEDS_SOURCE
    assert published_point_ids(db) >= {"4139", "4142"}
    assert expand_point_key(entry["source_point_id"]) == ["4139", "4142"]
    report = topic_ledger(db, "golden_scapegoat", official_points=POINTS)
    statuses = {row["source_point_id"]: row["status"] for row in report["targets"]}
    assert statuses["4139"] == "PUBLISHED" and statuses["4142"] == "PUBLISHED"
    assert statuses["3632"] == "NEEDS_SOURCE"
    assert report["published"] == 2
    db.close()


def test_a_count_that_does_not_match_the_region_is_refused(tmp_path):
    db = _db(tmp_path)
    # the article itself claims three, but 哀丽秘榭 holds exactly two official points
    article = "\n".join([
        "崩坏星穹铁道哀丽秘榭地图一共3个黄金替罪羊，下面分别说明位置",
        "第1个：先向左移动两次，再向右移动一次即可点亮祭坛",
        "第2个：先向下移动一次，再向左移动三次即可点亮祭坛",
    ])
    page = _page(db, article)
    _item(db, page, [line for line in article.split("\n") if line])
    report = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS)
    assert report["targets"] == 0 and report["rejected_count"] == 0
    db.close()


def test_an_ambiguous_region_is_refused(tmp_path):
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    # two sub-regions match "黎明云崖" and together they are not the stated count
    steps = ["黎明云崖的黄金替罪羊一共3个，位置如下", "第1个：向左两次，再向右一次", "第2个：向下一次，再向左三次"]
    _item(db, page, steps, map_name="崩坏星穹铁道黎明云崖黄金替罪羊")
    report = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS)
    assert report["targets"] == 0
    db.close()


class _Core:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE points(source_id TEXT)")
        self.conn.execute("CREATE TABLE maps(source_id TEXT)")
        self.conn.executemany("INSERT INTO points(source_id) VALUES (?)", [("4139",), ("4142",), ("3632",)])
        self.conn.executemany("INSERT INTO maps(source_id) VALUES (?)", [("900",), ("901",)])
        self.conn.commit()


class _Ctx:
    def __init__(self):
        self.core = _Core()


def test_set_keys_are_validated_member_by_member():
    ctx = _Ctx()
    assert validate_bindings({"set:4139-4142:topic:golden_scapegoat"}, ctx) == []
    assert validate_bindings({"map:900:topic:x"}, ctx) == []
    assert validate_bindings({"4139"}, ctx) == []

    broken = validate_bindings({"set:4139-9999:topic:golden_scapegoat"}, ctx)
    assert broken and broken[0]["kind"] == "set" and broken[0]["missing"] == "9999"
    assert validate_bindings({"map:777:topic:x"}, ctx)[0]["kind"] == "map"
    assert validate_bindings({"777"}, ctx)[0]["kind"] == "point"

def test_a_published_target_is_not_duplicated_by_a_thinner_draft(tmp_path):
    """Coverage is about targets: a second, thinner guide adds nothing."""
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    steps = [line for line in ARTICLE.split("\n") if line]
    item_id = _item(db, page, steps)
    target = "set:4139-4142:topic:golden_scapegoat"
    entry = db.create_entry({
        "source_point_id": target,
        "title": "已发布的哀丽秘榭替罪羊",
        "status": "published",
        "source_url": page["canonical_url"],
        "steps": [{"text": text, "images": []} for text in steps],
    })
    report = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS)
    assert report["targets"] == 0
    assert report["rejected"][0]["reason"] == "ALREADY_PUBLISHED"
    assert report["rejected"][0]["item_id"] == item_id
    rows = {int(row["id"]): row["status"] for row in db.conn.execute("SELECT id, status FROM review_item")}
    assert rows[item_id] == "NEEDS_REVIEW"
    assert entry["id"]
    db.close()


def test_a_bracketed_region_is_also_found_by_its_leading_half():
    """Pages write "世界尽头地图共有3个…" for the region "「世界尽头」酒馆"."""
    points = [
        {"source_point_id": "5016", "region": "「世界尽头」酒馆", "label": "浮脂溯源"},
        {"source_point_id": "5017", "region": "「世界尽头」酒馆", "label": "浮脂溯源"},
        {"source_point_id": "5018", "region": "「世界尽头」酒馆", "label": "浮脂溯源"},
        {"source_point_id": "5100", "region": "1层", "label": "浮脂溯源"},
    ]
    found = region_candidates(points, "（4）世界尽头地图共有3个浮脂溯源解密")
    assert list(found) == ["「世界尽头」酒馆"]
    assert found["「世界尽头」酒馆"] == ["5016", "5017", "5018"]
    # a two character half is still too loose to match, and so is a bare number
    assert region_candidates(points, "酒馆里有一个浮脂溯源") == {}
    assert region_candidates(points, "这里没有区域") == {}
    # the whole bracketed name still wins over the loose half
    whole = region_candidates(points, "「世界尽头」酒馆和世界尽头都提到了")
    assert list(whole) == ["「世界尽头」酒馆"]


def test_overlapping_region_sets_keep_the_richest_one(tmp_path):
    """A page covering two areas yields two sets; the weaker overlap steps aside."""
    db = _db(tmp_path)
    points = [
        {"source_point_id": "7001", "map_id": "930", "map_name": "A", "region": "「甲区」一处", "label": "梦境迷钟"},
        {"source_point_id": "7002", "map_id": "930", "map_name": "A", "region": "「甲区」一处", "label": "梦境迷钟"},
        {"source_point_id": "7003", "map_id": "931", "map_name": "B", "region": "「乙区」二处", "label": "梦境迷钟"},
    ]
    article = "\n".join([
        "「甲区」一处共有2个梦境迷钟，下面分别说明",
        "「乙区」二处共有1个梦境迷钟，位置在入口右侧",
        "第1个：把模块推到左边，钟表小子会从位置1走到位置2",
        "第2个：转动镜子两次，让道路拼接成一条直角",
        "第3个：开启机关1，再回收机关2即可完成",
    ])
    page = _page(db, article, url="https://t.test/areas")
    lines = [line for line in article.split("\n") if line]
    # the whole page first (it names both regions), then the area slice
    _item(db, page, lines, map_name="全部3个梦境迷钟解密", topic="dream_ticker")
    _item(db, page, [lines[2], "第1个：把模块推到左边", "第2个：转动镜子两次"], map_name="「甲区」一处", topic="dream_ticker")
    report = approve_grounded(db, topic="dream_ticker", official_points=points)
    keys = sorted(a["target_key"] for a in report["approved"])
    assert keys == ["set:7001-7002-7003:topic:dream_ticker"]
    assert any(r["reason"] == "OVERLAPS_RICHER_SET" for r in report["rejected"])
    db.close()


#: Official data keeps 朝露公馆's rooms as their own regions; the page says "朝露公馆".
ROOM_POINTS = (
    [{"source_point_id": "30%02d" % i, "map_id": "940", "map_name": "朝露公馆", "region": "朝露公馆", "label": "折纸小鸟"} for i in range(1, 8)]
    + [{"source_point_id": "31%02d" % i, "map_id": "94%d" % i, "map_name": "朝露公馆-%d" % i, "region": "朝露公馆-%d" % i, "label": "折纸小鸟"} for i in range(1, 4)]
)

ROOM_ARTICLE = "\n".join([
    "崩坏星穹铁道朝露公馆折纸小鸟全收集攻略",
    "（1）共10只，有的折纸小鸟需要交互多次，看到对话结束就行了",
    "第1只：进入朝露公馆后往左走，在沙盘旁边就能看到",
    "第2只：坐电梯到二层，沿着走廊走到尽头",
    "第3只：在公馆的书架后面，需要先搬开箱子",
])


def test_the_rooms_of_a_named_area_come_along_when_the_numbers_add_up(tmp_path):
    db = _db(tmp_path)
    page = _page(db, ROOM_ARTICLE, url="https://t.test/rooms")
    steps = [line for line in ROOM_ARTICLE.split("\n") if line]
    _item(db, page, steps, map_name="朝露公馆折纸小鸟全收集", topic="origami_bird")

    report = approve_grounded(db, topic="origami_bird", official_points=ROOM_POINTS)
    assert report["targets"] == 1
    target = report["approved"][0]["target_key"]
    # 7 in the hall plus its three rooms: the page said ten and ten is what it gets
    assert target == "set:" + "-".join(p["source_point_id"] for p in ROOM_POINTS) + ":topic:origami_bird"
    db.close()


def test_rooms_do_not_come_along_when_the_area_count_disagrees(tmp_path):
    db = _db(tmp_path)
    article = ROOM_ARTICLE.replace("共10只", "共8只").replace("（1）共8只", "（1）共8只")
    page = _page(db, article, url="https://t.test/rooms2")
    _item(db, page, [line for line in article.split("\n") if line], map_name="朝露公馆折纸小鸟", topic="origami_bird")
    report = approve_grounded(db, topic="origami_bird", official_points=ROOM_POINTS)
    assert report["targets"] == 0
    db.close()


def test_the_item_heading_decides_the_scope_not_a_passing_mention(tmp_path):
    """A body that says "从乙区传送到甲区" walks 甲区, not both."""
    db = _db(tmp_path)
    points = [
        {"source_point_id": "7101", "map_id": "950", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "7102", "map_id": "950", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "7103", "map_id": "950", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "7104", "map_id": "951", "map_name": "乙区", "region": "「乙区」二处", "label": "浮脂溯源"},
    ]
    article = "\n".join([
        "（1）「甲区」一处地图共有3个浮脂溯源解密，下面逐个说明",
        "（1）从「乙区」二处传送到甲区，先往左边走，把模块推到凹槽里",
        "（2）转动中间的镜子两次，让光束照到对面的浮脂上",
        "（3）把左侧的方块移到最下方，再踩一次机关即可",
    ])
    page = _page(db, article, url="https://t.test/heading")
    lines = [line for line in article.split("\n") if line]
    _item(db, page, lines, map_name="「甲区」一处", topic="floating_grease")
    report = approve_grounded(db, topic="floating_grease", official_points=points)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"] == "set:7101-7102-7103:topic:floating_grease"
    db.close()


#: The topic's official label carries the event decoration the articles never write.
GREASE_POINTS = [
    {"source_point_id": "5369", "map_id": "910", "map_name": "寂灭空飨妖都", "region": "寂灭空飨妖都", "label": "浮脂溯源·二次元ROTATE！"},
    {"source_point_id": "5384", "map_id": "910", "map_name": "寂灭空飨妖都", "region": "寂灭空飨妖都", "label": "浮脂溯源·二次元ROTATE！"},
    {"source_point_id": "5391", "map_id": "910", "map_name": "寂灭空飨妖都", "region": "寂灭空飨妖都", "label": "浮脂溯源·二次元ROTATE！"},
    {"source_point_id": "5407", "map_id": "910", "map_name": "寂灭空飨妖都", "region": "寂灭空飨妖都", "label": "浮脂溯源·二次元ROTATE！"},
]

GREASE_ARTICLE = "\n".join([
    "崩坏星穹铁道寂灭空飨妖都地图共有4个浮脂溯源解密，下面逐个说明",
    "第1个：先把黄色模块推到右上角的凹槽里，浮脂就会顺着轨道滑下去",
    "第2个：转动中间的镜子两次，让光束照到对面的浮脂上",
    "第3个：把左侧的方块移到最下方，再踩一次机关即可",
    "第4个：先开启机关1，再回收机关2，最后把地板移到机关3位置",
])


def test_declared_count_reads_the_copula_and_the_units_pages_use():
    # "共有3个" is how the pages write it; the bare "共3个" is the rare form
    assert declared_count("海原市地图共有3个浮脂溯源解密") == 3
    assert declared_count("（1）共10只，有的若虫需要交互多次") == 10
    assert declared_count("本区域总共有十二个宝箱") == 12
    assert declared_count("两个地图共 4 处解密") == 4
    # the number still has to be the only one the line states
    assert declared_count("浮脂溯源共有3个解密，战利品共12个") == 0


def test_label_variants_drop_the_official_decoration():
    assert label_variants("浮脂溯源·二次元ROTATE！") == ["浮脂溯源·二次元ROTATE！", "浮脂溯源"]
    assert label_variants("若虫") == ["若虫"]
    assert declared_count("海原市地图共有3个浮脂溯源解密", labels=label_variants("浮脂溯源·二次元ROTATE！")) == 3
    # the decorated label alone matches nothing, which is why the head is needed
    assert declared_count("海原市地图共有3个浮脂溯源解密", label="浮脂溯源·二次元ROTATE！") == 0


def test_region_scope_count_adds_up_what_the_page_states():
    regions = {"海原市": ["1", "2", "3"], "海原电视塔": ["4", "5", "6"]}
    both = "（1）海原市地图共有3个浮脂溯源解密 （2）海原电视塔地图共有3个浮脂溯源解密"
    assert region_scope_count(both, regions, ["浮脂溯源"]) == (6, "sum")
    # a page stating one region's number falls three short of the six points the
    # two regions hold, and the caller refuses the shortfall
    assert region_scope_count("（1）海原市地图共有3个浮脂溯源解密", regions, ["浮脂溯源"]) == (3, "sum")
    assert region_scope_count("两个区域都有浮脂溯源解密", regions, ["浮脂溯源"]) == (0, "")
    single = {"「半神议院」黎明云崖": ["7", "8"]}
    assert region_scope_count("（1）共2只，有的若虫需要交互多次", single, ["若虫"]) == (2, "topic")



#: Official data splits one area in two ("「白日梦」酒店-梦境" + its "-5" sub-room)
#: while the page counts them as one; the numbers still have to add up.
SPLIT_POINTS = [
    {"source_point_id": "4558", "map_id": "920", "map_name": "「白日梦」酒店-梦境", "region": "「白日梦」酒店-梦境", "label": "梦境迷钟"},
    {"source_point_id": "4559", "map_id": "920", "map_name": "「白日梦」酒店-梦境", "region": "「白日梦」酒店-梦境", "label": "梦境迷钟"},
    {"source_point_id": "4560", "map_id": "920", "map_name": "「白日梦」酒店-梦境", "region": "「白日梦」酒店-梦境", "label": "梦境迷钟"},
    {"source_point_id": "4561", "map_id": "920", "map_name": "「白日梦」酒店-梦境", "region": "「白日梦」酒店-梦境", "label": "梦境迷钟"},
    {"source_point_id": "4562", "map_id": "921", "map_name": "白日梦酒店梦境-5", "region": "白日梦酒店梦境-5", "label": "梦境迷钟"},
]

SPLIT_ARTICLE = "\n".join([
    "「白日梦」酒店-梦境区域共有5个梦境迷钟，下面逐个说明位置和解法",
    "第1个：把黄色模块移到左边，钟表小子会从位置1走到位置2",
    "第2个：转动镜子两次，让道路拼接成一条直角",
    "第3个：先把蓝色模块旋转一次，再把它推到右上角",
    "第4个：开启机关1并回收机关2，地板就会移到机关3",
    "第5个在白日梦酒店梦境-5的房间里，进门右手边就是",
])


def test_a_region_the_official_data_splits_still_binds_when_the_numbers_add_up(tmp_path):
    db = _db(tmp_path)
    page = _page(db, SPLIT_ARTICLE, url="https://t.test/split")
    steps = [line for line in SPLIT_ARTICLE.split("\n") if line]
    _item(db, page, steps, map_name="【崩坏：星穹铁道】全部5个梦境迷钟解密", topic="dream_ticker")

    report = approve_grounded(db, topic="dream_ticker", official_points=SPLIT_POINTS)
    assert report["targets"] == 1
    assert report["approved"][0]["binding"] == "REGION_SET"
    assert report["approved"][0]["target_key"] == "set:4558-4559-4560-4561-4562:topic:dream_ticker"
    db.close()


def test_stated_numbers_that_overshoot_the_regions_are_refused(tmp_path):
    """The page claims 5 for the parent and 4 more for the sub-room: 9 is not 5."""
    db = _db(tmp_path)
    article = "\n".join([
        "「白日梦」酒店-梦境区域共有5个梦境迷钟，下面逐个说明位置和解法",
        "白日梦酒店梦境-5共有4个梦境迷钟需要修复，进入房间后往右手边走",
        "第1个：把黄色模块推到左边，钟表小子会从位置1走到位置2",
        "第2个：转动镜子两次，让道路拼接成一条直角",
    ])
    page = _page(db, article, url="https://t.test/split2")
    _item(db, page, [line for line in article.split("\n") if line], map_name="梦境迷钟收集", topic="dream_ticker")
    report = approve_grounded(db, topic="dream_ticker", official_points=SPLIT_POINTS)
    assert report["targets"] == 0 and report["rejected_count"] == 0
    db.close()

def test_a_decorated_label_still_binds_its_region_set(tmp_path):
    """The page says "共有4个浮脂溯源"; the official label adds an event suffix."""
    db = _db(tmp_path)
    page = _page(db, GREASE_ARTICLE, url="https://t.test/grease")
    steps = [line for line in GREASE_ARTICLE.split("\n") if line]
    _item(db, page, steps, map_name="崩坏星穹铁道寂灭空飨妖都浮脂溯源解密攻略", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=GREASE_POINTS)
    assert report["targets"] == 1
    assert report["approved"][0]["binding"] == "REGION_SET"
    assert report["approved"][0]["target_key"] == "set:5369-5384-5391-5407:topic:floating_grease"
    db.close()


def test_a_region_set_without_a_body_stays_unapproved(tmp_path):
    """A page whose only line is its scope note states a count, not a walkthrough."""
    db = _db(tmp_path)
    page = _page(db, GREASE_ARTICLE, url="https://t.test/grease2")
    _item(db, page, ["崩坏星穹铁道寂灭空飨妖都地图共有4个浮脂溯源解密"], map_name="寂灭空飨妖都", topic="floating_grease")
    report = approve_grounded(db, topic="floating_grease", official_points=GREASE_POINTS)
    assert report["targets"] == 0
    assert report["rejected"][0]["reason"] == "REGION_SET_TOO_THIN"
    db.close()

def test_a_set_whose_points_are_all_published_adds_nothing(tmp_path):
    """The same points already have a guide: a second one is a card, not coverage."""
    db = _db(tmp_path)
    page = _page(db, ARTICLE)
    steps = [line for line in ARTICLE.split("\n") if line]
    _item(db, page, steps)
    # both points already have their own published guides, but no set target does
    for point_id in ("4139", "4142"):
        db.create_entry({
            "source_point_id": point_id,
            "title": f"已发布的点位 {point_id}",
            "status": "published",
            "source_url": page["canonical_url"],
            "steps": [{"text": text, "images": []} for text in steps],
        })
    report = approve_grounded(db, topic="golden_scapegoat", official_points=POINTS)
    assert report["targets"] == 0
    assert any(r["reason"] == "NO_NEW_COVERAGE" for r in report["rejected"])
    db.close()

def test_a_count_written_after_its_own_region_works_in_one_sentence(tmp_path):
    """『甲区』共3处，『乙区』共4处 — two numbers in one line, each tied to a name."""
    db = _db(tmp_path)
    points = [
        {"source_point_id": "9101", "map_id": "970", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "9102", "map_id": "970", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "9103", "map_id": "970", "map_name": "甲区", "region": "「甲区」一处", "label": "浮脂溯源"},
        {"source_point_id": "9104", "map_id": "971", "map_name": "乙区", "region": "「乙区」二处", "label": "浮脂溯源"},
        {"source_point_id": "9105", "map_id": "971", "map_name": "乙区", "region": "「乙区」二处", "label": "浮脂溯源"},
        {"source_point_id": "9106", "map_id": "971", "map_name": "乙区", "region": "「乙区」二处", "label": "浮脂溯源"},
        {"source_point_id": "9107", "map_id": "971", "map_name": "乙区", "region": "「乙区」二处", "label": "浮脂溯源"},
    ]
    article = "\n".join([
        "『甲区』一处共3处，『乙区』二处共4处，具体位置标序看图2，查缺补漏直接对号查找即可。",
        "甲区一处和乙区二处的解密都在下面的图里，按顺序做即可",
        "第一处把黄色模块推到左上角，光线会连到第二个模块上",
        "第二处转动中间的镜子两次，让道路拼接成一条直线",
    ])
    page = _page(db, article, url="https://t.test/anchored")
    lines = [line for line in article.split("\n") if line]
    _item(db, page, lines[1:], map_name="「甲区」一处和「乙区」二处", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=points)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"] == (
        "set:9101-9102-9103-9104-9105-9106-9107:topic:floating_grease"
    )
    db.close()


def test_a_gallery_carries_a_region_set_when_the_text_is_thin(tmp_path):
    """Four points, one scope line, four labelled screenshots: the pictures are the body."""
    db = _db(tmp_path)
    points = [
        {"source_point_id": "920%d" % index, "map_id": "980", "map_name": "丙区", "region": "「丙区」三处", "label": "浮脂溯源"}
        for index in range(1, 5)
    ]
    article = "「丙区」三处共有4个浮脂溯源解密，具体位置标序看图"
    page = _page(db, article, url="https://t.test/gallery")
    _item(db, page, ["「丙区」三处共有4个浮脂溯源解密"], map_name="「丙区」三处", topic="floating_grease")

    # the page html sits three levels down so the derived folder is next to it
    deep = tmp_path / "raw" / "a" / "b"
    deep.mkdir(parents=True)
    html_path = deep / "page.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    db.conn.execute(
        "UPDATE guide_page SET raw_html_path = ? WHERE id = ?", (str(html_path), page["id"])
    )
    derived = tmp_path / "derived" / str(page["id"])
    derived.mkdir(parents=True)
    observations = []
    for index in range(1, 5):
        sha = "sha-%d" % index
        db.conn.execute(
            "INSERT INTO guide_asset_cache(source_url, sha256, width, height, status, downloaded_at)"
            " VALUES (?, ?, 560, 747, 'ok', '2026-10-04T00:00:00Z')",
            ("https://t.test/%d.png" % index, sha),
        )
        observations.append({"sha256": sha, "role": "puzzle_step", "block_id": "b%d" % index})
    # an advertisement and a tiny icon must not count
    db.conn.execute(
        "INSERT INTO guide_asset_cache(source_url, sha256, width, height, status, downloaded_at)"
        " VALUES ('https://t.test/ad.png', 'sha-ad', 560, 747, 'ok', '2026-10-04T00:00:00Z')"
    )
    observations.append({"sha256": "sha-ad", "role": "advertisement", "block_id": "b9"})
    (derived / "image-roles.json").write_text(
        json.dumps({"observations": observations}, ensure_ascii=False), encoding="utf-8"
    )
    db.conn.commit()

    report = approve_grounded(db, topic="floating_grease", official_points=points)
    assert report["targets"] == 1
    entry = report["approved"][0]
    assert entry["target_key"] == "set:9201-9202-9203-9204:topic:floating_grease"
    assert entry["image_source"] == "gallery"
    assert entry["images"] == 4  # the advertisement is not part of the body
    db.close()


def test_a_gallery_that_cannot_cover_every_point_is_refused(tmp_path):
    db = _db(tmp_path)
    points = [
        {"source_point_id": "930%d" % index, "map_id": "981", "map_name": "丁区", "region": "「丁区」四处", "label": "浮脂溯源"}
        for index in range(1, 5)
    ]
    article = "「丁区」四处共有4个浮脂溯源解密，具体位置标序看图"
    page = _page(db, article, url="https://t.test/gallery2")
    _item(db, page, ["「丁区」四处共有4个浮脂溯源解密"], map_name="「丁区」四处", topic="floating_grease")
    deep = tmp_path / "raw" / "a" / "b"
    deep.mkdir(parents=True)
    html_path = deep / "page.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_html_path = ? WHERE id = ?", (str(html_path), page["id"]))
    derived = tmp_path / "derived" / str(page["id"])
    derived.mkdir(parents=True)
    observations = []
    for index in range(1, 3):  # only two pictures for four points
        sha = "sha-%d" % index
        db.conn.execute(
            "INSERT INTO guide_asset_cache(source_url, sha256, width, height, status, downloaded_at)"
            " VALUES (?, ?, 560, 747, 'ok', '2026-10-04T00:00:00Z')",
            ("https://t.test/g%d.png" % index, sha),
        )
        observations.append({"sha256": sha, "role": "puzzle_step", "block_id": "b%d" % index})
    (derived / "image-roles.json").write_text(
        json.dumps({"observations": observations}, ensure_ascii=False), encoding="utf-8"
    )
    db.conn.commit()

    report = approve_grounded(db, topic="floating_grease", official_points=points)
    assert report["targets"] == 0
    assert report["rejected"][0]["reason"] == "REGION_SET_TOO_THIN"
    db.close()

def test_unclassified_images_fall_back_to_the_rule_based_judge(tmp_path):
    """Most pages were never classified: big article pictures still count, ads do not."""
    db = _db(tmp_path)
    points = [
        {"source_point_id": "940%d" % index, "map_id": "990", "map_name": "戊区", "region": "「戊区」五处", "label": "浮脂溯源"}
        for index in range(1, 5)
    ]
    article = "「戊区」五处共有4个浮脂溯源解密，具体位置标序看图"
    page = _page(db, article, url="https://t.test/unknown")
    _item(db, page, ["「戊区」五处共有4个浮脂溯源解密"], map_name="「戊区」五处", topic="floating_grease")
    deep = tmp_path / "raw" / "a" / "b"
    deep.mkdir(parents=True)
    html_path = deep / "page.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_html_path = ? WHERE id = ?", (str(html_path), page["id"]))
    derived = tmp_path / "derived" / str(page["id"])
    derived.mkdir(parents=True)

    def cache(url, sha, width=560, height=747):
        db.conn.execute(
            "INSERT INTO guide_asset_cache(source_url, sha256, width, height, status, downloaded_at)"
            " VALUES (?, ?, ?, ?, 'ok', '2026-10-04T00:00:00Z')",
            (url, sha, width, height),
        )

    observations = []
    for index in range(1, 5):
        sha = "raw-%d" % index
        cache("https://t.test/step-%d.png" % index, sha)
        observations.append({"sha256": sha, "role": "unknown", "block_id": "b%d" % index})
    # a banner and a tiny icon: the rules must drop both
    cache("https://t.test/banner.png", "raw-banner")
    observations.append({"sha256": "raw-banner", "role": "unknown", "block_id": "b8"})
    cache("https://t.test/small.png", "raw-small", width=120, height=90)
    observations.append({"sha256": "raw-small", "role": "unknown", "block_id": "b9"})
    (derived / "image-roles.json").write_text(
        json.dumps({"observations": observations}, ensure_ascii=False), encoding="utf-8"
    )
    db.conn.commit()

    report = approve_grounded(db, topic="floating_grease", official_points=points)
    assert report["targets"] == 1
    entry = report["approved"][0]
    assert entry["image_source"] == "gallery"
    assert entry["images"] == 4  # the banner and the icon are not part of the body
    db.close()


def test_a_number_stated_once_for_every_region_counts_them_all():
    """"每个区域各有3个" states one number for two areas: three in each of them."""
    regions = {
        "「呓语密林」神悟树庭": ["3458", "3580", "3582"],
        "「神谕圣地」雅努萨波利斯": ["3391", "3400", "3569"],
    }
    sentence = "黄金替罪羊谜题分布在呓语密林-神悟树庭和神谕圣地-雅努萨波利斯两大区域，每个区域各有3个谜题等待玩家解锁。"
    assert distributive_count(sentence) == 3
    assert region_scope_count(sentence, regions, ["黄金替罪羊"]) == (6, "each")
    # a page that says a different number for each region is not saying one count
    # for every region, and the scope stays unknown
    assert distributive_count("每个区域各有3个谜题，每个区域各有4个谜题") == 0
    assert distributive_count("两个区域都有浮脂溯源解密") == 0
    assert region_scope_count("两个区域都有浮脂溯源解密", regions, ["浮脂溯源"]) == (0, "")


#: One page covering two areas, one number for both ("各有3个") — the wording the
#: 3.1 scapegoat guide uses. The official points are one per floor of either area.
EACH_POINTS = [
    {"source_point_id": "3458", "map_id": "383", "map_name": "1层", "region": "「呓语密林」神悟树庭", "label": "黄金替罪羊"},
    {"source_point_id": "3580", "map_id": "382", "map_name": "-1层", "region": "「呓语密林」神悟树庭", "label": "黄金替罪羊"},
    {"source_point_id": "3582", "map_id": "385", "map_name": "3层", "region": "「呓语密林」神悟树庭", "label": "黄金替罪羊"},
    {"source_point_id": "3391", "map_id": "380", "map_name": "-2层", "region": "「神谕圣地」雅努萨波利斯", "label": "黄金替罪羊"},
    {"source_point_id": "3400", "map_id": "380", "map_name": "-2层", "region": "「神谕圣地」雅努萨波利斯", "label": "黄金替罪羊"},
    {"source_point_id": "3569", "map_id": "378", "map_name": "1层", "region": "「神谕圣地」雅努萨波利斯", "label": "黄金替罪羊"},
]

EACH_ARTICLE = "\n".join([
    "黄金替罪羊谜题分布在呓语密林-神悟树庭和神谕圣地-雅努萨波利斯两大区域，每个区域各有3个谜题等待玩家解锁。",
    "一、呓语密林-神悟树庭",
    "黄金替罪羊①：按照左左右右右的顺序操作机关即可点亮祭坛",
    "黄金替罪羊②：依次点击右下左左上右的顺序解开谜题",
    "黄金替罪羊③：按照右右左右的顺序解开谜题即可通过",
    "二、神谕圣地-雅努萨波利斯",
    "黄金替罪羊①：先解开预言算碑再按左左右右右的顺序操作",
    "黄金替罪羊②：依次点击右右上右左上的顺序解开谜题",
    "黄金替罪羊③：按照右右下左左右的顺序解开谜题即可",
])


def test_one_number_for_two_areas_binds_both_areas(tmp_path):
    db = _db(tmp_path)
    page = _page(db, EACH_ARTICLE, url="https://t.test/each")
    steps = [line for line in EACH_ARTICLE.split("\n") if line]
    _item(db, page, steps, map_name="崩坏星穹铁道3.1黄金替罪羊解谜攻略")

    report = approve_grounded(db, topic="golden_scapegoat", official_points=EACH_POINTS)
    assert report["targets"] == 1
    entry = report["approved"][0]
    assert entry["binding"] == "REGION_SET"
    assert entry["target_key"] == "set:3391-3400-3458-3569-3580-3582:topic:golden_scapegoat"
    db.close()


#: Two variants of one area, each holding ten points: a page that counts ten in
#: the one it names must not be read as evidence for the other one.
VARIANT_POINTS = [
    {"source_point_id": "44%02d" % index, "map_id": "480", "map_name": "1层", "region": "「灾梦余温」无名泰坦大墓", "label": "若虫"}
    for index in range(10)
] + [
    {"source_point_id": "43%02d" % index, "map_id": "470", "map_name": "1层", "region": "「全世矩阵」无名泰坦大墓", "label": "若虫"}
    for index in range(10)
]


def test_a_page_about_the_twin_variant_does_not_bind_this_one(tmp_path):
    db = _db(tmp_path)
    article = "\n".join([
        "崩坏星穹铁道全世矩阵无名泰坦大墓全若虫收集攻略",
        "（1）共10只，有的若虫需要交互多次，看到对话结束就行了",
        "第1只若虫在传送锚点右边的柱子后面，靠近墙壁",
        "第2只若虫从楼梯上去左手边的房间里，进门就能看到",
        "第3只若虫在平台边缘的木箱上，需要绕着走过去",
    ])
    page = _page(db, article, url="https://t.test/variant")
    # the item's heading was resolved (wrongly) to the twin variant
    _item(db, page, [line for line in article.split("\n") if line],
          map_name="翁法罗斯 / 「灾梦余温」无名泰坦大墓 / 1层", topic="nymph")

    report = approve_grounded(db, topic="nymph", official_points=VARIANT_POINTS)
    assert report["targets"] == 0 and report["rejected_count"] == 0
    db.close()


def test_a_page_that_names_its_own_variant_still_binds(tmp_path):
    db = _db(tmp_path)
    article = "\n".join([
        "崩坏星穹铁道灾梦余温无名泰坦大墓全若虫收集攻略",
        "（1）共10只，有的若虫需要交互多次，看到对话结束就行了",
        "第1只若虫在传送锚点右边的柱子后面，靠近墙壁",
        "第2只若虫从楼梯上去左手边的房间里，进门就能看到",
        "第3只若虫在平台边缘的木箱上，需要绕着走过去",
    ])
    page = _page(db, article, url="https://t.test/variant2")
    _item(db, page, [line for line in article.split("\n") if line],
          map_name="翁帕罗斯 / 「灾梦余温」无名泰坦大墓 / 1层".replace("帕", "法"), topic="nymph")

    report = approve_grounded(db, topic="nymph", official_points=VARIANT_POINTS)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"].startswith("set:44")
    db.close()


#: An area whose parts the page counts, one of them a room official data cannot name.
RESIDUAL_POINTS = [
    {"source_point_id": "7001", "map_id": "900", "map_name": "1层", "region": "甲城中心城区", "label": "浮脂溯源", "map_path": "甲城 / 甲城中心城区 / 1层"},
    {"source_point_id": "7002", "map_id": "900", "map_name": "1层", "region": "甲城中心城区", "label": "浮脂溯源", "map_path": "甲城 / 甲城中心城区 / 1层"},
    {"source_point_id": "7003", "map_id": "901", "map_name": "3层", "region": "指针塔", "label": "浮脂溯源", "map_path": "甲城 / 指针塔 / 3层"},
    {"source_point_id": "7004", "map_id": "902", "map_name": "", "region": "特殊房间", "label": "浮脂溯源", "map_path": "甲城 / 特殊房间 / 902"},
    {"source_point_id": "7005", "map_id": "903", "map_name": "", "region": "生研院", "label": "浮脂溯源", "map_path": "甲城 / 生研院 / 903"},
]

RESIDUAL_LINES = [
    "【星穹铁道4.5】新增4个浮脂溯源，二次元ROTATE，甲城中心城区2个，指针塔1个，乙声院1个，浮脂溯源解谜攻略",
    "甲城中心城区的第一处解密在传送锚点右边的房间，按顺序转动两次即可",
    "指针塔的解密需要先坐电梯到三层，再按右右左右的顺序操作机关",
    "乙声院的那一处比较隐蔽，从主路尽头的小门进去就能看到机关",
]


def test_partition_counts_reads_an_areas_own_breakdown():
    line = "【星穹铁道4.5】新增8个浮脂溯源，二次元ROTATE，千星城中心城区4个，指针塔3个，空声院1个，浮脂溯源解谜攻略"
    assert partition_counts(line) == [("千星城中心城区", 4), ("指针塔", 3), ("空声院", 1)]
    # a title with the number in brackets is a scope note, not a partition entry
    assert partition_counts("【浮脂溯源·二次元ROTATE】指针塔（共三个）") == []


def test_a_partition_binds_the_room_official_data_leaves_unnamed(tmp_path):
    db = _db(tmp_path)
    page = _page(db, "\n".join(RESIDUAL_LINES), url="https://t.test/residual")
    _item(db, page, RESIDUAL_LINES, map_name="甲城浮脂溯源全收集", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=RESIDUAL_POINTS)
    assert report["targets"] == 1
    entry = report["approved"][0]
    assert entry["binding"] == "REGION_SET"
    # only the unnamed room: the 生研院 point has a name, so it is not a leftover
    assert entry["target_key"] == "set:7004:topic:floating_grease"
    db.close()


def test_two_unnamed_parts_are_not_attributed(tmp_path):
    """Two parts the official regions cannot match: nothing says which is which."""
    db = _db(tmp_path)
    lines = [
        "【星穹铁道4.5】新增4个浮脂溯源，二次元ROTATE，甲城中心城区2个，指针塔1个，乙声院1个，丙声院1个",
        "甲城中心城区的第一处解密在传送锚点右边的房间，按顺序转动两次即可",
        "指针塔的解密需要先坐电梯到三层，再按右右左右的顺序操作机关",
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/residual2")
    _item(db, page, lines, map_name="甲城浮脂溯源全收集", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=RESIDUAL_POINTS)
    assert report["targets"] == 0
    db.close()


def test_a_shortened_official_name_is_not_an_unnamed_room(tmp_path):
    """The page named 中心城区 loosely; official data calls it 甲城中心城区."""
    db = _db(tmp_path)
    lines = [
        "【星穹铁道4.5】新增3个浮脂溯源，中心城区2个，指针塔1个，浮脂溯源解谜攻略",
        "中心城区的第一处解密在传送锚点右边的房间，按顺序转动两次即可",
        "指针塔的解密需要先坐电梯到三层，再按右右左右的顺序操作机关",
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/residual3")
    _item(db, page, lines, map_name="甲城浮脂溯源全收集", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=RESIDUAL_POINTS)
    #: 缩写的「中心城区」不算甲城中心城区（那 2 个点位一个都不绑）；
    #: 但同一页明说「指针塔1个」并给了它的解法，那一个是真绑定。
    targets = sorted(item["target_key"] for item in report["approved"])
    assert targets == ["set:7003:topic:floating_grease"]
    assert not any("7001" in key or "7002" in key for key in targets)
    db.close()


def test_a_partition_whose_numbers_do_not_close_binds_nothing(tmp_path):
    db = _db(tmp_path)
    lines = [
        "【星穹铁道4.5】新增4个浮脂溯源，甲城中心城区3个，指针塔1个，乙声院1个，浮脂溯源解谜攻略",
        "甲城中心城区有三处解密，第一处在传送锚点右边的房间，按顺序转动两次即可",
        "指针塔的解密需要先坐电梯到三层，再按右右左右的顺序操作机关",
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/residual4")
    _item(db, page, lines, map_name="甲城浮脂溯源全收集", topic="floating_grease")

    report = approve_grounded(db, topic="floating_grease", official_points=RESIDUAL_POINTS)
    assert report["targets"] == 0
    db.close()


#: Official data splits one area into a region and a map name ("渡画泉隐" lives
#: under the region 「无名客「阿哈」的债务清单」); pages name the map.
MAP_NAMED_POINTS = [
    {"source_point_id": "55%02d" % index, "map_id": "518", "map_name": "渡画泉隐", "region": "「无名客「阿哈」的债务清单」", "label": "二次元JUMP!", "map_path": "二相乐园 / 「无名客「阿哈」的债务清单」 / 渡画泉隐"}
    for index in range(9)
] + [
    {"source_point_id": "56%02d" % index, "map_id": "519", "map_name": "1层", "region": "「甲区」二处", "label": "二次元JUMP!", "map_path": "二相乐园 / 「甲区」二处 / 1层"}
    for index in range(3)
]


def test_a_page_may_name_the_official_map_instead_of_the_region():
    found = region_candidates(MAP_NAMED_POINTS, "可以在地图上数一下渡画泉隐jump点位是否为9个")
    assert list(found) == ["渡画泉隐"]
    assert found["渡画泉隐"] == ["550%d" % index for index in range(9)]
    # a floor name is shared by every map and must never stand for an area
    assert region_candidates(MAP_NAMED_POINTS, "1层的宝箱一共有3个") == {}


def test_a_count_stated_as_a_fact_is_read_but_a_bare_have_is_not():
    assert anchored_count("可以在地图上数一下渡画泉隐jump点位是否为9个", "渡画泉隐") == 9
    assert anchored_count("标准状态下，该地图应该拥有9个跳转点", "渡画泉隐") == 0
    # "「甲区」有3个宝箱" counts chests, not the topic's items
    assert anchored_count("「甲区」二处有3个宝箱", "「甲区」二处") == 0


def test_a_map_name_with_a_stated_count_binds_its_points(tmp_path):
    db = _db(tmp_path)
    lines = [
        "一、二次元jump",
        "可以在地图上数一下渡画泉隐jump点位是否为9个。",
        "本次渡画泉隐有一个易漏jump点位，可以检查一下地图上是否开启。",
        "随便进入一个jump，进去之后点击左上角然后开始演奏奇异乐章。",
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/mapname")
    _item(db, page, lines, map_name="一、二次元jump", topic="jump")

    report = approve_grounded(db, topic="jump", official_points=MAP_NAMED_POINTS)
    assert report["targets"] == 1
    entry = report["approved"][0]
    assert entry["binding"] == "REGION_SET"
    assert entry["target_key"] == "set:" + "-".join("55%02d" % index for index in range(9)) + ":topic:jump"
    db.close()


def test_enumeration_count_reads_a_walkthroughs_own_numbering():
    two = "第1个【梦境迷钟】修复解密 第2个【梦境迷钟】修复解密"
    assert enumeration_count(two, ["梦境迷钟"]) == 2
    assert enumeration_count("第一个黄金替罪羊位置 第二个黄金替罪羊位置 第三个黄金替罪羊位置", ["黄金替罪羊"]) == 3
    assert enumeration_count("黄金替罪羊① 黄金替罪羊②", ["黄金替罪羊"]) == 2
    # a gap or a duplicate is not a count of the items
    assert enumeration_count("第2个【梦境迷钟】 第3个【梦境迷钟】", ["梦境迷钟"]) == 0
    # the markers have to sit next to the topic's own name
    assert enumeration_count("①普通战利品 ②王下一桶 ③梦境米迷钟", ["梦境迷钟"]) == 0
    assert enumeration_count("第1个 第2个", ["梦境迷钟"]) == 0


def test_a_walkthrough_that_numbers_its_items_binds_the_region(tmp_path):
    db = _db(tmp_path)
    lines = [
        "一、第一个黄金替罪羊位置及路线",
        "此处挑战位于【晨昏之眼】地图右侧负二层，如图所示。",
        "影子出现前路线：左、左、左、上、右、右、右、右",
        "二、第二个黄金替罪羊位置及路线",
        "影子出现前路线：左、左、右、右、右、右、右",
        "影子出现后应对技巧：反复移动引导影子触发机关后，迅速直奔目标祭坛。",
        "三、第三个黄金替罪羊位置及路线",
        "此处位于地图中央的平台上方，需要先点亮两侧的机关再进入。",
    ]
    points = [
        {"source_point_id": "38%02d" % index, "map_id": "435", "map_name": "2层", "region": "「穹顶关塞」晨昏之眼", "label": "黄金替罪羊", "map_path": "翁法罗斯 / 「穹顶关塞」晨昏之眼 / 2层"}
        for index in (11, 55, 68)
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/enum")
    _item(db, page, lines, map_name="穹顶关塞", topic="golden_scapegoat")

    report = approve_grounded(db, topic="golden_scapegoat", official_points=points)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"] == "set:3811-3855-3868:topic:golden_scapegoat"
    db.close()


def test_a_number_written_at_the_name_beats_the_sentences_total():
    line = "这个区域中一共有29个宝箱，2个贼灵和3个黄金替罪羊解谜。"
    assert near_count(line, "黄金替罪羊") == 3
    assert declared_count(line, label="黄金替罪羊") == 3
    # the previous entry's number is not this entry's count
    assert near_count("甲城中心城区2个，指针塔1个", "指针塔") == 0
    # the ordinal form is a numbering, not a count
    assert near_count("一、第一个黄金替罪羊位置及路线", "黄金替罪羊") == 0


def test_a_list_sentence_binds_the_region_it_counts(tmp_path):
    db = _db(tmp_path)
    lines = [
        "崩坏星穹铁道浴血战端悬锋城全宝箱在哪里介绍",
        "这个区域中一共有29个宝箱，2个贼灵和3个黄金替罪羊解谜。",
        "一、宝箱及贼灵解谜位置图",
        "小伙伴们顺着图中的顺序探索就可以一次性完成探索，这一区域我们也要频繁进行黎明模式和永夜模式的切换",
        "二、黄金替罪羊解谜",
        "右右下右右右上左左左左左左",
        "左右右下右右左上左左左",
    ]
    points = [
        {"source_point_id": "29%02d" % index, "map_id": "342", "map_name": "2层", "region": "「浴血战端」悬锋城", "label": "黄金替罪羊", "map_path": "翁法罗斯 / 「浴血战端」悬锋城 / 2层"}
        for index in (92, 93)
    ] + [
        {"source_point_id": "3037", "map_id": "340", "map_name": "-1层", "region": "「浴血战端」悬锋城", "label": "黄金替罪羊", "map_path": "翁法罗斯 / 「浴血战端」悬锋城 / -1层"}
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/list")
    _item(db, page, lines, map_name="《崩坏星穹铁道》浴血战端悬锋城全宝箱位置一览", topic="golden_scapegoat")

    report = approve_grounded(db, topic="golden_scapegoat", official_points=points)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"] == "set:2992-2993-3037:topic:golden_scapegoat"
    db.close()


def test_location_lines_count_the_items_a_page_places_in_the_area():
    steps = [
        "1、位置：半神议院黎明云崖地图的左下方。",
        "2、到达后点击【调查】按钮，然后需要避开危险并在七步内点亮祭坛。",
        "3、影子出现前：左-下-左-右-右-右-右。",
        "1、位置：在半神议院黎明云崖的地图中央处。",
        "2、影子出现前：右-右-下-右-左-上-左。",
        "1、位置：在半神议院黎明云崖地图的上半部分。",
        "2、影子出现前：左-左-左-右-左-左。",
    ]
    # three puzzles are placed inside the area, a fourth line places a chest elsewhere
    assert location_statement_count(steps, ["「半神议院」黎明云崖", "黄金替罪羊"]) == 3
    assert location_statement_count(
        ["1、位置：在【无晖祈堂】黎明云崖，地图的中央。", "1、位置：在【无晖祈堂】黎明云崖，地图下方。"],
        ["「无晖祈堂」黎明云崖"],
    ) == 2
    # a line that misspells the area is not one of its locations
    assert location_statement_count(
        ["1、位置：在【无晖祈堂】黎明云崖，地图的中央。", "1、位置：在【无晖祈堂】委黎明云崖，地图下方。"],
        ["「无晖祈堂」黎明云崖"],
    ) == 0
    # a single location line is not a count of anything
    assert location_statement_count(["位置：半神议院黎明云崖地图的左下方。"], ["「半神议院」黎明云崖"]) == 0
    # lines that never name the area do not count
    assert location_statement_count(["位置：地图的左下方。", "位置：地图的中央。"], ["「半神议院」黎明云崖"]) == 0


def test_a_page_that_places_every_puzzle_in_the_area_binds_it(tmp_path):
    db = _db(tmp_path)
    lines = [
        "崩坏星穹铁道半神议院黎明云崖黄金替罪羊攻略",
        "1、位置：半神议院黎明云崖地图的左下方。",
        "2、到达后点击【调查】按钮，然后需要避开危险并在七步内点亮祭坛。",
        "3、影子出现前：左-下-左-右-右-右-右。",
        "4、影子出现后：左-右-右-左-左-右。",
        "1、位置：在半神议院黎明云崖的地图中央处。",
        "2、影子出现前：右-右-下-右-左-上-左。",
        "3、影子出现后：左-左-左-右-左-左。",
        "1、位置：在半神议院黎明云崖地图的上半部分。",
        "2、影子出现前：左-左-左-右-左-左。",
    ]
    points = [
        {"source_point_id": "36%02d" % index, "map_id": "414", "map_name": "1层", "region": "「半神议院」黎明云崖", "label": "黄金替罪羊", "map_path": "翁法罗斯 / 「半神议院」黎明云崖 / 1层"}
        for index in (25, 28, 32)
    ]
    page = _page(db, "\n".join(lines), url="https://t.test/locations")
    _item(db, page, lines, map_name="崩坏星穹铁道半神议院黎明云崖黄金替罪羊攻略", topic="golden_scapegoat")

    report = approve_grounded(db, topic="golden_scapegoat", official_points=points)
    assert report["targets"] == 1
    assert report["approved"][0]["target_key"] == "set:3625-3628-3632:topic:golden_scapegoat"
    db.close()


def test_an_item_that_never_names_a_region_does_not_bind_one(tmp_path):
    """A slice of the page's furniture ("最新专题" and its link list) is not a guide."""
    db = _db(tmp_path)
    page = _page(db, EACH_ARTICLE, url="https://t.test/each3")
    _item(db, page, [
        "类似崩坏3的游戏有哪些",
        "崩坏星穹铁道1.1什么时候上线",
        "战锤40K星际战士2加速器",
    ], map_name="最新专题")

    # the page states the two areas and their numbers, but this item walks none of
    # them: membership comes from the item, so there is nothing to bind
    report = approve_grounded(db, topic="golden_scapegoat", official_points=EACH_POINTS)
    assert report["targets"] == 0
    db.close()


def test_a_shared_number_that_does_not_match_the_areas_is_refused(tmp_path):
    """The page says two in each area; the two areas hold six points, not four."""
    db = _db(tmp_path)
    article = EACH_ARTICLE.replace("每个区域各有3个谜题", "每个区域各有2个谜题")
    page = _page(db, article, url="https://t.test/each2")
    steps = [line for line in article.split("\n") if line]
    _item(db, page, steps, map_name="崩坏星穹铁道3.1黄金替罪羊解谜攻略")

    report = approve_grounded(db, topic="golden_scapegoat", official_points=EACH_POINTS)
    assert report["targets"] == 0 and report["rejected_count"] == 0
    db.close()


def test_line_leading_ordinals_are_a_count_too(tmp_path):
    """九游「1、集市左侧解谜」、游侠「一、灾梦余温」：行首序号也是页面自己的计数。

    一区里的编号是连续的（4、5、6、7），所以允许连续段；但段长仍须等于该区域点位数——
    这条由 `_region_set_binding` 把关。
    """
    assert line_ordinal_count(
        "1、集市左侧解谜 右2步 2、天宫南侧解谜 右2步 3、私人温泉房间外解谜 右4步 4、三季海庭传送点右侧解谜 右2步"
    ) == 4
    assert line_ordinal_count(
        "5、下方入口处下层角落解谜 右2步 6、纷争正殿的2层解谜 左1步 7、铸魂仪门右侧解谜点 左1步",
        allow_run=True,
    ) == 3
    # 「右2步，左1步」是走法，不是编号
    assert line_ordinal_count("右2步，左1步，上1步，右2步") == 0

    from hsrmap.guides.review.service import _region_set_binding

    points = [
        {
            "source_point_id": "29%02d" % index,
            "map_id": "420",
            "map_name": "1层",
            "region": "「永恒圣城」奥赫玛",
            "label": "黄金替罪羊",
            "map_path": "翁法罗斯 / 「永恒圣城」奥赫玛 / 1层",
        }
        for index in (54, 55, 56, 57)
    ]
    article = "「永恒圣城」奥赫玛 1、集市左侧解谜 右2步，左1步，上1步，右2步 2、天宫南侧解谜 右2步，左1步，右1步 3、私人温泉房间外解谜 右4步，左1步 4、三季海庭传送点右侧解谜 右2步，左3步"
    draft = {
        "map_name": "「永恒圣城」奥赫玛",
        "steps": [
            {"text": "永恒圣城奥赫玛"},
            {"text": "1、集市左侧解谜"},
            {"text": "右2步，左1步，上1步，右2步"},
            {"text": "2、天宫南侧解谜"},
            {"text": "右2步，左1步，右1步"},
            {"text": "3、私人温泉房间外解谜"},
            {"text": "右4步，左1步"},
            {"text": "4、三季海庭传送点右侧解谜"},
            {"text": "右2步，左3步"},
        ],
    }
    item_text = draft["map_name"] + " " + " ".join(step["text"] for step in draft["steps"])
    binding, target = _region_set_binding(draft, "golden_scapegoat", points, article, item_text)
    assert binding == "REGION_SET"
    assert target == "set:2954-2955-2956-2957:topic:golden_scapegoat"


def test_a_shared_region_name_is_resolved_by_its_own_count(tmp_path):
    """「雅努萨波利斯」同时是「神谕圣地」和「命运重渊」的尾巴：用页面自己说的数量分辨。

    页面写「二、雅努萨波利斯」并给了 4 段解密步骤；官方两个同名区域一个 3 点、一个 4 点，
    只有 4 点的那个对得上。两个都对得上（或都对不上）时照旧拒绝。
    """
    from hsrmap.guides.review.service import _region_set_binding

    points = [
        {
            "source_point_id": "70%02d" % index,
            "map_id": "430",
            "map_name": "1层",
            "region": "「神谕圣地」雅努萨波利斯",
            "label": "黄金替罪羊",
            "map_path": "翁法罗斯 / 「神谕圣地」雅努萨波利斯 / 1层",
        }
        for index in (1, 2, 3)
    ] + [
        {
            "source_point_id": "71%02d" % index,
            "map_id": "431",
            "map_name": "2层",
            "region": "「命运重渊」雅努萨波利斯",
            "label": "黄金替罪羊",
            "map_path": "翁法罗斯 / 「命运重渊」雅努萨波利斯 / 2层",
        }
        for index in (11, 12, 13, 15)
    ]
    article = "二、雅努萨波利斯 解密步骤 1、右右-左上右右。 2、右右左右左-右右右右右右。 3、右右-右右左右。 4、右左右右-右右左右右右。"
    draft = {
        "map_name": "雅努萨波利斯",
        "steps": [
            {"text": "1、解密步骤：右右-左上右右。"},
            {"text": "2、解密步骤：右右左右左-右右右右右右。"},
            {"text": "3、解密步骤：右右-右右左右。"},
            {"text": "4、解密步骤：右左右右-右右左右右右。"},
        ],
    }
    item_text = draft["map_name"] + " " + " ".join(step["text"] for step in draft["steps"])
    binding, target = _region_set_binding(draft, "golden_scapegoat", points, article, item_text)
    assert binding == "REGION_SET"
    assert target == "set:7111-7112-7113-7115:topic:golden_scapegoat"
    #: 数量对不上任何候选时仍然拒绝
    draft["steps"] = draft["steps"][:2]
    item_text = draft["map_name"] + " " + " ".join(step["text"] for step in draft["steps"])
    assert _region_set_binding(draft, "golden_scapegoat", points, article, item_text) == ("", "")

def test_step_numbering_is_not_a_puzzle_count():
    """「1、位置：… 2、影子出现前：… 3、影子出现后：…」是一个谜题的三步，不是三个谜题。

    17173 的无晖祈堂那篇就是这个形状：序号行里带「位置」时整串编号都按步骤模板处理。
    """
    lines = (
        "1、位置：在【无晖祈堂】委黎明云崖，地图下方。 "
        "2、影子出现前：左、下、右、左、下、右、右。 "
        "3、影子出现后：左、左、左、右、左、左、左。"
    )
    assert line_ordinal_count(lines, allow_run=True) == 0
    #: 同一篇里「1、集市左侧解谜 右2步」这种编谜题的写法照旧算数
    assert line_ordinal_count("1、集市左侧解谜 右2步 2、天宫南侧解谜 右2步", allow_run=True) == 2
