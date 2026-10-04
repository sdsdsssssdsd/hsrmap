from hsrmap.guides.matching.candidates import query_candidates


def test_only_current_map_grease_points():
    points = [
        {"source_point_id": "a", "map_name": "海原市", "label": "浮脂溯源"},
        {"source_point_id": "b", "map_name": "海原市", "label": "浮脂溯源"},
        {"source_point_id": "c", "map_name": "海原市", "label": "浮脂溯源"},
        {"source_point_id": "x", "map_name": "海原电视塔", "label": "浮脂溯源"},
        {"source_point_id": "z", "map_name": "海原市", "label": "宝箱"},
    ]
    hits = query_candidates("海原市", points)
    assert {row["source_point_id"] for row in hits} == {"a", "b", "c"}


def test_tower_region_matches_floor_points_via_path():
    points = [
        {
            "source_point_id": "5196",
            "map_name": "1层",
            "map_path": "二相乐园 / 海原电视塔 / 1层",
            "label": "浮脂溯源",
        },
        {
            "source_point_id": "5171",
            "map_name": "海原市",
            "map_path": "二相乐园 / 海原市 / 海原市",
            "label": "浮脂溯源",
        },
    ]
    hits = query_candidates("海原电视塔", points)
    assert {row["source_point_id"] for row in hits} == {"5196"}


def test_topic_label_names_filter_dream_ticker():
    points = [
        {"source_point_id": "1", "map_name": "黄金的时刻", "label": "梦境迷钟"},
        {"source_point_id": "2", "map_name": "黄金的时刻", "label": "浮脂溯源"},
    ]
    hits = query_candidates("黄金的时刻", points, semantic=None, label_names=["梦境迷钟"])
    assert {row["source_point_id"] for row in hits} == {"1"}


def test_floor_region_heading_matches_same_floor_leaf():
    points = [
        {
            "source_point_id": "1709",
            "map_name": "1层",
            "map_path": "匹诺康尼 / 「白日梦」酒店-梦境 / 1层",
            "label": "梦境迷钟",
        },
        {
            "source_point_id": "1710",
            "map_name": "2层",
            "map_path": "匹诺康尼 / 「白日梦」酒店-梦境 / 2层",
            "label": "梦境迷钟",
        },
    ]
    hits = query_candidates("1层区域", points, semantic=None, label_names=["梦境迷钟"])
    assert {row["source_point_id"] for row in hits} == {"1709"}

#: 正文自己说区域 + 楼层时的绑定（标题里没有区域名也能绑）。
GOATS = [
    {
        "source_point_id": "3811",
        "label": "黄金替罪羊",
        "map_name": "2层",
        "map_path": "翁法罗斯 / 「穹顶关塞」晨昏之眼 / 2层",
        "region": "「穹顶关塞」晨昏之眼",
    },
    {
        "source_point_id": "3855",
        "label": "黄金替罪羊",
        "map_name": "1层",
        "map_path": "翁法罗斯 / 「穹顶关塞」晨昏之眼 / 1层",
        "region": "「穹顶关塞」晨昏之眼",
    },
    {
        "source_point_id": "3868",
        "label": "黄金替罪羊",
        "map_name": "-2层",
        "map_path": "翁法罗斯 / 「穹顶关塞」晨昏之眼 / -2层",
        "region": "「穹顶关塞」晨昏之眼",
    },
]


def test_region_plus_floor_in_the_body_binds_the_only_point_that_fits():
    from hsrmap.guides.matching.candidates import query_candidates_by_text

    got = query_candidates_by_text(
        "随后我们来到穹顶关塞二层的左侧位置处，找到第一个黄金替罪羊",
        GOATS,
        label_names=["黄金替罪羊"],
    )
    assert [row["source_point_id"] for row in got] == ["3811"]
    # 「负二层」折叠成「-2层」，不会命中 2 层
    got = query_candidates_by_text(
        "随后我们来到穹顶关塞，晨昏之眼右侧，负二层的位置",
        GOATS,
        label_names=["黄金替罪羊"],
    )
    assert [row["source_point_id"] for row in got] == ["3868"]


def test_a_region_name_alone_does_not_bind_anything():
    from hsrmap.guides.matching.candidates import query_candidates_by_text

    # 只说了区域（三个点位都在这个区域）→ 谁都不绑
    assert query_candidates_by_text("穹顶关塞的黄金替罪羊怎么解", GOATS, label_names=["黄金替罪羊"]) == []
    # 只说了楼层、没说区域 → 也不绑
    assert query_candidates_by_text("二层左侧有一个黄金替罪羊", GOATS, label_names=["黄金替罪羊"]) == []


def test_a_day_night_qualifier_must_land_on_the_winner():
    from hsrmap.guides.matching.candidates import query_candidates_by_text

    points = [
        {
            "source_point_id": "416",
            "label": "黄金替罪羊",
            "map_name": "1层",
            "map_path": "翁法罗斯 / 「龙骸古城」斯缇科西亚 / 1层",
            "region": "「龙骸古城」斯缇科西亚",
        },
        {
            "source_point_id": "424",
            "label": "黄金替罪羊",
            "map_name": "「龙骸古城」斯缇科西亚-1层房间（永夜）",
            "map_path": "翁法罗斯 / 「龙骸古城」斯缇科西亚-1层房间（永夜） / 「龙骸古城」斯缇科西亚-1层房间（永夜）",
            "region": "「龙骸古城」斯缇科西亚-1层房间（永夜）",
        },
    ]
    # 正文说了「永夜」，胜出的 416 路径里没有 → 宁可返回空，也不猜
    assert query_candidates_by_text(
        "地图西南方1层古城市集永夜时刻的那位黄金替罪羊", points, label_names=["黄金替罪羊"]
    ) == []


def test_the_label_filter_still_applies_to_text_binding():
    from hsrmap.guides.matching.candidates import query_candidates_by_text

    points = [
        {**GOATS[0], "label": "宝箱"},
        {**GOATS[1], "label": "黄金替罪羊"},
    ]
    got = query_candidates_by_text("穹顶关塞2层的黄金替罪羊", points, label_names=["黄金替罪羊"])
    assert got == []

