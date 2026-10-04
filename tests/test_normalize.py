"""Normalizer must flatten trees, version the CRS, and keep transform invariants."""

from hsrmap.normalize import (
    flatten_label_nodes,
    flatten_map_nodes,
    normalize_map_info,
    normalize_point,
)
from hsrmap.transform import TRANSFORM_VERSION, source_to_raster


def test_flatten_map_nodes_marks_type2_leaves_renderable_without_hardcoded_count():
    tree = [
        {
            "id": 502,
            "name": "二相乐园",
            "node_type": 1,
            "depth": 1,
            "parent_id": 0,
            "children": [
                {
                    "id": 842,
                    "name": "海原市",
                    "node_type": 2,
                    "depth": 3,
                    "parent_id": 841,
                    "children": [],
                }
            ],
        }
    ]
    nodes = flatten_map_nodes(tree)
    by_id = {n["source_id"]: n for n in nodes}
    assert len(nodes) == 2
    assert by_id["502"]["is_renderable"] is False
    assert by_id["842"]["is_renderable"] is True
    assert by_id["842"]["parent_source_id"] == "841"


def test_normalize_map_and_point_use_origin_translation_v1():
    payload = {
        "retcode": 0,
        "message": "OK",
        "data": {
            "info": {
                "id": 842,
                "name": "海原市",
                "detail": '{"slices":[[{"url":"https://example.test/a.png"}]],"origin":[3827,2217],"total_size":[8192,4096],"padding":[1706,219]}',
            }
        },
    }
    mapped = normalize_map_info(payload)
    assert mapped["source_id"] == "842"
    assert mapped["origin_x"] == 3827
    assert mapped["origin_y"] == 2217
    assert mapped["coordinate_transform"] == TRANSFORM_VERSION
    assert mapped["fragment_count"] == 1
    assert mapped["fragments"][0]["x"] == 0
    assert mapped["fragments"][0]["width"] == 8192

    point = normalize_point(
        {"id": 5171, "label_id": 686, "x_pos": 85.5, "y_pos": -81.5},
        map_source_id="842",
        origin_x=mapped["origin_x"],
        origin_y=mapped["origin_y"],
    )
    assert point["raster_x"] == 3912.5
    assert point["raster_y"] == 2135.5
    assert abs(point["raster_x"] - point["x_pos"] - mapped["origin_x"]) < 1e-9
    assert abs(point["raster_y"] - point["y_pos"] - mapped["origin_y"]) < 1e-9
    assert source_to_raster(85.5, -81.5, mapped["origin_x"], mapped["origin_y"]) == (3912.5, 2135.5)


def test_flatten_labels_resolves_semantic_names_not_ids():
    tree = [
        {
            "id": 658,
            "name": "解密战利品",
            "depth": 1,
            "parent_id": 0,
            "icon": "",
            "children": [
                {
                    "id": 777,
                    "name": "浮脂溯源·二次元ROTATE！",
                    "depth": 2,
                    "parent_id": 658,
                    "icon": "https://example.test/a.png",
                    "children": [],
                }
            ],
        }
    ]
    nodes, bindings = flatten_label_nodes(tree)
    assert any(n["source_id"] == "777" and n["is_selectable"] for n in nodes)
    assert bindings[0]["semantic_key"] == "floating_grease_origin_retrace"
    assert bindings[0]["source_label_id"] == "777"
    assert bindings[0]["resolver"] == "exact_normalized_name"
