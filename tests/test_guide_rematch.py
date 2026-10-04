from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.rematch import rematch_topic


def test_rematch_fills_official_candidates_and_never_publishes(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/ticker", "title": "黄金的时刻迷钟"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"dream_ticker","map_name":"黄金的时刻","steps":[{"text":"调指针"}]}'),
    )
    db.conn.commit()
    official = [
        {"source_point_id": "101", "map_name": "黄金的时刻", "map_id": "508", "label": "梦境迷钟", "x": 10, "y": 20},
        {"source_point_id": "102", "map_name": "黄金的时刻", "map_id": "508", "label": "梦境迷钟", "x": 80, "y": 90},
    ]
    out = rematch_topic(db, "dream_ticker", official_points=official)
    assert out["publish"] == "skipped"
    assert out["updated"] >= 1
    row = db.conn.execute("SELECT source_point_id, draft_json, status FROM review_item").fetchone()
    import json

    draft = json.loads(row["draft_json"])
    assert draft["candidate_points"]
    assert {item["source_point_id"] for item in draft["candidate_points"]} == {"101", "102"}
    assert db.list_for_point("101") == []
    assert db.list_for_point("102") == []
    assert row["status"] != "APPROVED"
    db.close()


def test_rematch_reads_map_from_numbered_article_heading(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/t2", "title": "迷钟"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"dream_ticker","map_name":"1、黄金的时刻（4个）","steps":[]}'),
    )
    db.conn.commit()
    official = [
        {
            "source_point_id": "1609",
            "map_name": "3层",
            "map_path": "匹诺康尼 / 黄金的时刻 / 3层",
            "region": "黄金的时刻",
            "label": "梦境迷钟",
        },
        {
            "source_point_id": "1597",
            "map_name": "2层",
            "map_path": "匹诺康尼 / 黄金的时刻 / 2层",
            "region": "黄金的时刻",
            "label": "梦境迷钟",
        },
    ]
    rematch_topic(db, "dream_ticker", official_points=official)
    import json

    draft = json.loads(db.conn.execute("SELECT draft_json FROM review_item").fetchone()[0])
    assert {item["source_point_id"] for item in draft["candidate_points"]} == {"1609", "1597"}
    assert draft.get("map_name") in {"黄金的时刻", "1、黄金的时刻（4个）"}
    db.close()


def test_heading_matches_origami_university_short_name():
    from hsrmap.guides.matching.rematch import heading_map_name

    official = [
        {
            "source_point_id": "2835",
            "map_name": "匹诺康尼折纸大学学院",
            "map_path": "匹诺康尼 / 匹诺康尼折纸大学学院 / 匹诺康尼折纸大学学院",
            "region": "匹诺康尼折纸大学学院",
            "label": "梦境迷钟",
        }
    ]
    assert heading_map_name("折纸大学全梦境迷钟解法", official) == "匹诺康尼折纸大学学院"


def test_rematch_reads_maps_from_step_text_when_title_is_generic(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/22", "title": "2.2新地图"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (
            page["id"],
            '{"topic_key":"dream_ticker","map_name":"2.2新地图梦境迷钟解法","steps":[{"text":"匹诺康尼大剧院（寂寞迷钟）"},{"text":"苏乐达热砂海选会场（巨星迷钟）"}]}',
        ),
    )
    db.conn.commit()
    official = [
        {"source_point_id": "2373", "map_name": "匹诺康尼大剧院", "map_path": "匹诺康尼 / 匹诺康尼大剧院 / 匹诺康尼大剧院", "region": "匹诺康尼大剧院", "label": "梦境迷钟"},
        {"source_point_id": "2245", "map_name": "1层", "map_path": "匹诺康尼 / 苏乐达™热砂海选会场 / 1层", "region": "苏乐达™热砂海选会场", "label": "梦境迷钟"},
    ]
    rematch_topic(db, "dream_ticker", official_points=official)
    import json

    rows = list(db.conn.execute("SELECT status, draft_json FROM review_item"))
    cand_ids = set()
    maps = set()
    for row in rows:
        assert row["status"] != "APPROVED"
        draft = json.loads(row["draft_json"] or "{}")
        maps.add(draft.get("resolved_map_name") or draft.get("map_name"))
        for item in draft.get("candidate_points") or []:
            cand_ids.add(str(item.get("source_point_id") or ""))
    assert cand_ids >= {"2373", "2245"}
    db.close()


