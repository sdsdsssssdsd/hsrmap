"""Detail statistics must join core.db and detail.db, never query points from detail.db."""

from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.detail_db import DetailDatabase
from hsrmap.detail_enrich import build_detail_statistics
from hsrmap.detail_normalize import normalize_point_info
from hsrmap.detail_queue import point_info_request_key


def test_statistics_do_not_read_points_from_detail_db(tmp_path: Path):
    core = CoreDatabase(tmp_path / "core.db")
    core.conn.execute("INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, is_renderable) VALUES ('1', NULL, 2, 'm', 1)")
    core.conn.execute("INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count) VALUES ('1','m',10,10,1,1,1)")
    core.conn.execute("INSERT INTO label_nodes(source_id, name, is_category, is_selectable, sort_order) VALUES ('9', 'x', 0, 1, 0)")
    core.conn.execute("INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('99', 1, 0, 0, 1, 1)")
    core.conn.execute("INSERT INTO point_labels(point_id, label_id) VALUES (1, 1)")
    core.conn.commit()
    detail = DetailDatabase(tmp_path / "detail.db")
    key = point_info_request_key("99", "av")
    detail.upsert_request({"request_key": key, "endpoint_name": "point_info", "parameters": {"point_id": "99"}, "state": "COMPLETE"})
    parsed = normalize_point_info({"retcode": 0, "data": {"info": {"id": 99, "content": "hi", "img": "", "url_list": []}}})
    detail_id = detail.save_detail(key, parsed, "aa")
    detail.bind_core_point(1, detail_id, "99", "1")
    stats = build_detail_statistics(core, detail)
    assert stats["nonempty_details"] == 1
    assert stats["by_label"][0]["core_point_count"] == 1
    assert stats["by_label"][0]["with_text"] == 1
    core.close()
    detail.close()
