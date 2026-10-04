from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hsrmap.guides.assets import AssetCache, AssetFetcher
from hsrmap.guides.crawler.fetch import fetch_page
from hsrmap.guides.crawler.render import browser_path, render_page
from hsrmap.guides.crawler.guard import URLRejected, admit_url
from hsrmap.guides.crawler.hosts import ROBOTS_DENIED, HostScheduler, RobotsCache
from hsrmap.guides.crawler.identity import ArticleFamilyResolver
from hsrmap.guides.evidence import finish_search, record_result, start_search
from hsrmap.guides.crawler.robots import path_allowed
from hsrmap.guides.derived import DerivedGuideStore
from hsrmap.guides.extract.deterministic import DeterministicGuideExtractor
from hsrmap.guides.ingest import ingest_page
from hsrmap.http import RateLimitedClient
from hsrmap.paths import DATA, GUIDE_ASSETS, GUIDE_CACHE, GUIDE_DB, GUIDE_DERIVED, GUIDE_RAW


CANARY_URLS = [
    "https://news.17173.com/content/04222026/173231817.shtml",
    "https://www.gamersky.com/handbook/202602/2092445.shtml",
    "https://shouyou.3dmgame.com/gl/619094.html",
    "https://www.taptap.cn/moment/786205107434815910",
    "https://ol.3dmgame.com/gl/332681.html",
]


def _robots(client: RateLimitedClient, url: str) -> str | None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.get(robots_url, timeout=20)
        return resp.body.decode("utf-8", errors="replace")
    except Exception:
        return None


def _official_catalog(topic_key: str = "floating_grease") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from hsrmap.guides.topics.official import official_maps_for_topic, official_points_for_topic

        points = official_points_for_topic(topic_key)
        maps = official_maps_for_topic(topic_key)
        return points, maps
    except Exception:
        return [], []


def _asset_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count typed asset outcomes so a report explains a host's refusals."""
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "?")
        counts[key] = counts.get(key, 0) + 1
    blocked = [str(row.get("source_url")) for row in rows if row.get("status") == "HTTP_BLOCKED"]
    return {"total": len(rows), "counts": counts, "blocked_sample": blocked[:5]}


def known_urls(db) -> set[str]:
    """URLs already stored as pages — a crawl must not fetch them again."""
    return {
        str(row["canonical_url"])
        for row in db.conn.execute("SELECT canonical_url FROM guide_page")
        if row["canonical_url"]
    }


