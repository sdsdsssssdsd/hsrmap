from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.registry import match_for_topic
from hsrmap.guides.matching.rematch import heading_map_names, rematch_topic
from hsrmap.guides.review.service import create_item
from hsrmap.guides.topics.loader import get_topic, list_topics


def test_registry_loads_grease_and_dream_ticker():
    keys = {item["topic_key"] for item in list_topics()}
    assert "floating_grease" in keys
    assert "dream_ticker" in keys
    assert "origami_bird" in keys
    assert "nymph" in keys
    assert "nameless_dust_spirit" in keys
    assert "hidden_treasure" in keys
    assert get_topic("hidden_treasure").get("enabled") is False


def test_registry_loads_named_special_topics():
    keys = {item["topic_key"] for item in list_topics()}
    assert "golden_scapegoat" in keys
    assert "miracle_orb" in keys
    assert "king_bucket" in keys
    assert "dimensional_trotter" in keys
    assert get_topic("golden_scapegoat")["scope"] == "POINT"
    assert get_topic("miracle_orb")["scope"] == "MAP_LABEL"
    assert get_topic("miracle_orb")["matcher"]["profile"] == "collectible_route_v1"


def test_grease_topic_is_point_puzzle():
    topic = get_topic("floating_grease")
    assert topic["scope"] == "POINT"
    assert topic["guide_kind"] == "PUZZLE"
    assert "浮脂溯源" in " ".join(topic["official_labels"]["names"])
    assert topic["matcher"]["profile"] == "puzzle_point_v2"


def test_collectible_route_does_not_invent_point_id():
    out = match_for_topic(
        "origami_bird",
        {"map_name": "黄金的时刻", "map_id": "508"},
        [{"source_point_id": "11", "map_id": "508"}, {"source_point_id": "12", "map_id": "508"}],
    )
    assert out["source_point_id"] in {"", "pending"}
    assert out["status"] == "review"
    assert out.get("target_type") == "MAP_LABEL"


def test_unique_on_map_profile_suggests_only_candidate():
    topic = get_topic("dream_ticker")
    previous = topic["matcher"]["profile"]
    topic["matcher"]["profile"] = "unique_on_map_v1"
    try:
        out = match_for_topic("dream_ticker", {"map_name": "黄金的时刻"}, [{"source_point_id": "99"}])
    finally:
        topic["matcher"]["profile"] = previous
    assert out["source_point_id"] == "99"
    assert out["status"] == "auto"


def test_heading_skips_parent_world_and_chrome_title():
    points = [
        {"map_name": "时光归墟", "region": "葬忆彼岸", "map_path": "翁法罗斯 / 葬忆彼岸 / 时光归墟", "source_point_id": "1", "label": "黄金替罪羊"},
        {"map_name": "无名泰坦大墓", "region": "灾梦余梦", "map_path": "翁法罗斯 / 灾梦余梦 / 无名泰坦大墓", "source_point_id": "2", "label": "黄金替罪羊"},
    ]
    hay = "《崩坏星穹铁道》3.7黄金替罪羊 （1）「灾梦余梦」无名泰坦大墓 （2）「葬忆彼岸」时光归墟"
    names = heading_map_names(hay, points)
    assert "翁法罗斯" not in names
    assert "时光归墟" in names
    assert "无名泰坦大墓" in names


def test_heading_matches_quoted_official_region_suffix():
    points = [
        {
            "map_name": "1层",
            "region": "「灾梦余温」无名泰坦大墓",
            "map_path": "翁法罗斯 / 「灾梦余温」无名泰坦大墓 / 1层",
            "source_point_id": "1",
            "label": "黄金替罪羊",
        },
        {
            "map_name": "1层",
            "region": "「葬忆彼岸」时光归墟",
            "map_path": "翁法罗斯 / 「葬忆彼岸」时光归墟 / 1层",
            "source_point_id": "2",
            "label": "黄金替罪羊",
        },
    ]
    hay = "（1）「灾梦余梦」无名泰坦大墓 （2）「葬忆彼岸」时光归墟"
    names = heading_map_names(hay, points)
    assert any("无名泰坦大墓" in name for name in names)
    assert any("时光归墟" in name for name in names)


