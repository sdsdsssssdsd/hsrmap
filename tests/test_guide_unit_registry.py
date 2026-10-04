from hsrmap.guides.units.registry import build_units_for_topic


_COLLECTIBLE_SECTION = {
    "map_name": "黄金的时刻",
    "resolved_map": {"map_id": "508"},
    "texts": ["第1只在喷泉", "第2只在台阶", "第3只在阳台"],
    "observations": [
        {"role": "location_map", "article_ordinal": 1, "instruction_text": ["第1只在喷泉"]},
        {"role": "collectible_location", "article_ordinal": 2, "instruction_text": ["第2只在台阶"]},
        {"role": "collectible_location", "article_ordinal": 3, "instruction_text": ["第3只在阳台"]},
    ],
}


def test_collectible_keeps_one_route_unit_per_map():
    units = build_units_for_topic("origami_bird", [_COLLECTIBLE_SECTION])
    assert len(units) == 1
    assert units[0]["scope"] == "MAP_LABEL"
    assert units[0]["item_count"] == 3
    assert units[0].get("article_ordinal") in {None, ""}
    assert units[0]["map_name"] == "黄金的时刻"


def test_nymph_uses_same_collectible_builder_not_puzzle_split():
    units = build_units_for_topic("nymph", [_COLLECTIBLE_SECTION])
    assert len(units) == 1
    assert units[0]["scope"] == "MAP_LABEL"


def test_ticker_still_splits_puzzle_units_by_ordinal():
    section = {
        "map_name": "黄金的时刻",
        "texts": [
            "黄金的时刻（4个）",
            "第1个【梦境迷钟】修复解密",
            "将黄色模块移到左下",
            "第2个【梦境迷钟】修复解密",
            "将镜子往右下移动",
        ],
        "observations": [],
    }
    units = build_units_for_topic("dream_ticker", [section])
    assert len(units) == 2
    assert [item["article_ordinal"] for item in units] == [1, 2]
    assert units[0].get("scope") in {None, "POINT", "point"}
