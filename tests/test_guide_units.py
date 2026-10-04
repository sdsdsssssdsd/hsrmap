from hsrmap.guides.regions.units import build_guide_units


def test_haiyuan_splits_into_three_units():
    section = {
        "map_name": "海原市",
        "observations": [
            {"block_id": "l1", "role": "location_map", "article_ordinal": 1, "instruction_text": ["a"]},
            {"block_id": "s1", "role": "puzzle_step", "article_ordinal": 1, "instruction_text": ["b"]},
            {"block_id": "l2", "role": "location_map", "article_ordinal": 2, "instruction_text": ["c"]},
            {"block_id": "s2", "role": "puzzle_step", "article_ordinal": 2, "instruction_text": ["d"]},
            {"block_id": "l3", "role": "location_map", "article_ordinal": 3, "instruction_text": ["e"]},
            {"block_id": "s3", "role": "puzzle_step", "article_ordinal": 3, "instruction_text": ["f"]},
        ],
    }
    units = build_guide_units([section])
    assert len(units) == 3
    assert [item["article_ordinal"] for item in units] == [1, 2, 3]


def test_unit_keeps_image_spatial_anchor():
    section = {
        "map_name": "二维市",
        "observations": [
            {
                "block_id": "l1",
                "role": "location_map",
                "article_ordinal": 3,
                "spatial_anchor": "左下角",
                "instruction_text": ["3号点位：【二维市】左下角"],
            }
        ],
    }
    units = build_guide_units([section])
    assert units[0]["spatial_anchor"] == "左下角"


def test_text_ordinals_split_units_without_images():
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
    units = build_guide_units([section])
    assert len(units) == 2
    assert [item["article_ordinal"] for item in units] == [1, 2]
    assert units[0]["map_name"] == "黄金的时刻"
    assert any("黄色模块" in step["text"] for step in units[0]["steps"])


def test_heading_ordinal_when_fold_is_own_section():
    section = {
        "map_name": "第1个【梦境迷钟】修复解密",
        "texts": ["将1黄色模块移到左下"],
        "observations": [],
    }
    units = build_guide_units([section])
    assert len(units) == 1
    assert units[0]["article_ordinal"] == 1


def test_unresolved_batch_without_anchors():
    section = {
        "map_name": "海原市",
        "observations": [{"block_id": f"p{i}", "role": "puzzle_step"} for i in range(6)],
    }
    units = build_guide_units([section])
    assert len(units) == 1
    assert units[0]["status"] == "UNRESOLVED_REGION_BATCH"

def test_page_anchor_binds_units_the_page_itself_names():
    """A per-map guide labels its pictures "1号小鸟"; the page names the map."""
    from hsrmap.guides.regions.resolver import apply_page_anchor, resolve_page_map

    maps = [
        {"map_id": "150", "name": "筑梦边境", "path": "匹诺康尼 / 筑梦边境"},
        {"map_id": "255", "name": "匹诺康尼大剧院", "path": "匹诺康尼 / 大剧院"},
        {"map_id": "147", "name": "1层", "path": "匹诺康尼 / 朝露公馆 / 1层"},
        {"map_id": "26", "name": "朝露公馆", "path": "匹诺康尼 / 朝露公馆"},
    ]
    title = "【崩坏：星穹铁道】V2.0攻略 | 筑梦边境地图折纸小鸟全收集"
    anchor = resolve_page_map(title, ["1号小鸟", "2号小鸟"], maps)
    assert anchor["status"] == "PAGE_ANCHOR" and anchor["map_id"] == "150"

    sections = [{"map_name": "1号小鸟", "observations": [{"resolved_map": {"status": "NO_MATCH"}}]}]
    anchored = apply_page_anchor(sections, title=title, headings=["1号小鸟"], maps=maps)
    assert anchored[0]["resolved_map"]["map_id"] == "150"
    assert anchored[0]["resolved_map"]["anchored_by"] == "page_title"
    assert anchored[0]["observations"][0]["resolved_map"]["map_id"] == "150"

    # a section whose own label is ambiguous takes the page anchor instead
    ambiguous = apply_page_anchor(
        [{"map_name": "《崩坏：星穹铁道》匹诺康尼大剧院-20只折纸鸟全收集", "resolved_map": {"status": "AMBIGUOUS"}}],
        title="《崩坏：星穹铁道》匹诺康尼大剧院-20只折纸鸟全收集",
        headings=[],
        maps=maps,
    )
    # either its own label resolves to that one map, or the page anchor does it
    assert ambiguous[0]["resolved_map"]["map_id"] == "255"
    assert ambiguous[0]["resolved_map"]["status"] in {"MATCH", "PAGE_ANCHOR"}

    # an article covering several maps is never anchored to one of them
    many = resolve_page_map("朝露公馆 / 筑梦边境 合集", [], maps)
    assert many["status"] == "NO_MATCH"
    # and a section the page *can* resolve keeps its own answer
    kept = apply_page_anchor(
        [{"map_name": "1层", "resolved_map": {"status": "NO_MATCH"}}], title=title, headings=[], maps=maps
    )
    assert kept[0]["resolved_map"]["map_id"] == "147"