def test_rematch_splits_chrome_title_into_official_maps(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/goat", "title": "3.7黄金替罪羊"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "golden_scapegoat",
                "map_name": "《崩坏星穹铁道》3.7黄金替罪羊全关卡解谜攻略",
                "steps": [
                    {"text": "（1）「灾梦余梦」无名泰坦大墓地图共有4个黄金替罪羊解密"},
                    {"text": "（2）「葬忆彼岸」时光归墟地图共有4个黄金替罪羊解密"},
                ],
            },
        },
    )
    official = [
        {"source_point_id": "501", "map_id": "a", "map_name": "无名泰坦大墓", "region": "灾梦余梦", "map_path": "翁法罗斯 / 灾梦余梦 / 无名泰坦大墓", "label": "黄金替罪羊"},
        {"source_point_id": "502", "map_id": "a", "map_name": "无名泰坦大墓", "region": "灾梦余梦", "map_path": "翁法罗斯 / 灾梦余梦 / 无名泰坦大墓", "label": "黄金替罪羊"},
        {"source_point_id": "601", "map_id": "b", "map_name": "时光归墟", "region": "葬忆彼岸", "map_path": "翁法罗斯 / 葬忆彼岸 / 时光归墟", "label": "黄金替罪羊"},
        {"source_point_id": "602", "map_id": "b", "map_name": "时光归墟", "region": "葬忆彼岸", "map_path": "翁法罗斯 / 葬忆彼岸 / 时光归墟", "label": "黄金替罪羊"},
    ]
    report = rematch_topic(db, "golden_scapegoat", official_points=official)
    rows = [dict(row) for row in db.conn.execute("SELECT id, source_point_id, status, draft_json FROM review_item")]
    maps = set()
    for row in rows:
        import json

        draft = json.loads(row["draft_json"] or "{}")
        maps.add(draft.get("resolved_map_name") or draft.get("map_name"))
        assert row["source_point_id"] in {"", None}
        assert row["status"] != "APPROVED"
    assert "时光归墟" in maps
    assert "无名泰坦大墓" in maps
    assert report["publish"] == "skipped"
    assert report["updated"] >= 2


def test_rematch_splits_when_official_map_name_is_floor(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/goat-floor", "title": "黄金替罪羊"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "golden_scapegoat",
                "map_name": "《崩坏星穹铁道》黄金替罪羊全关卡解谜攻略",
                "steps": [
                    {"text": "（1）「永恒圣城」奥赫玛地图共有4个黄金替罪羊解密"},
                    {"text": "（2）「葬忆彼岸」时光归墟地图共有4个黄金替罪羊解密"},
                ],
            },
        },
    )
    official = [
        {
            "source_point_id": "2957",
            "map_id": "331",
            "map_name": "1层",
            "region": "「永恒圣城」奥赫玛",
            "map_path": "翁法罗斯 / 「永恒圣城」奥赫玛 / 1层",
            "label": "黄金替罪羊",
        },
        {
            "source_point_id": "2956",
            "map_id": "331",
            "map_name": "1层",
            "region": "「永恒圣城」奥赫玛",
            "map_path": "翁法罗斯 / 「永恒圣城」奥赫玛 / 1层",
            "label": "黄金替罪羊",
        },
        {
            "source_point_id": "8801",
            "map_id": "900",
            "map_name": "1层",
            "region": "「葬忆彼岸」时光归墟",
            "map_path": "翁法罗斯 / 「葬忆彼岸」时光归墟 / 1层",
            "label": "黄金替罪羊",
        },
        {
            "source_point_id": "8802",
            "map_id": "900",
            "map_name": "1层",
            "region": "「葬忆彼岸」时光归墟",
            "map_path": "翁法罗斯 / 「葬忆彼岸」时光归墟 / 1层",
            "label": "黄金替罪羊",
        },
    ]
    report = rematch_topic(db, "golden_scapegoat", official_points=official)
    rows = [dict(row) for row in db.conn.execute("SELECT id, source_point_id, status, draft_json FROM review_item")]
    assert len(rows) >= 2
    resolved = set()
    cand_ids = set()
    for row in rows:
        import json

        draft = json.loads(row["draft_json"] or "{}")
        resolved.add(draft.get("resolved_map_name") or draft.get("map_name"))
        for cand in draft.get("candidate_points") or []:
            cand_ids.add(str(cand.get("source_point_id") or ""))
        assert row["status"] != "APPROVED"
        assert row["source_point_id"] in {"", None} or row["source_point_id"] in {"2957", "2956", "8801", "8802"}
    assert any(name == "「永恒圣城」奥赫玛" or name == "奥赫玛" for name in resolved)
    assert any("时光归墟" in str(name) and "奥赫玛" not in str(name) for name in resolved)
    assert cand_ids <= {"2957", "2956", "8801", "8802"}
    assert cand_ids
    assert report["publish"] == "skipped"


def test_rematch_splits_mixed_slash_regions_as_units(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/mix", "title": "迷钟"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "dream_ticker",
                "map_name": "苏乐达-1号-右",
                "resolved_map_name": "苏乐达-1号-右 / 匹诺康尼大剧院",
                "steps": [
                    {"text": "苏乐达-1号-右迷钟"},
                    {"text": "匹诺康尼大剧院迷钟"},
                ],
            },
        },
    )
    official = [
        {
            "source_point_id": "2245",
            "map_id": "a",
            "map_name": "1层",
            "region": "苏乐达-1号-右",
            "map_path": "匹诺康尼 / 苏乐达-1号-右 / 1层",
            "label": "梦境迷钟",
        },
        {
            "source_point_id": "2373",
            "map_id": "b",
            "map_name": "匹诺康尼大剧院",
            "region": "匹诺康尼大剧院",
            "map_path": "匹诺康尼 / 匹诺康尼大剧院 / 匹诺康尼大剧院",
            "label": "梦境迷钟",
        },
    ]
    rematch_topic(db, "dream_ticker", official_points=official)
    rows = [dict(row) for row in db.conn.execute("SELECT source_point_id, status, draft_json FROM review_item")]
    import json

    maps = set()
    for row in rows:
        draft = json.loads(row["draft_json"] or "{}")
        maps.add(draft.get("resolved_map_name") or draft.get("map_name"))
        assert " / " not in str(draft.get("resolved_map_name") or "")
        assert row["status"] != "APPROVED"
    assert len(rows) >= 2
    assert any("苏乐达" in str(name) for name in maps)
    assert any("大剧院" in str(name) for name in maps)