def import_real_urls(
    urls: list[str] | None = None,
    *,
    topic: str = "floating_grease",
    inbox: Path | None = None,
    db=None,
    store=None,
    official_points: list[dict[str, Any]] | None = None,
    official_maps: list[dict[str, Any]] | None = None,
    skip_known: bool = True,
    render: bool | None = None,
    derived_root: Path | None = None,
    cache_root: Path | None = None,
    job_id: int | None = None,
    budget: dict[str, Any] | None = None,
    report_dir: Path | None = None,
) -> list[dict[str, Any]]:
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.guides.store import RawGuideStore

    client = RateLimitedClient(min_interval=1.0)
    dest = Path(inbox or (DATA / "guides" / "inbox"))
    dest.mkdir(parents=True, exist_ok=True)
    database = db or GuideDatabase(GUIDE_DB)
    raw = store or RawGuideStore(GUIDE_RAW, GUIDE_ASSETS)
    extractor = DeterministicGuideExtractor()
    derived = DerivedGuideStore(Path(derived_root) if derived_root is not None else GUIDE_DERIVED)
    cache = AssetCache(Path(cache_root) if cache_root is not None else GUIDE_CACHE, db=database)
    fetcher = AssetFetcher(client=client, cache=cache)
    topic_key = str(topic).replace("-", "_")
    if render is None:
        #: rendering is offered whenever this machine has a browser to do it with
        render = bool(browser_path())
    if official_points is None or official_maps is None:
        catalog_points, catalog_maps = _official_catalog(topic_key)
        official_points = official_points if official_points is not None else catalog_points
        official_maps = official_maps if official_maps is not None else catalog_maps
    reports = []
    pending = list(urls or CANARY_URLS)
    tracker = _open_tracker(database, topic_key, pending, job_id=job_id, budget=budget)
    if tracker is not None:
        pending = tracker.pending(pending)
    stop_reason = ""
    known = known_urls(database) if skip_known else set()
    resolver = ArticleFamilyResolver()
    scheduler = HostScheduler(min_interval=max(1.0, float(getattr(client, "min_interval", 1.0) or 1.0)))
    robots_cache = RobotsCache(fetch=lambda target: _robots(client, target))
    # Every crawl run leaves search evidence (a1-6 §八), so "why is this target
    # still empty?" is answerable from the database instead of from memory.
    search_run = start_search(
        database, topic=topic_key, target_key="", query=f"corpus:{topic_key}", provider="corpus"
    )
    rank = 0

    def note(target: str, decision: str, reason: str = "") -> None:
        nonlocal rank
        rank += 1
        record_result(
            database,
            search_run,
            rank=rank,
            url=target,
            decision=decision,
            reason=reason,
            topic=topic_key,
        )

    def emit(report: dict[str, Any]) -> None:
        """Record one URL outcome for the manifest and for the job counters."""
        reports.append(report)
        if tracker is not None:
            tracker.note_page(url, report)
            tracker.flush()

    seen: set[str] = set()
    #: family -> host we already took it from, so a mobile/APP mirror of a page
    #: fetched earlier in this same run is not fetched a second time.
    taken: dict[str, str] = {}
    for url in pending:
        if tracker is not None:
            stop_reason = tracker.stop_reason() or ""
            if stop_reason:
                break
        if url in seen:
            continue
        seen.add(url)
        if url in known:
            emit({"url": url, "status": "SKIPPED_ALREADY_IMPORTED"})
            note(url, "ALREADY_IMPORTED", "page is already in the corpus")
            continue
        ref = resolver.resolve(url)
        mirror = resolver.mirror_of_known(url, known) if known else None
        if mirror is None and ref.family in taken and taken[ref.family] != ref.raw_host:
            mirror = taken[ref.family]
        if mirror is not None:
            emit({"url": url, "status": "SKIPPED_MIRROR", "of": mirror})
            note(url, "MIRROR", f"mirror of {mirror}")
            continue
        taken.setdefault(ref.family, ref.raw_host)
        parsed = urlparse(url)
        try:
            admit_url(url)
        except URLRejected as exc:
            emit({"url": url, "status": "URL_REJECTED", "reason": exc.reason})
            note(url, "BLOCKED", f"URL admission refused it: {exc.reason}")
            continue
        if not scheduler.before(url):
            state = scheduler.state(url)
            emit({"url": url, "status": "SKIPPED_CIRCUIT_OPEN", "reason": state.reason})
            note(url, "BLOCKED", f"circuit open for {state.host}: {state.reason}")
            continue
        robots = robots_cache.get(url)
        if robots is not None and not path_allowed(robots, parsed.path or "/"):
            emit({"url": url, "status": ROBOTS_DENIED, "reason": "robots.txt"})
            note(url, "BLOCKED", "robots.txt disallows this path")
            continue
        try:
            fetched = fetch_page(url, client, robots_txt=robots)
        except Exception as exc:
            scheduler.after(url, ok=False, reason=str(exc)[:80])
            emit({"url": url, "status": "NEEDS_MANUAL_IMPORT", "reason": str(exc)[:160]})
            note(url, "BLOCKED", str(exc)[:160])
            continue
        if fetched.get("status") != "ok":
            failed_status = str(fetched.get("status") or "")
            scheduler.after(
                url,
                ok=False,
                blocked=failed_status in {"HTTP_BLOCKED", "HTTP_FORBIDDEN"},
                reason=failed_status,
            )
            emit({"url": url, **fetched})
            note(url, "BLOCKED", f"fetch status {fetched.get('status')}: {fetched.get('reason') or ''}"[:160])
            continue
        scheduler.after(url, ok=True)
        html = fetched["html"]
        from hsrmap.guides.crawler.pagination import sibling_pages

        for extra in sibling_pages(html, url):
            if extra not in seen and extra not in pending and len(pending) < 30:
                pending.append(extra)
        slug = parsed.path.rstrip("/").split("/")[-1] or "page"
        html_path = dest / f"{parsed.netloc.replace('.', '_')}_{slug}.html"
        html_path.write_text(html, encoding="utf-8")

        asset_log: list[dict[str, Any]] = []

        def fetch_asset(src: str, _page=url) -> bytes:
            # one unreachable image must never abort the whole page import
            try:
                outcome = fetcher.fetch(src, _page)
            except Exception as exc:  # noqa: BLE001 - typed failure, reported below
                asset_log.append({
                    "status": "NETWORK_ERROR",
                    "source_url": src,
                    "reason": f"{type(exc).__name__}: {exc}"[:160],
                })
                return b""
            asset_log.append(outcome.to_report())
            return outcome.body if outcome.ok else b""

        try:
            ingested = ingest_page(
                html,
                url,
                database,
                raw,
                extractor,
                derived=derived,
                topic=topic_key,
                fetch_asset=fetch_asset,
                official_points=official_points,
                official_maps=official_maps,
            )
        except Exception as exc:  # noqa: BLE001 - report it and keep crawling
            scheduler.after(url, ok=False, reason=str(exc)[:80])
            emit({
                "url": url,
                "status": "IMPORT_FAILED",
                "reason": f"{type(exc).__name__}: {exc}"[:160],
            })
            note(url, "IRRELEVANT", f"import failed: {exc}"[:160])
            continue
        qa = ingested["qa"]
        #: the verdict lives beside the raw inspection ("qa_status"/"qa_reason").
        #: Reading the reason out of "qa" left it empty, so pages that need a browser
        #: were filed as IRRELEVANT — "not about this target" — instead of JS_ONLY,
        #: and the planner could never learn that the host needs rendering.
        qa_pass = bool(qa.get("pass")) or str(ingested.get("qa_status") or "") == "QA_PASS"
        qa_reason = str(ingested.get("qa_reason") or qa.get("reason") or "")
        rendered = False
        if not qa_pass and qa_reason == "JS_RENDER_REQUIRED" and render:
            #: the article exists, it is written into the DOM after the bundle runs
            rendered_result = render_page(url)
            if rendered_result.get("status") == "ok":
                rendered_html = str(rendered_result.get("html") or "")
                html_path.write_text(rendered_html, encoding="utf-8")
                try:
                    ingested = ingest_page(
                        rendered_html,
                        url,
                        database,
                        raw,
                        extractor,
                        derived=derived,
                        topic=topic_key,
                        fetch_asset=fetch_asset,
                        official_points=official_points,
                        official_maps=official_maps,
                    )
                except Exception as exc:  # noqa: BLE001 - keep crawling
                    emit({
                        "url": url,
                        "status": "IMPORT_FAILED",
                        "reason": f"render import: {type(exc).__name__}: {exc}"[:160],
                        "rendered": True,
                    })
                    note(url, "JS_ONLY", "rendered dom failed to import")
                    continue
                rendered = True
                qa = ingested["qa"]
                qa_pass = bool(qa.get("pass")) or str(ingested.get("qa_status") or "") == "QA_PASS"
                qa_reason = str(ingested.get("qa_reason") or qa.get("reason") or "")
            elif str(rendered_result.get("status")) == "NO_BROWSER":
                render = False
        if qa_pass:
            note(
                url,
                "ACCEPTED",
                "imported after a headless render" if rendered else "imported and admitted by QA",
            )
        elif qa_reason == "JS_RENDER_REQUIRED":
            note(url, "JS_ONLY", "content only exists after JS renders")
        else:
            note(url, "IRRELEVANT", f"QA reject: {qa_reason}"[:160])
        if tracker is not None:
            tracker.note_assets(asset_log)
        emit(
            {
                "url": url,
                "status": "IMPORTED" if qa.get("pass") else "QA_FAIL",
                "page_id": ingested["page"]["id"],
                "title": ingested["page"].get("title"),
                "author": ingested["page"].get("author"),
                "source_claim": ingested["page"].get("source_claim"),
                "qa_pass": qa_pass,
                "rendered": rendered,
                "raw_html_exists": qa.get("raw_html_exists"),
                "images_in_guide_assets": qa.get("images_in_guide_assets"),
                "ad_images_dropped": qa.get("ad_images_dropped"),
                "image_blocks": qa.get("image_blocks"),
                "assets": _asset_summary(asset_log),
                "review": [item["status"] for item in ingested["review"]],
                "inbox": str(html_path),
            }
        )
    finish_search(database, search_run, status="OK", reason=f"{len(reports)} urls", result_count=rank)
    manifest = dest / "import-report.json"
    manifest.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    extra = {"scheduler": scheduler.snapshot(), "robots": robots_cache.state()}
    if tracker is not None:
        _finish_tracker(tracker, reports, stop_reason=stop_reason, dest=dest, report_dir=report_dir, extra=extra)
    else:
        _write_plain_report(reports, topic_key, dest=dest, report_dir=report_dir, extra=extra)
    return reports


