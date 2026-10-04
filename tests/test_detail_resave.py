"""Re-saving a detail must not delete rows that bindings still reference."""

from pathlib import Path

from hsrmap.detail_db import DetailDatabase
from hsrmap.detail_normalize import normalize_point_info
from hsrmap.detail_queue import point_info_request_key


def test_save_detail_twice_keeps_binding(tmp_path: Path):
    db = DetailDatabase(tmp_path / "detail.db")
    key = point_info_request_key("99", "av")
    db.upsert_request({"request_key": key, "endpoint_name": "point_info", "parameters": {"point_id": "99"}, "state": "COMPLETE"})
    parsed = normalize_point_info(
        {"retcode": 0, "data": {"info": {"id": 99, "content": "hi", "img": "https://example.test/a.png", "url_list": []}}}
    )
    first = db.save_detail(key, parsed, "aa")
    db.bind_core_point(1, first, "99", "1")
    parsed["plain_text"] = "hello again"
    second = db.save_detail(key, parsed, "bb")
    assert second == first
    row = db.detail_by_source("99")
    assert row["plain_text"] == "hello again"
    assert db.conn.execute("SELECT COUNT(*) n FROM point_detail_bindings").fetchone()["n"] == 1
    db.close()
