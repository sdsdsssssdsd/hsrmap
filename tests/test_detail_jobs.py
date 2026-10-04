"""FETCHING detail jobs resume as PENDING; empty success is a terminal state."""

from pathlib import Path

from hsrmap.detail_db import DetailDatabase


def test_fetching_resumes_as_pending(tmp_path: Path):
    db = DetailDatabase(tmp_path / "detail.staging.db")
    db.upsert_request(
        {
            "request_key": "abc",
            "endpoint_name": "point_info",
            "parameters_json": "{}",
            "state": "FETCHING",
            "attempts": 1,
        }
    )
    db.reset_in_flight()
    row = db.get_request("abc")
    assert row["state"] == "PENDING"
    db.close()


def test_complete_empty_is_terminal(tmp_path: Path):
    db = DetailDatabase(tmp_path / "detail.staging.db")
    db.upsert_request(
        {
            "request_key": "empty",
            "endpoint_name": "point_info",
            "parameters_json": "{}",
            "state": "COMPLETE_EMPTY",
        }
    )
    assert db.pending_keys() == []
    db.close()