def test_rematch_splits_collectible_parent_region_into_floor_maps(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/clock", "title": "克劳克小鸟"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"origami_bird","map_name":"克劳克影视乐园","steps":[{"text":"全图折纸小鸟"}]}'),
    )
    db.conn.commit()
    official = [
        {
            "source_point_id": "2151",
            "map_id": "215",
            "map_name": "1层",
            "map_path": "匹诺康尼 / 克劳克影视乐园 / 1层",
            "region": "克劳克影视乐园",
            "label": "折纸小鸟",
        },
        {
            "source_point_id": "2161",
            "map_id": "216",
            "map_name": "2层",
            "map_path": "匹诺康尼 / 克劳克影视乐园 / 2层",
            "region": "克劳克影视乐园",
            "label": "折纸小鸟",
        },
        {
            "source_point_id": "2162",
            "map_id": "216",
            "map_name": "2层",
            "map_path": "匹诺康尼 / 克劳克影视乐园 / 2层",
            "region": "克劳克影视乐园",
            "label": "折纸小鸟",
        },
    ]
    rematch_topic(db, "origami_bird", official_points=official)
    import json

    rows = list(db.conn.execute("SELECT status, draft_json FROM review_item"))
    keys = set()
    for row in rows:
        assert row["status"] != "APPROVED"
        draft = json.loads(row["draft_json"] or "{}")
        keys.add(draft.get("target_key"))
        assert draft.get("target_type") == "MAP_LABEL"
    assert keys == {"map:215:topic:origami_bird", "map:216:topic:origami_bird"}
    db.close()


def test_heading_matches_quoted_amphoreus_inner_name():
    from hsrmap.guides.matching.rematch import heading_map_names

    official = [
        {
            "source_point_id": "4501",
            "map_id": "450",
            "map_name": "1层",
            "map_path": "翁法罗斯 / 「酣歌海垠」 斯缇科西亚 / 1层",
            "region": "「酣歌海垠」 斯缇科西亚",
            "label": "创生若虫",
        }
    ]
    hits = heading_map_names("《崩坏星穹铁道》酣歌海垠全若虫收集攻略", official)
    assert any("酣歌海垠" in str(hit) for hit in hits)


def test_rematch_splits_quoted_region_from_plain_title(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/nymph", "title": "酣歌海垠若虫"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"nymph","map_name":"《崩坏星穹铁道》酣歌海垠全若虫收集攻略","steps":[{"text":"共10只"}]}'),
    )
    db.conn.commit()
    official = [
        {
            "source_point_id": "4501",
            "map_id": "450",
            "map_name": "1层",
            "map_path": "翁法罗斯 / 「酣歌海垠」 斯缇科西亚 / 1层",
            "region": "「酣歌海垠」 斯缇科西亚",
            "label": "创生若虫",
        },
        {
            "source_point_id": "4511",
            "map_id": "451",
            "map_name": "-1层",
            "map_path": "翁法罗斯 / 「酣歌海垠」 斯缇科西亚 / -1层",
            "region": "「酣歌海垠」 斯缇科西亚",
            "label": "创生若虫",
        },
        {
            "source_point_id": "4161",
            "map_id": "416",
            "map_name": "1层",
            "map_path": "翁法罗斯 / 「龙骸古城」斯缇科西亚 / 1层",
            "region": "「龙骸古城」斯缇科西亚",
            "label": "创生若虫",
        },
    ]
    rematch_topic(db, "nymph", official_points=official)
    import json

    keys = set()
    for row in db.conn.execute("SELECT status, draft_json FROM review_item"):
        assert row["status"] != "APPROVED"
        draft = json.loads(row["draft_json"] or "{}")
        if draft.get("target_key"):
            keys.add(draft.get("target_key"))
    assert keys == {"map:450:topic:nymph", "map:451:topic:nymph"}
    db.close()


