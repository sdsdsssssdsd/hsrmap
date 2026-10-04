"""Stale RUNNING jobs are closed instead of reporting phantom work."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.jobs import (
    complete_job,
    create_job,
    fail_stale_jobs,
    get_job,
    start_job,
)


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def test_a_running_job_with_no_process_is_closed(tmp_path):
    db = _db(tmp_path)
    stale = create_job(db, job_type="corpus", topic="nymph", budget={})
    start_job(db, int(stale["id"]))
    assert get_job(db, int(stale["id"]))["state"] == "RUNNING"

    closed = fail_stale_jobs(db)
    assert closed == [int(stale["id"])]
    job = get_job(db, int(stale["id"]))
    assert job["state"] == "FAILED"
    assert "no live process" in str(job.get("error") or "")
    db.close()


def test_a_finished_job_is_left_alone_and_the_current_one_is_kept(tmp_path):
    db = _db(tmp_path)
    done = create_job(db, job_type="corpus", topic="nymph", budget={})
    start_job(db, int(done["id"]))
    complete_job(db, int(done["id"]))
    current = create_job(db, job_type="corpus", topic="nymph", budget={})
    start_job(db, int(current["id"]))

    # the job that is starting right now is not stale
    assert fail_stale_jobs(db, keep=int(current["id"])) == []
    assert get_job(db, int(current["id"]))["state"] == "RUNNING"
    assert get_job(db, int(done["id"]))["state"] == "COMPLETED"
    db.close()
