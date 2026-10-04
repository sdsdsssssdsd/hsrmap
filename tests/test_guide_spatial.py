from io import BytesIO

from PIL import Image, ImageDraw

from hsrmap.guides.matching.inset import read_inset_anchor
from hsrmap.guides.matching.matcher import match_unit
from hsrmap.guides.matching.spatial import assign_map_units, parse_spatial_anchor, pick_by_anchor

TWOD = [
    {"source_point_id": "4620", "x": -844.425, "y": -269.448},
    {"source_point_id": "4618", "x": -1081.910, "y": -618.988},
    {"source_point_id": "4608", "x": -286.409, "y": -1058.923},
    {"source_point_id": "4592", "x": 902.181, "y": -171.989},
]


def test_parse_corner_from_qiyuan_title():
    assert parse_spatial_anchor("3号点位：【二维市】左下角") == "左下角"
    assert parse_spatial_anchor("1号点位：【二维市】右下角") == "右下角"
    assert parse_spatial_anchor("2号点位：【空飨妖都】左侧") == "左侧"


def test_2d_city_corners_follow_image_y_down():
    assert pick_by_anchor("右下角", TWOD) == "4592"
    assert pick_by_anchor("右上角", TWOD) == "4608"
    assert pick_by_anchor("左下角", TWOD) == "4620"
    assert pick_by_anchor("左上角", TWOD) == "4618"


def test_match_unit_uses_image_anchor_not_article_index():
    bind = match_unit({"map_name": "二维市", "article_ordinal": 3, "spatial_anchor": "左下角"}, TWOD)
    assert bind["source_point_id"] == "4620"
    assert bind["status"] == "auto"


def test_match_unit_ignores_puzzle_step_corners():
    bind = match_unit(
        {
            "map_name": "二维市",
            "spatial_anchor": "右下角",
            "steps": [{"text": "跳到中间平台的右上角"}],
        },
        TWOD,
    )
    assert bind["source_point_id"] == "4592"


HAIYUAN = [
    {"source_point_id": "5171", "x": 85.5, "y": -81.5},
    {"source_point_id": "5170", "x": -182.5, "y": 238.0},
    {"source_point_id": "5169", "x": -858.5, "y": -1.5},
]


def test_haiyuan_left_mid_right():
    assert pick_by_anchor("左边", HAIYUAN) == "5169"
    assert pick_by_anchor("中间", HAIYUAN) == "5170"
    assert pick_by_anchor("右边", HAIYUAN) == "5171"


def test_assign_worlds_end_uses_floor_after_spatial():
    points = [
        {"source_point_id": "4894", "map_name": "1层", "x": 132.1, "y": 357.0},
        {"source_point_id": "4884", "map_name": "2层", "x": 424.1, "y": -921.8},
        {"source_point_id": "4876", "map_name": "2层", "x": -578.6, "y": 967.1},
    ]
    units = [
        {"spatial_anchor": None, "floor_label": 2},
        {"spatial_anchor": "中部"},
        {"spatial_anchor": "上面"},
    ]
    assert assign_map_units(units, points) == {0: "4876", 1: "4894", 2: "4884"}


def test_inset_pin_reads_visual_corner():
    image = Image.new("RGB", (400, 280), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 36, 188, 188), fill=(236, 236, 236))
    draw.polygon([(36, 56), (168, 56), (168, 168), (36, 168)], fill=(48, 48, 48))
    draw.ellipse((142, 142, 164, 164), fill=(255, 208, 48))
    blob = BytesIO()
    image.save(blob, format="PNG")
    assert read_inset_anchor(blob.getvalue()) == "右下角"