def test_rematch_title_unique_map_ignores_sidebar_maps(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/jixie", "title": "机械聚落宝箱"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "dimensional_trotter",
                "map_name": "《崩坏星穹铁道》机械聚落全宝箱收集教程",
                "steps": [
                    {"text": "机械聚落2层次元扑满"},
                    {"text": "相关攻略 朝露公馆宝箱"},
                    {"text": "基座舱段战利品"},
                ],
            },
        },
    )
    official = [
        {
            "source_point_id": "649",
            "map_id": "3",
            "map_name": "1层",
            "region": "机械聚落",
            "map_path": "雅利洛-VI / 机械聚落 / 1层",
            "label": "次元扑满",
        },
        {
            "source_point_id": "2217",
            "map_id": "2",
            "map_name": "沙盘模型",
            "region": "朝露公馆",
            "map_path": "匹诺康尼 / 朝露公馆 / 沙盘模型",
            "label": "次元扑满",
        },
        {
            "source_point_id": "261",
            "map_id": "4",
            "map_name": "1层",
            "region": "基座舱段",
            "map_path": "空间站「黑塔」 / 基座舱段 / 1层",
            "label": "次元扑满",
        },
    ]
    rematch_topic(db, "dimensional_trotter", official_points=official)
    rows = [dict(row) for row in db.conn.execute("SELECT source_point_id, status, draft_json FROM review_item")]
    assert len(rows) == 1
    import json

    draft = json.loads(rows[0]["draft_json"] or "{}")
    maps = {draft.get("resolved_map_name") or draft.get("map_name")}
    assert "朝露公馆" not in maps
    assert "基座舱段" not in maps
    assert any("机械聚落" in str(name) for name in maps)
    assert rows[0]["source_point_id"] == "649"
    assert rows[0]["status"] != "APPROVED"


def test_rematch_does_not_resplit_resolved_region_with_sidebar(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/trotter", "title": "雅利洛六号"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "516",
            "status": "AUTO_SUGGEST",
            "draft": {
                "topic_key": "dimensional_trotter",
                "map_name": "铁卫禁区",
                "resolved_map_name": "铁卫禁区",
                "steps": [
                    {"text": "铁卫禁区扑满位置"},
                    {"text": "《巫师3重制版》官方中文版下载"},
                    {"text": "朝露公馆宝箱"},
                ],
            },
        },
    )
    official = [
        {
            "source_point_id": "516",
            "map_id": "1",
            "map_name": "1层",
            "region": "铁卫禁区",
            "map_path": "雅利洛-VI / 铁卫禁区 / 1层",
            "label": "次元扑满",
        },
        {
            "source_point_id": "2217",
            "map_id": "2",
            "map_name": "沙盘模型",
            "region": "朝露公馆",
            "map_path": "匹诺康尼 / 朝露公馆 / 沙盘模型",
            "label": "次元扑满",
        },
    ]
    rematch_topic(db, "dimensional_trotter", official_points=official)
    rematch_topic(db, "dimensional_trotter", official_points=official)
    rows = [dict(row) for row in db.conn.execute("SELECT status, draft_json FROM review_item")]
    maps = set()
    for row in rows:
        import json

        draft = json.loads(row["draft_json"] or "{}")
        maps.add(draft.get("resolved_map_name") or draft.get("map_name"))
        assert row["status"] != "APPROVED"
    assert len(rows) == 1
    assert "朝露公馆" not in maps
    assert any("铁卫禁区" in str(name) for name in maps)


def test_rematch_skips_rejected_items(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/rej", "title": "t"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "2217",
            "status": "REJECTED",
            "draft": {"topic_key": "dimensional_trotter", "map_name": "朝露公馆"},
        },
    )
    official = [
        {
            "source_point_id": "2217",
            "map_id": "2",
            "map_name": "沙盘模型",
            "region": "朝露公馆",
            "map_path": "匹诺康尼 / 朝露公馆 / 沙盘模型",
            "label": "次元扑满",
        }
    ]
    rematch_topic(db, "dimensional_trotter", official_points=official)
    row = db.conn.execute("SELECT status FROM review_item").fetchone()
    assert row["status"] == "REJECTED"
