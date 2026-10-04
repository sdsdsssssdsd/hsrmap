"""Minimal job model for long crawls (a1-6 §十六/§十七/§十八, §28).

A corpus run is a job: it has a state, a cursor, a hard budget, counters and a
report. Stopping is normal — the budget says when — and resuming is safe because
every step is idempotent: canonical URLs, asset SHA256, article families and
review signatures are all unique keys.

```text
PENDING -> RUNNING -> COMPLETED
                     -> PAUSED   (budget reached, resume later)
                     -> FAILED   (too many failures, or an exception)
```
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from hsrmap.guide_db import GuideDatabase

JOB_STATES = ("PENDING", "RUNNING", "PAUSED", "FAILED", "COMPLETED")

#: Budgets are hard stops, not hints (a1-6 §十六).
DEFAULT_BUDGET: dict[str, Any] = {
    "max_pages": 30,
    "max_assets": 500,
    "max_bytes": 256 * 1024 * 1024,
    "max_runtime": 1800.0,
    "max_failures": 5,
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


@dataclass(frozen=True)
class JobBudget:
    """Hard limits for one job; any of them stops the run cleanly."""

    max_pages: int = int(DEFAULT_BUDGET["max_pages"])
    max_assets: int = int(DEFAULT_BUDGET["max_assets"])
    max_bytes: int = int(DEFAULT_BUDGET["max_bytes"])
    max_runtime: float = float(DEFAULT_BUDGET["max_runtime"])
    max_failures: int = int(DEFAULT_BUDGET["max_failures"])

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "JobBudget":
        data = {**DEFAULT_BUDGET, **{k: v for k, v in (values or {}).items() if v is not None}}
        return cls(
            max_pages=int(data["max_pages"]),
            max_assets=int(data["max_assets"]),
            max_bytes=int(data["max_bytes"]),
            max_runtime=float(data["max_runtime"]),
            max_failures=int(data["max_failures"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_pages": self.max_pages,
            "max_assets": self.max_assets,
            "max_bytes": self.max_bytes,
            "max_runtime": self.max_runtime,
            "max_failures": self.max_failures,
        }

    def exceeded(self, counters: dict[str, Any], elapsed: float) -> str | None:
        """The first limit that is used up, with the reason to record."""
        if counters.get("failures", 0) >= self.max_failures:
            return f"budget:max_failures({self.max_failures})"
        if counters.get("pages_imported", 0) >= self.max_pages:
            return f"budget:max_pages({self.max_pages})"
        if counters.get("assets_fetched", 0) >= self.max_assets:
            return f"budget:max_assets({self.max_assets})"
        if counters.get("bytes", 0) >= self.max_bytes:
            return f"budget:max_bytes({self.max_bytes})"
        if elapsed >= self.max_runtime:
            return f"budget:max_runtime({self.max_runtime:g}s)"
        return None


def create_job(
    db: GuideDatabase,
    *,
    job_type: str = "corpus",
    topic: str = "",
    cursor: dict[str, Any] | None = None,
    budget: dict[str, Any] | JobBudget | None = None,
) -> dict[str, Any]:
    engine = budget if isinstance(budget, JobBudget) else JobBudget.from_mapping(budget)
    now = _now()
    cursor_data = cursor or {}
    cursor = db.conn.execute(
        """
        INSERT INTO guide_job(
            job_type, topic, state, cursor_json, budget_json, counters_json,
            completed_json, failed_json, started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(job_type),
            str(topic),
            "PENDING",
            json.dumps(cursor_data, ensure_ascii=False),
            json.dumps(engine.as_dict(), ensure_ascii=False),
            json.dumps({}, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            now,
            now,
        ),
    )
    db.conn.commit()
    return get_job(db, int(cursor.lastrowid))


def get_job(db: GuideDatabase, job_id: int) -> dict[str, Any]:
    row = db.conn.execute("SELECT * FROM guide_job WHERE id = ?", (int(job_id),)).fetchone()
    if row is None:
        raise KeyError(f"job not found: {job_id}")
    job = dict(row)
    job["cursor"] = _json(job.pop("cursor_json", None), {})
    job["budget"] = _json(job.pop("budget_json", None), {})
    job["counters"] = _json(job.pop("counters_json", None), {})
    job["completed"] = _json(job.pop("completed_json", None), [])
    job["failed"] = _json(job.pop("failed_json", None), [])
    job["job_id"] = int(job["id"])
    return job


