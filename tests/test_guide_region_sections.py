from hsrmap.guides.regions.sections import build_region_sections


def test_two_regions_drop_ad():
    blocks = [
        {"type": "heading", "text": "4.2版本", "id": "h0"},
        {"type": "image", "id": "b1"},
        {"type": "image", "id": "b2"},
        {"type": "image", "id": "b3"},
        {"type": "image", "id": "b4"},
        {"type": "image", "id": "b5"},
        {"type": "image", "id": "ad"},
    ]
    observations = [
        {"block_id": "b1", "role": "location_map", "map_name_raw": "海原市", "resolved_map": {"map_name": "海原市"}},
        {"block_id": "b2", "role": "puzzle_step", "map_name_raw": "海原市", "resolved_map": {"map_name": "海原市"}},
        {"block_id": "b3", "role": "puzzle_step", "map_name_raw": "海原市", "resolved_map": {"map_name": "海原市"}},
        {"block_id": "b4", "role": "location_map", "map_name_raw": "海原电视塔", "resolved_map": {"map_name": "海原电视塔"}},
        {"block_id": "b5", "role": "puzzle_step", "map_name_raw": "海原电视塔", "resolved_map": {"map_name": "海原电视塔"}},
        {"block_id": "ad", "role": "advertisement", "map_name_raw": None},
    ]
    sections = build_region_sections(blocks, observations)
    assert [item["map_name"] for item in sections] == ["海原市", "海原电视塔"]
    assert "ad" not in {bid for section in sections for bid in section["block_ids"]}


def test_heading_strips_count_suffix_for_any_map():
    blocks = [
        {"type": "heading", "text": "黄金的时刻（4个）", "id": "h1"},
        {"type": "paragraph", "text": "第1个【梦境迷钟】修复解密", "id": "p1"},
    ]
    sections = build_region_sections(blocks, [])
    assert sections[0]["map_name"] == "黄金的时刻"


def test_point_headings_stay_under_floor_region():
    blocks = [
        {"type": "heading", "text": "1层区域", "id": "h1"},
        {"type": "heading", "text": "点位3：（解谜）", "id": "h2"},
        {"type": "paragraph", "text": "【梦境迷钟】解谜宝箱", "id": "p1"},
        {"type": "heading", "text": "点位4：（次元扑满）", "id": "h3"},
        {"type": "paragraph", "text": "位置在迷宫花园中央", "id": "p2"},
    ]
    sections = build_region_sections(blocks, [])
    assert [item["map_name"] for item in sections] == ["1层区域"]
    texts = sections[0].get("texts") or []
    assert "点位3：（解谜）" in texts
    assert "【梦境迷钟】解谜宝箱" in texts


def test_ordinal_fold_headings_stay_under_region():
    blocks = [
        {"type": "heading", "text": "黄金的时刻（4个）", "id": "h1"},
        {"type": "heading", "text": "第1个【梦境迷钟】修复解密", "id": "h2"},
        {"type": "paragraph", "text": "将黄色模块移到左下", "id": "p1"},
        {"type": "heading", "text": "第2个【梦境迷钟】修复解密", "id": "h3"},
        {"type": "paragraph", "text": "将镜子往右下移动", "id": "p2"},
    ]
    sections = build_region_sections(blocks, [])
    assert sections[0]["map_name"] == "黄金的时刻"
    from hsrmap.guides.regions.units import build_guide_units

    units = build_guide_units(sections)
    assert [item["article_ordinal"] for item in units] == [1, 2]
    assert all(item["map_name"] == "黄金的时刻" for item in units)

POINTS = [
    {"source_point_id": "2954", "region": "「永恒圣城」奥赫玛", "map_name": "1层", "map_path": "翁法罗斯 / 「永恒圣城」奥赫玛 / 1层"},
    {"source_point_id": "4416", "region": "「灾梦余温」无名泰坦大墓", "map_name": "1层", "map_path": "翁法罗斯 / 「灾梦余温」无名泰坦大墓 / 1层"},
    {"source_point_id": "4355", "region": "「全世矩阵」无名泰坦大墓", "map_name": "1层", "map_path": "翁法罗斯 / 「全世矩阵」无名泰坦大墓 / 1层"},
    {"source_point_id": "3043", "region": "「纷争荒墟」悬锋城", "map_name": "1层", "map_path": "翁法罗斯 / 「纷争荒墟」悬锋城 / 1层"},
]


def test_a_region_line_in_the_body_starts_a_section():
    """九游/游侠把区域名写成普通段落：「永恒圣城奥赫玛」这一行就是边界。"""
    from hsrmap.guides.regions.sections import region_aliases

    blocks = [
        {"type": "heading", "text": "3.0版本黄金替罪羊解谜攻略", "id": "h0"},
        {"type": "paragraph", "text": "玩法解析：不能碰到黑暗羊", "id": "p0"},
        {"type": "paragraph", "text": "永恒圣城奥赫玛", "id": "p1"},
        {"type": "paragraph", "text": "1、集市左侧解谜", "id": "p2"},
        {"type": "paragraph", "text": "右2步，左1步", "id": "p3"},
        {"type": "paragraph", "text": "「纷争荒墟」悬锋城", "id": "p4"},
        {"type": "paragraph", "text": "8、入口处往上走的解谜", "id": "p5"},
    ]
    sections = build_region_sections(
        blocks, [], title="3.0版本黄金替罪羊解谜攻略", regions=region_aliases(POINTS)
    )
    names = [item["map_name"] for item in sections]
    assert names == ["3.0版本黄金替罪羊解谜攻略", "「永恒圣城」奥赫玛", "「纷争荒墟」悬锋城"]
    #: 区域名那一行本身留在这一节里（九游的「奥赫玛：4个」这种计数行不能被丢掉）
    assert sections[1]["texts"][0] == "永恒圣城奥赫玛"
    assert "1、集市左侧解谜" in sections[1]["texts"]


def test_a_shared_tail_is_not_a_boundary():
    """「无名泰坦大墓」同时属于两个区域：命中不唯一就不切，宁可整页留着。"""
    from hsrmap.guides.regions.sections import region_aliases

    blocks = [
        {"type": "heading", "text": "3.7黄金替罪羊", "id": "h0"},
        {"type": "paragraph", "text": "无名泰坦大墓", "id": "p1"},
        {"type": "paragraph", "text": "第1个：右右左右", "id": "p2"},
    ]
    sections = build_region_sections(blocks, [], title="3.7黄金替罪羊", regions=region_aliases(POINTS))
    assert [item["map_name"] for item in sections] == ["3.7黄金替罪羊"]
    assert "无名泰坦大墓" in sections[0]["texts"]


def test_text_before_any_heading_is_not_dropped():
    """标题带「3.0版本」会被版本守卫跳过，正文不能因此整段消失。"""
    blocks = [
        {"type": "heading", "text": "崩坏星穹铁道3.0版本黄金替罪羊解谜攻略", "id": "h0"},
        {"type": "paragraph", "text": "1、集市左侧解谜", "id": "p1"},
        {"type": "paragraph", "text": "右2步", "id": "p2"},
    ]
    sections = build_region_sections(blocks, [], title="崩坏星穹铁道3.0版本黄金替罪羊解谜攻略")
    assert [item["map_name"] for item in sections] == ["崩坏星穹铁道3.0版本黄金替罪羊解谜攻略"]
    assert [text for text in sections[0]["texts"]] == ["1、集市左侧解谜", "右2步"]