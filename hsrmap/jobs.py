from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    job_type: str
    resource_id: str
    state: str = "PENDING"
    attempts: int = 0
    last_error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    response_path: str | None = None
    response_sha256: str | None = None


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("jobs", []):
                job = Job(**item)
                self._jobs[self._key(job.job_type, job.resource_id)] = job

    def _key(self, job_type: str, resource_id: str) -> str:
        return f"{job_type}:{resource_id}"

    def save(self) -> None:
        with self._lock:
            payload = {"jobs": [asdict(job) for job in self._jobs.values()]}
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.path.exists():
                self.path.unlink()
            tmp.rename(self.path)

    def close(self) -> None:
        self.save()

    def enqueue(self, job_type: str, resource_id: str, persist: bool = True) -> Job:
        key = self._key(job_type, resource_id)
        if key not in self._jobs:
            self._jobs[key] = Job(job_type=job_type, resource_id=str(resource_id))
            if persist:
                self.save()
        return self._jobs[key]

    def get(self, job_type: str, resource_id: str) -> Job:
        return self._jobs[self._key(job_type, resource_id)]

    def mark_running(self, job_type: str, resource_id: str) -> None:
        job = self.get(job_type, resource_id)
        job.state = "RUNNING"
        job.started_at = _now()
        job.attempts += 1
        self.save()

    def mark_success(self, job_type: str, resource_id: str, response_path: str, response_sha256: str) -> None:
        job = self.get(job_type, resource_id)
        job.state = "SUCCESS"
        job.finished_at = _now()
        job.response_path = response_path
        job.response_sha256 = response_sha256
        job.last_error = None
        self.save()

    def mark_failed(self, job_type: str, resource_id: str, error: str) -> None:
        job = self.get(job_type, resource_id)
        job.state = "FAILED"
        job.finished_at = _now()
        job.last_error = error
        self.save()

    def reset_running_to_pending(self) -> None:
        for job in self._jobs.values():
            if job.state == "RUNNING":
                job.state = "PENDING"
        self.save()

    def pending(self, job_type: str | None = None) -> list[Job]:
        jobs = [j for j in self._jobs.values() if j.state in {"PENDING", "FAILED"}]
        if job_type:
            jobs = [j for j in jobs if j.job_type == job_type]
        return jobs

    def pending_count(self) -> int:
        return len(self.pending())

    def success_ids(self, job_type: str) -> list[str]:
        return [j.resource_id for j in self._jobs.values() if j.job_type == job_type and j.state == "SUCCESS"]