def _open_tracker(
    database,
    topic_key: str,
    pending: list[str],
    *,
    job_id: int | None,
    budget: dict[str, Any] | None,
):
    """Resume the job that was asked for, start one when a budget was given."""
    from hsrmap.guides.jobs import (
        JobBudget,
        JobTracker,
        create_job,
        fail_stale_jobs,
        get_job,
        start_job,
    )

    #: a crashed run leaves its row RUNNING forever, and "running" then means
    #: nothing; starting (or resuming) a job is the moment we know no other
    #: process is alive — except the very job being resumed, which the caller owns
    for stale in fail_stale_jobs(database, keep=int(job_id) if job_id is not None else None):
        print(f"closed stale job {stale} (no live process owned it)")

    if job_id is not None:
        job = get_job(database, int(job_id))
        if str(job.get("state")) == "COMPLETED":
            raise ValueError(f"job {job_id} is already completed")
        if budget is not None:
            # resuming with an explicit budget raises (or lowers) the stored one
            from hsrmap.guides.jobs import update_job

            job = update_job(database, int(job_id), budget_json=JobBudget.from_mapping(budget).as_dict())
        tracker = JobTracker(database, job)
    else:
        if budget is None:
            return None
        job = create_job(
            database,
            job_type="corpus",
            topic=topic_key,
            cursor={"urls": list(pending)},
            budget=JobBudget.from_mapping(budget),
        )
        tracker = JobTracker(database, job)
    start_job(database, tracker.job_id)
    return JobTracker(database, {**tracker.job, "state": "RUNNING"})


