"""Crashed RUNNING jobs must become PENDING on resume, SUCCESS jobs stay done."""

from pathlib import Path

from hsrmap.jobs import JobStore


def test_resume_resets_running_and_keeps_success(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.json")
    store.enqueue("map_info", "842")
    store.enqueue("map_info", "1")
    store.mark_running("map_info", "842")
    store.mark_success("map_info", "1", response_path="raw/1.json", response_sha256="aa")

    store.close()
    resumed = JobStore(tmp_path / "jobs.json")
    resumed.reset_running_to_pending()

    assert resumed.get("map_info", "842").state == "PENDING"
    assert resumed.get("map_info", "1").state == "SUCCESS"
    assert resumed.pending_count() == 1
