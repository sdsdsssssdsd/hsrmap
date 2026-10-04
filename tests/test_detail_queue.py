"""Detail request identity follows registry params: point_id, not map_id."""

from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.detail_queue import build_detail_queue, point_info_request_key, source_ids_for_label_key


APP_VERSION = "de16a09fca4e0ab89acf69fe0c12514f"


def test_request_key_ignores_map_id_and_canonicalizes_point_id():
    a = point_info_request_key(5171, APP_VERSION)
    b = point_info_request_key("5171", APP_VERSION)
    c = point_info_request_key(5171, "other")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_duplicate_source_point_ids_share_one_request(tmp_path: Path):
    db = CoreDatabase(tmp_path / "core.db")
    db.conn.executemany(
        "INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable) VALUES (?,?,?,?,?,?,?)",
        [("10", None, 1, "root", 0, 0, 0), ("11", "10", 2, "A", 1, 0, 1), ("12", "10", 2, "B", 1, 1, 1)],
    )
    db.conn.execute(
        "INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count) VALUES ('11','A',10,10,1,1,1)"
    )
    db.conn.execute(
        "INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count) VALUES ('12','B',10,10,1,1,1)"
    )
    db.conn.execute(
        "INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('99', 1, 0, 0, 1, 1)"
    )
    db.conn.execute(
        "INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('99', 2, 2, 2, 3, 3)"
    )
    db.conn.commit()

    queue = build_detail_queue(db, APP_VERSION)
    assert queue["core_point_count"] == 2
    assert queue["unique_source_point_ids"] == 1
    assert queue["unique_request_count"] == 1
    assert queue["duplicate_source_point_ids"] == 1
    assert len(queue["requests"]) == 1
    assert len(queue["bindings"]) == 2
    db.close()


def test_label_name_finds_points_without_semantic_binding(tmp_path: Path):
    db = CoreDatabase(tmp_path / "core.db")
    db.conn.execute(
        "INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable) VALUES (?,?,?,?,?,?,?)",
        ("10", None, 1, "root", 0, 0, 0),
    )
    db.conn.execute(
        "INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count) VALUES ('10','root',10,10,1,1,1)"
    )
    db.conn.execute(
        "INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('201', 1, 0, 0, 1, 1)"
    )
    db.conn.execute(
        "INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('202', 1, 2, 2, 3, 3)"
    )
    db.conn.execute(
        "INSERT INTO label_nodes(source_id, name, is_selectable) VALUES ('668', '梦境迷钟', 1)"
    )
    db.conn.execute(
        "INSERT INTO label_nodes(source_id, name, is_selectable) VALUES ('452', '梦境迷钟', 1)"
    )
    db.conn.execute("INSERT INTO point_labels(point_id, label_id) VALUES (1, 1)")
    db.conn.execute("INSERT INTO point_labels(point_id, label_id) VALUES (2, 2)")
    db.conn.commit()
    assert source_ids_for_label_key(db, "梦境迷钟") == {"201", "202"}
    db.close()