def _finish_tracker(
    tracker,
    reports: list[dict[str, Any]],
    *,
    stop_reason: str,
    dest: Path,
    report_dir: Path | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close the job with a crawl report (a1-6 §十八)."""
    from hsrmap.guides.jobs import build_report, fail_job, job_status, pause_job, write_report

    counters = tracker.counters
    if stop_reason.startswith("budget:max_failures"):
        state, error = "FAILED", stop_reason
    elif stop_reason:
        state, error = "PAUSED", stop_reason
    elif counters.get("failures", 0) >= tracker.budget.max_failures:
        state, error = "FAILED", "too many failures"
    else:
        state, error = "COMPLETED", ""
    report = build_report(
        {**tracker.job, "job_id": tracker.job_id},
        counters,
        elapsed=tracker.elapsed(),
        results=reports,
        state=state,
        extra=extra,
    )
    out_dir = Path(report_dir) if report_dir is not None else dest
    paths = write_report(
        report,
        json_path=out_dir / "crawl-report.json",
        md_path=out_dir / "crawl-report.md",
    )
    tracker.finish(state, error=error, report_path=str(paths.get("json") or ""))
    return report


def _write_plain_report(
    reports: list[dict[str, Any]],
    topic_key: str,
    *,
    dest: Path,
    report_dir: Path | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A run without a job still reports the same numbers (a1-6 §十八)."""
    from hsrmap.guides.jobs import JobTracker, build_report, create_job, write_report

    database = None
    counters = {
        "requests": len(reports),
        "pages_discovered": len(reports),
        "pages_imported": sum(1 for item in reports if item.get("status") == "IMPORTED"),
        "pages_skipped": sum(1 for item in reports if str(item.get("status", "")).startswith("SKIPPED")),
        "qa_pass": sum(1 for item in reports if item.get("status") == "IMPORTED"),
        "qa_fail": sum(1 for item in reports if item.get("status") == "QA_FAIL"),
        "review_units": sum(len(item.get("review") or []) for item in reports),
        "targets_matched": sum(1 for item in reports if item.get("review")),
    }
    report = build_report(
        {"job_type": "corpus", "topic": topic_key}, counters, elapsed=0.0, results=reports, extra=extra
    )
    out_dir = Path(report_dir) if report_dir is not None else dest
    write_report(report, json_path=out_dir / "crawl-report.json", md_path=out_dir / "crawl-report.md")
    return report