def test_rematch_does_not_explode_parent_world_name(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/dust", "title": "二维市尘灵"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (
            page["id"],
            '{"topic_key":"nameless_dust_spirit","map_name":"二相乐园 / 二维市 / 1层","map_id":"518","target_key":"map:518:topic:nameless_dust_spirit","steps":[{"text":"二相乐园二维市全收集"}]}',
        ),
    )
    db.conn.commit()
    official = [
        {
            "source_point_id": "5181",
            "map_id": "518",
            "map_name": "1层",
            "map_path": "二相乐园 / 二维市 / 1层",
            "region": "二维市",
            "label": "无名尘灵",
        },
        {
            "source_point_id": "5191",
            "map_id": "519",
            "map_name": "-1层",
            "map_path": "二相乐园 / 二维市 / -1层",
            "region": "二维市",
            "label": "无名尘灵",
        },
        {
            "source_point_id": "5201",
            "map_id": "520",
            "map_name": "1层",
            "map_path": "二相乐园 / 绘世学院 / 1层",
            "region": "绘世学院",
            "label": "无名尘灵",
        },
    ]
    rematch_topic(db, "nameless_dust_spirit", official_points=official)
    import json

    keys = set()
    for row in db.conn.execute("SELECT status, draft_json FROM review_item"):
        assert row["status"] != "APPROVED"
        draft = json.loads(row["draft_json"] or "{}")
        if draft.get("target_key"):
            keys.add(draft.get("target_key"))
    assert "map:520:topic:nameless_dust_spirit" not in keys
    assert keys == {"map:518:topic:nameless_dust_spirit"}
    db.close()


def test_rematch_single_official_map_writes_map_id(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/aelie", "title": "哀丽秘榭若虫"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"nymph","map_name":"哀丽秘榭全若虫收集","steps":[{"text":"共10只"}]}'),
    )
    db.conn.commit()
    official = [
        {
            "source_point_id": "4481",
            "map_id": "448",
            "map_name": "哀丽秘榭",
            "map_path": "翁法罗斯 / 哀丽秘榭 / 哀丽秘榭",
            "region": "哀丽秘榭",
            "label": "创生若虫",
        }
    ]
    rematch_topic(db, "nymph", official_points=official)
    import json

    draft = json.loads(db.conn.execute("SELECT draft_json FROM review_item").fetchone()[0])
    assert draft.get("target_key") == "map:448:topic:nymph"
    assert draft.get("map_id") == "448"
    db.close()


def test_heading_does_not_invent_hyphen_variant_or_miss_trademark():
    from hsrmap.guides.matching.rematch import heading_map_names, leaf_map_names

    official = [
        {
            "source_point_id": "2771",
            "map_id": "277",
            "map_name": "苏乐达™热砂海选会场",
            "map_path": "匹诺康尼 / 苏乐达™热砂海选会场 / 苏乐达™热砂海选会场",
            "region": "苏乐达™热砂海选会场",
            "label": "折纸小鸟",
        },
        {
            "source_point_id": "2451",
            "map_id": "245",
            "map_name": "1层",
            "map_path": "匹诺康尼 / 苏乐达-1号-左 / 1层",
            "region": "苏乐达-1号-左",
            "label": "折纸小鸟",
        },
        {
            "source_point_id": "2131",
            "map_id": "213",
            "map_name": "1层",
            "map_path": "匹诺康尼 / 朝露公馆 / 1层",
            "region": "朝露公馆",
            "label": "折纸小鸟",
        },
        {
            "source_point_id": "2051",
            "map_id": "205",
            "map_name": "4",
            "map_path": "匹诺康尼 / 朝露公馆-1 / 4",
            "region": "朝露公馆-1",
            "label": "折纸小鸟",
        },
    ]
    sand = heading_map_names("苏乐达热砂海选会场折纸小鸟", official)
    assert any("热砂" in str(hit) for hit in sand)
    dawn = leaf_map_names("朝露公馆折纸小鸟收集指南", official)
    assert "朝露公馆" in dawn
    assert "朝露公馆-1" not in dawn