def list_jobs(
    db: GuideDatabase,
    *,
    topic: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    sql = ["SELECT * FROM guide_job WHERE 1 = 1"]
    params: list[Any] = []
    if topic:
        sql.append("AND topic = ?")
        params.append(str(topic))
    if state:
        sql.append("AND state = ?")
        params.append(str(state))
    sql.append("ORDER BY id DESC LIMIT ?")
    params.append(int(limit))
    return [get_job(db, int(row["id"])) for row in db.conn.execute(" ".join(sql), tuple(params))]


def update_job(db: GuideDatabase, job_id: int, **fields: Any) -> dict[str, Any]:
    allowed = {
        "state",
        "error",
        "report_path",
        "cursor_json",
        "counters_json",
        "completed_json",
        "failed_json",
        "budget_json",
    }
    sets: list[str] = []
    params: list[Any] = []
    for name, value in fields.items():
        if name not in allowed:
            continue
        if name in {"state", "error", "report_path"}:
            sets.append(f"{name} = ?")
            params.append(value)
        else:
            sets.append(f"{name} = ?")
            params.append(json.dumps(value, ensure_ascii=False))
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(int(job_id))
    db.conn.execute(f"UPDATE guide_job SET {', '.join(sets)} WHERE id = ?", tuple(params))
    db.conn.commit()
    return get_job(db, job_id)


def start_job(db: GuideDatabase, job_id: int) -> dict[str, Any]:
    return update_job(db, job_id, state="RUNNING", error="")


def pause_job(db: GuideDatabase, job_id: int, reason: str = "") -> dict[str, Any]:
    return update_job(db, job_id, state="PAUSED", error=str(reason))


def fail_job(db: GuideDatabase, job_id: int, error: str = "") -> dict[str, Any]:
    return update_job(db, job_id, state="FAILED", error=str(error))


def complete_job(db: GuideDatabase, job_id: int) -> dict[str, Any]:
    return update_job(db, job_id, state="COMPLETED")


def fail_stale_jobs(db: GuideDatabase, *, keep: int | None = None) -> list[int]:
    """A job still marked RUNNING whose process is gone can never finish.

    A crash (or a killed process) leaves the row in RUNNING, and every later
    job-list then reports work that is not happening. The next corpus run closes
    those rows as FAILED, so RUNNING keeps meaning "running right now".
    """
    sql = "SELECT id FROM guide_job WHERE state = 'RUNNING'"
    params: tuple[Any, ...] = ()
    if keep is not None:
        sql += " AND id <> ?"
        params = (int(keep),)
    stale = [int(row["id"]) for row in db.conn.execute(sql, params)]
    for job_id in stale:
        fail_job(db, job_id, "interrupted: no live process owns this job (crash or killed)")
    return stale


def job_status(db: GuideDatabase, job_id: int) -> dict[str, Any]:
    """One job with its counters and remaining work."""
    job = get_job(db, job_id)
    cursor = job.get("cursor") or {}
    urls = list(cursor.get("urls") or [])
    done = set(job.get("completed") or []) | set(job.get("failed") or [])
    return {
        **job,
        "urls": len(urls),
        "remaining": len([url for url in urls if url not in done]),
        "budget": job.get("budget") or {},
    }


class JobTracker:
    """Counters, checkpoint and budget checks for one running job."""

    def __init__(self, db: GuideDatabase, job: dict[str, Any], *, clock=datetime.now) -> None:
        self.db = db
        self.job = job
        self.job_id = int(job["job_id"])
        self.budget = JobBudget.from_mapping(job.get("budget"))
        self.counters: dict[str, Any] = {
            "requests": 0,
            "bytes": 0,
            "hosts": {},
            "http": {},
            "retries": 0,
            "cache_hits": 0,
            "pages_discovered": 0,
            "pages_skipped": 0,
            "pages_imported": 0,
            "failures": 0,
            "assets_discovered": 0,
            "assets_fetched": 0,
            "assets_deduped": 0,
            "assets_failed": 0,
            "qa_pass": 0,
            "qa_fail": 0,
            "targets_matched": 0,
            "review_units": 0,
            **(job.get("counters") or {}),
        }
        self.completed: list[str] = list(job.get("completed") or [])
        self.failed: list[str] = list(job.get("failed") or [])
        #: Counters are cumulative for the report; the budget is checked against
        #: *this run* so resuming a paused job always makes progress.
        self.spent: dict[str, Any] = {
            "pages_imported": 0,
            "assets_fetched": 0,
            "bytes": 0,
            "failures": 0,
        }
        self._clock = clock
        self.started = datetime.now(timezone.utc).replace(microsecond=0)

    # -- resume -------------------------------------------------------- #

    def pending(self, urls: Iterable[str]) -> list[str]:
        done = set(self.completed) | set(self.failed)
        return [url for url in urls if url not in done]

    # -- accounting ---------------------------------------------------- #

    def note_host(self, url: str) -> None:
        host = (urlparse(str(url or "")).netloc or "").lower()
        if host:
            hosts = self.counters.setdefault("hosts", {})
            hosts[host] = int(hosts.get(host, 0)) + 1

    def note_assets(self, asset_log: Iterable[dict[str, Any]]) -> None:
        for item in asset_log:
            status = str(item.get("status") or "")
            size = int(item.get("byte_size") or 0)
            self.counters["assets_discovered"] += 1
            if item.get("cache_hit"):
                self.counters["cache_hits"] += 1
            if status in {"FETCHED", "CACHE_HIT"}:
                self.counters["bytes"] += size
                if status == "CACHE_HIT":
                    self.counters["assets_deduped"] += 1
                else:
                    self.counters["assets_fetched"] += 1
                    self.spent["assets_fetched"] += 1
                    self.spent["bytes"] += size
            else:
                self.counters["assets_failed"] += 1
            self.note_http(item.get("http_status"))
            self.counters["retries"] += max(0, len(item.get("attempts") or []) - 1)

    def note_http(self, status: Any) -> None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            return
        bucket = f"{code // 100}xx"
        http = self.counters.setdefault("http", {})
        http[bucket] = int(http.get(bucket, 0)) + 1

    def note_page(self, url: str, report: dict[str, Any]) -> None:
        status = str(report.get("status") or "")
        self.counters["pages_discovered"] += 1
        self.counters["requests"] += 1
        self.note_host(url)
        self.note_http(report.get("http_status"))
        assets = report.get("assets") or {}
        if assets.get("total"):
            self.counters["bytes"] += int(assets.get("bytes") or 0)
        if status in {"SKIPPED_ALREADY_IMPORTED", "SKIPPED_MIRROR"}:
            self.counters["pages_skipped"] += 1
        elif status == "IMPORTED":
            self.counters["pages_imported"] += 1
            self.counters["qa_pass"] += 1
            self.counters["review_units"] += len(report.get("review") or [])
            self.counters["targets_matched"] += 1 if report.get("review") else 0
            self.spent["pages_imported"] += 1
        elif status == "QA_FAIL":
            self.counters["qa_fail"] += 1
            self.counters["failures"] += 1
            self.spent["failures"] += 1
        else:
            self.counters["failures"] += 1
            self.spent["failures"] += 1
        if status in {"IMPORTED", "QA_FAIL"}:
            self.completed.append(url)
        elif status.startswith("SKIPPED"):
            self.completed.append(url)
        else:
            self.failed.append(url)

    # -- control ------------------------------------------------------- #

    def elapsed(self) -> float:
        """Wall clock since the job was first created (for the report)."""
        try:
            start = datetime.fromisoformat(self.job.get("started_at") or str(self.started))
            return max(0.0, (self._clock().astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return 0.0

    def run_elapsed(self) -> float:
        """Wall clock since this run started (for the budget)."""
        try:
            return max(
                0.0,
                (self._clock().astimezone(timezone.utc) - self.started.astimezone(timezone.utc)).total_seconds(),
            )
        except Exception:
            return 0.0

    def stop_reason(self) -> str | None:
        return self.budget.exceeded(self.spent, self.run_elapsed())

    def flush(self) -> dict[str, Any]:
        return update_job(
            self.db,
            self.job_id,
            counters_json=self.counters,
            completed_json=self.completed,
            failed_json=self.failed,
        )

    def finish(self, state: str, *, error: str = "", report_path: str = "") -> dict[str, Any]:
        update_job(
            self.db,
            self.job_id,
            state=state,
            error=error,
            report_path=report_path,
            counters_json=self.counters,
            completed_json=self.completed,
            failed_json=self.failed,
        )
        return job_status(self.db, self.job_id)


def build_report(
    job: dict[str, Any],
    counters: dict[str, Any],
    *,
    elapsed: float,
    results: list[dict[str, Any]],
    state: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The crawl report of a1-6 §十八, including the two yield ratios."""
    imported = int(counters.get("pages_imported") or 0)
    fetched = int(counters.get("requests") or 0)
    qa_pass = int(counters.get("qa_pass") or 0)
    targets = int(counters.get("targets_matched") or 0)
    return {
        "job_id": job.get("job_id") or job.get("id"),
        "job_type": job.get("job_type"),
        "topic": job.get("topic"),
        "state": state or job.get("state"),
        "elapsed_seconds": round(float(elapsed), 2),
        "requests": fetched,
        "bytes": int(counters.get("bytes") or 0),
        "hosts": dict(counters.get("hosts") or {}),
        "http": dict(counters.get("http") or {}),
        "retries": int(counters.get("retries") or 0),
        "cache_hits": int(counters.get("cache_hits") or 0),
        "pages": {
            "discovered": int(counters.get("pages_discovered") or 0),
            "skipped": int(counters.get("pages_skipped") or 0),
            "imported": imported,
            "failed": int(counters.get("failures") or 0),
        },
        "assets": {
            "discovered": int(counters.get("assets_discovered") or 0),
            "fetched": int(counters.get("assets_fetched") or 0),
            "deduped": int(counters.get("assets_deduped") or 0),
            "failed": int(counters.get("assets_failed") or 0),
        },
        "qa": {"pass": qa_pass, "fail": int(counters.get("qa_fail") or 0)},
        "targets_matched": targets,
        "review_units": int(counters.get("review_units") or 0),
        "yield": {
            "source_yield": round(qa_pass / fetched, 3) if fetched else 0.0,
            "target_yield": round(targets / fetched, 3) if fetched else 0.0,
        },
        "results": results,
        **(extra or {}),
    }


def render_markdown(report: dict[str, Any]) -> str:
    pages = report.get("pages") or {}
    assets = report.get("assets") or {}
    qa = report.get("qa") or {}
    yield_block = report.get("yield") or {}
    lines = [
        f"# Crawl report — job {report.get('job_id')} ({report.get('topic')})",
        "",
        f"- state: **{report.get('state')}**",
        f"- elapsed: {report.get('elapsed_seconds')}s",
        f"- requests: {report.get('requests')} · bytes: {report.get('bytes')}",
        f"- http: {report.get('http')}",
        f"- retries: {report.get('retries')} · cache hits: {report.get('cache_hits')}",
        "",
        "| pages | assets | qa |",
        "| --- | --- | --- |",
        f"| discovered {pages.get('discovered', 0)} · skipped {pages.get('skipped', 0)} · "
        f"imported {pages.get('imported', 0)} · failed {pages.get('failed', 0)} "
        f"| discovered {assets.get('discovered', 0)} · fetched {assets.get('fetched', 0)} · "
        f"deduped {assets.get('deduped', 0)} · failed {assets.get('failed', 0)} "
        f"| pass {qa.get('pass', 0)} · fail {qa.get('fail', 0)} |",
        "",
        f"- targets matched: {report.get('targets_matched')} · unique review units: {report.get('review_units')}",
        f"- source yield: {yield_block.get('source_yield')} · target yield: {yield_block.get('target_yield')}",
        "",
        "## Hosts",
        "",
    ]
    for host, count in sorted((report.get("hosts") or {}).items(), key=lambda item: -item[1]):
        lines.append(f"- {host}: {count}")
    if not report.get("hosts"):
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def write_report(
    report: dict[str, Any],
    *,
    json_path: str | Path | None = None,
    md_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write crawl-report.json / crawl-report.md (a1-6 §十八)."""
    out: dict[str, Any] = {}
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        out["json"] = str(path)
    if md_path:
        path = Path(md_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
        out["markdown"] = str(path)
    return out
