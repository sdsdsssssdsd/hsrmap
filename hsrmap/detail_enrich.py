from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from hsrmap.assets import AssetStore
from hsrmap.database import CoreDatabase
from hsrmap.detail_db import TERMINAL, DetailDatabase
from hsrmap.detail_lock import EnrichmentLock
from hsrmap.detail_normalize import classify_retcode, normalize_point_info
from hsrmap.detail_queue import build_detail_queue, canary_source_ids, point_info_request_params
from hsrmap.http import NO_RETRY_STATUS, RateLimitedClient
from hsrmap.paths import ASSETS, DATA, ENRICHMENTS, GOLDEN_PATH, LOGS, REGISTRY_PATH, SNAPSHOTS
from hsrmap.reports import write_json
from hsrmap.schema import check_payload


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_raw(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return hashlib.sha256(data).hexdigest()


class DetailEnrichment:
    def __init__(self, snapshot_id: str, resume: bool = False, retry_failed: bool = False):
        self.snapshot_id = snapshot_id
        self.resume = resume
        self.retry_failed = retry_failed
        self.snapshot_root = SNAPSHOTS / snapshot_id
        self.core_path = self.snapshot_root / "core.db"
        self.manifest_path = self.snapshot_root / "manifest.json"
        if not self.core_path.exists():
            raise SystemExit(f"core snapshot missing: {self.core_path}")
        self.root = ENRICHMENTS / snapshot_id
        self.staging_db_path = self.root / "staging" / "detail.staging.db"
        self.final_db_path = self.root / "detail.db"
        self.raw_dir = self.root / "raw" / "point_info"
        self.reports_dir = self.root / "reports"
        self.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.app_version = self.registry["client"]["app_version"]
        self.host = self.registry["hosts"]["public"]
        self.client = RateLimitedClient()
        self.assets = AssetStore(ASSETS)
        self.core_sha_start = file_sha256(self.core_path)
        self.manifest_sha = file_sha256(self.manifest_path)
        self.schema_warnings: list[dict[str, Any]] = []
        self.log = logging.getLogger(f"hsrmap.detail.{snapshot_id}")

    def _setup_log(self) -> None:
        LOGS.mkdir(parents=True, exist_ok=True)
        self.log.setLevel(logging.INFO)
        self.log.handlers.clear()
        handler = logging.FileHandler(LOGS / f"detail-{self.snapshot_id}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.log.addHandler(handler)
        self.log.addHandler(logging.StreamHandler())

    def _assert_core_frozen(self) -> None:
        current = file_sha256(self.core_path)
        if current != self.core_sha_start:
            raise SystemExit("core.db changed during enrichment; aborting")

    def _open_core(self) -> CoreDatabase:
        return CoreDatabase(self.core_path, readonly=True)

    def _prepare_db(self) -> DetailDatabase:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.staging_db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.staging_db_path.exists() and self.final_db_path.exists() and self.resume:
            shutil.copy2(self.final_db_path, self.staging_db_path)
        db = DetailDatabase(self.staging_db_path)
        db.reset_in_flight()
        if self.retry_failed:
            db.conn.execute("UPDATE detail_requests SET state = 'PENDING' WHERE state IN ('FAILED_RETRYABLE', 'FAILED_PERMANENT')")
            db.conn.commit()
        db.set_meta("core_snapshot_id", self.snapshot_id)
        db.set_meta("core_manifest_sha256", self.manifest_sha)
        db.set_meta("core_db_sha256", self.core_sha_start)
        db.set_meta("bundle_sha256", self.registry["entry"]["bundle_sha256"])
        db.set_meta("public_api_host", self.host)
        db.set_meta("app_version", self.app_version)
        if db.get_meta("created_at") is None:
            db.set_meta("created_at", _now())
        bound = db.get_meta("core_snapshot_id")
        if bound != self.snapshot_id:
            raise SystemExit(f"enrichment bound to {bound}, not {self.snapshot_id}")
        return db

    def _write_manifest(self, status: str, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "format_version": 1,
            "core_snapshot_id": self.snapshot_id,
            "core_manifest_sha256": self.manifest_sha,
            "started_at": extra.get("started_at") if extra else None,
            "finished_at": extra.get("finished_at") if extra else None,
            "status": status,
        }
        if extra:
            payload.update(extra)
        write_json(self.root / "manifest.json", payload)

    def collect_canary_ids(self, core: CoreDatabase) -> set[str]:
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        return canary_source_ids(core, golden)

    def queue_from_core(
        self,
        core: CoreDatabase,
        *,
        canary: bool = False,
        semantic_key: str | None = None,
        map_id: str | None = None,
        point_id: str | None = None,
    ) -> dict[str, Any]:
        source_ids = None
        if point_id:
            source_ids = {str(point_id)}
        elif semantic_key:
            from hsrmap.detail_queue import source_ids_for_label_key

            source_ids = source_ids_for_label_key(core, semantic_key)
        elif map_id:
            rows = core.conn.execute(
                """
                SELECT p.source_id FROM points p
                JOIN maps m ON m.id = p.map_id
                WHERE m.source_id = ?
                """,
                (str(map_id),),
            )
            source_ids = {str(row["source_id"]) for row in rows}
        elif canary:
            source_ids = self.collect_canary_ids(core)
        return build_detail_queue(core, self.app_version, source_ids)

    def _fetch_one(self, db: DetailDatabase, request_key: str) -> None:
        request = db.get_request(request_key)
        if request is None:
            return
        if request["state"] in TERMINAL and request["state"] != "SOURCE_ASSET_BROKEN":
            return
        db.mark_state(request_key, "FETCHING")
        raw_path = self.raw_dir / f"{request_key}.json"
        try:
            if raw_path.exists():
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
                digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                http_status = 200
            else:
                params = json.loads(request["parameters_json"])
                resp = self.client.api_get(self.host, "/v1/map/point/info", params)
                payload = resp.json()
                digest = _save_raw(raw_path, payload)
                http_status = resp.status
            db.mark_state(request_key, "RAW_SAVED", raw_relpath=str(raw_path.relative_to(self.root)).replace("\\", "/"), raw_sha256=digest, last_http_status=http_status, last_retcode=payload.get("retcode"))
            classification = classify_retcode(payload.get("retcode"), payload.get("message"))
            if classification == "SOURCE_NOT_FOUND":
                parsed = normalize_point_info(payload)
                db.save_detail(request_key, parsed, digest)
                db.mark_state(request_key, "SOURCE_NOT_FOUND", last_retcode=payload.get("retcode"))
                return
            check = check_payload("point_info", payload, self.registry.get("schema_fingerprints", {}).get("point_info"))
            if not check.ok:
                db.mark_state(request_key, "FAILED_SCHEMA", last_error=check.reason)
                return
            if check.reason == "SCHEMA_CHANGED":
                self.schema_warnings.append({"request_key": request_key, "fingerprint": check.fingerprint})
            db.mark_state(request_key, "PARSED")
            parsed = normalize_point_info(payload)
            detail_id = db.save_detail(request_key, parsed, digest)
            if parsed["job_state"] == "COMPLETE_EMPTY":
                db.mark_state(request_key, "COMPLETE_EMPTY")
                return
            db.mark_state(request_key, "ASSETS_PENDING")
            self._fetch_images(db, detail_id)
            broken = int(db.conn.execute("SELECT COUNT(*) n FROM point_detail_assets WHERE detail_id = ? AND state = 'SOURCE_ASSET_BROKEN'", (detail_id,)).fetchone()["n"])
            pending = int(db.conn.execute("SELECT COUNT(*) n FROM point_detail_assets WHERE detail_id = ? AND state = 'PENDING'", (detail_id,)).fetchone()["n"])
            if pending:
                db.mark_state(request_key, "FAILED_RETRYABLE", last_error="asset pending")
            elif broken:
                db.mark_state(request_key, "COMPLETE", last_error="SOURCE_ASSET_BROKEN")
            else:
                db.mark_state(request_key, parsed["job_state"])
        except HTTPError as exc:
            state = "FAILED_PERMANENT" if exc.code in NO_RETRY_STATUS else "FAILED_RETRYABLE"
            db.mark_state(request_key, state, last_http_status=exc.code, last_error=str(exc))
        except Exception as exc:
            db.mark_state(request_key, "FAILED_RETRYABLE", last_error=str(exc))

    def _fetch_images(self, db: DetailDatabase, detail_id: int) -> None:
        rows = list(db.conn.execute("SELECT * FROM point_detail_assets WHERE detail_id = ?", (detail_id,)))
        for row in rows:
            url = row["remote_url"]
            if not url:
                db.set_asset_sha(detail_id, url, None, "SOURCE_ASSET_BROKEN")
                continue
            last_exc: Exception | None = None
            for attempt, delay in enumerate((0,) + (1, 2, 4, 8)):
                if delay:
                    time.sleep(delay)
                try:
                    resp = self.client.get(url)
                    stored = self.assets.ingest_bytes(resp.body, remote_url=url)
                    if not stored.width or not stored.height:
                        raise ValueError("image has no dimensions")
                    db.set_asset_sha(detail_id, url, stored.sha256, "COMPLETE")
                    last_exc = None
                    break
                except HTTPError as exc:
                    if exc.code in NO_RETRY_STATUS:
                        db.set_asset_sha(detail_id, url, None, "SOURCE_ASSET_BROKEN")
                        last_exc = None
                        break
                    last_exc = exc
                except Exception as exc:
                    last_exc = exc
                    self.log.info("asset fail attempt %s %s %s", attempt + 1, url, exc)
            if last_exc is not None:
                db.set_asset_sha(detail_id, url, None, "PENDING")

    def _run_pending(self, db: DetailDatabase) -> None:
        keys = db.pending_keys()
        self.log.info("detail pending %s", len(keys))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._fetch_one, db, key) for key in keys]
            done = 0
            for future in as_completed(futures):
                future.result()
                done += 1
                if done % 50 == 0:
                    self.log.info("detail %s/%s", done, len(keys))

    def _publish(self, db: DetailDatabase) -> None:
        if not db.integrity_ok():
            raise SystemExit("detail.staging.db integrity_check failed")
        db.close()
        tmp = self.final_db_path.with_suffix(".db.tmp")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(self.staging_db_path, tmp)
        if self.final_db_path.exists():
            self.final_db_path.unlink()
        tmp.replace(self.final_db_path)

    def _semantic_report(self, core: CoreDatabase, db: DetailDatabase, key: str) -> dict[str, Any]:
        label_id = core.semantic_source_id(key)
        points = list(
            core.conn.execute(
                """
                SELECT p.id, p.source_id, p.x_pos, p.y_pos, p.raster_x, p.raster_y, m.source_id AS map_source_id, m.name AS map_name
                FROM points p
                JOIN maps m ON m.id = p.map_id
                JOIN point_labels pl ON pl.point_id = p.id
                JOIN label_nodes l ON l.id = pl.label_id
                WHERE l.source_id = ?
                """,
                (label_id,),
            )
        ) if label_id else []
        items = []
        nonempty = empty = with_text = with_images = image_count = 0
        for point in points:
            detail = db.detail_by_source(point["source_id"])
            images = []
            if detail:
                images = list(db.conn.execute("SELECT * FROM point_detail_assets WHERE detail_id = ?", (detail["id"],)))
            status = None if detail is None else detail["detail_state"]
            if status == "EMPTY":
                empty += 1
            elif status == "NONEMPTY":
                nonempty += 1
            if detail and detail["plain_text"]:
                with_text += 1
            if images:
                with_images += 1
            image_count += len(images)
            items.append(
                {
                    "point_source_id": point["source_id"],
                    "map": {"source_id": point["map_source_id"], "name": point["map_name"]},
                    "position": {"x_pos": point["x_pos"], "y_pos": point["y_pos"], "raster_x": point["raster_x"], "raster_y": point["raster_y"]},
                    "description_status": status,
                    "image_count": len(images),
                }
            )
        return {
            "semantic_key": key,
            "source_label_id": label_id,
            "total_core_points": len(points),
            "detail_complete": nonempty + empty,
            "empty_detail": empty,
            "with_official_description": with_text,
            "with_official_images": with_images,
            "official_images": image_count,
            "maps": sorted({item["map"]["source_id"] for item in items}),
            "points": items,
        }

    def write_reports(self, core: CoreDatabase, db: DetailDatabase, queue: dict[str, Any], phase: str) -> dict[str, Any]:
        fixture = db.detail_by_source("5260")
        stats = build_detail_statistics(core, db)
        empty_5260 = fixture is not None and fixture["is_empty"] == 1 and fixture["detail_state"] == "EMPTY"
        reports = {
            "phase": phase,
            "queue": {
                "core_point_count": queue["core_point_count"],
                "unique_source_point_ids": queue["unique_source_point_ids"],
                "unique_request_count": queue["unique_request_count"],
                "duplicate_source_point_ids": queue["duplicate_source_point_ids"],
            },
            "requests": db.request_counts(),
            "stats": stats,
            "empty_fixture_5260": "PASS" if empty_5260 else "FAIL",
            "schema_warnings": self.schema_warnings,
        }
        write_json(self.reports_dir / "queue-report.json", {**queue, "requests": [{"request_key": r["request_key"], "source_point_id": r["source_point_id"]} for r in queue["requests"]], "bindings": len(queue["bindings"])})
        write_json(self.reports_dir / "detail-statistics.json", stats)
        write_json(self.reports_dir / "detail-schema-report.json", {"warnings": self.schema_warnings, "fingerprint": self.registry.get("schema_fingerprints", {}).get("point_info")})
        write_json(self.reports_dir / "floating-grease-details.json", self._semantic_report(core, db, "floating_grease_origin_retrace"))
        write_json(self.reports_dir / "floating-grease-notes-details.json", self._semantic_report(core, db, "floating_grease_notes"))
        write_json(self.reports_dir / "canary-report.json" if phase == "canary" else self.reports_dir / "enrichment-report.json", reports)
        return reports

    def run(
        self,
        *,
        canary: bool = False,
        queue_only: bool = False,
        semantic_key: str | None = None,
        map_id: str | None = None,
        point_id: str | None = None,
    ) -> dict[str, Any]:
        self._setup_log()
        started = _now()
        with EnrichmentLock(self.root / ".lock"):
            self._write_manifest("STAGING", {"started_at": started, "finished_at": None})
            db = self._prepare_db()
            core = self._open_core()
            try:
                queue = self.queue_from_core(core, canary=canary, semantic_key=semantic_key, map_id=map_id, point_id=point_id)
                write_json(self.reports_dir / "queue-report.json", {
                    "core_point_count": queue["core_point_count"],
                    "unique_source_point_ids": queue["unique_source_point_ids"],
                    "unique_request_count": queue["unique_request_count"],
                    "duplicate_source_point_ids": queue["duplicate_source_point_ids"],
                    "duplicate_groups": queue["duplicate_groups"],
                })
                self.log.info(
                    "queue core=%s unique_source=%s unique_req=%s dup=%s",
                    queue["core_point_count"],
                    queue["unique_source_point_ids"],
                    queue["unique_request_count"],
                    queue["duplicate_source_point_ids"],
                )
                if queue_only:
                    return {
                        "status": "QUEUE_ONLY",
                        "queue_summary": {
                            "core_point_count": queue["core_point_count"],
                            "unique_source_point_ids": queue["unique_source_point_ids"],
                            "unique_request_count": queue["unique_request_count"],
                            "duplicate_source_point_ids": queue["duplicate_source_point_ids"],
                        },
                    }
                if queue["unique_request_count"] == 0:
                    raise SystemExit("detail queue is empty")
                db.enqueue_queue(queue)
                self._run_pending(db)
                db.bind_queue(queue)
                phase = "canary" if canary or semantic_key or map_id or point_id else "full"
                reports = self.write_reports(core, db, queue, phase)
                self._assert_core_frozen()
                counts = db.request_counts()
                failed = counts.get("FAILED_RETRYABLE", 0) + counts.get("FAILED_SCHEMA", 0)
                status = "READY" if failed == 0 else "FAILED"
                if failed == 0 and int(db.conn.execute("SELECT COUNT(*) n FROM point_detail_assets WHERE state = 'SOURCE_ASSET_BROKEN'").fetchone()["n"]):
                    status = "READY_WITH_SOURCE_WARNINGS"
                db.set_meta("completed_at", _now())
                self._publish(db)
                self._write_manifest(status, {"started_at": started, "finished_at": _now(), "request_counts": counts})
                self._assert_core_frozen()
                return {
                    "status": status,
                    "reports": reports,
                    "counts": counts,
                    "queue_summary": {
                        "core_point_count": queue["core_point_count"],
                        "unique_source_point_ids": queue["unique_source_point_ids"],
                        "unique_request_count": queue["unique_request_count"],
                        "duplicate_source_point_ids": queue["duplicate_source_point_ids"],
                    },
                }
            finally:
                core.close()


def build_detail_statistics(core: CoreDatabase, db: DetailDatabase) -> dict[str, Any]:
    core_points = int(core.conn.execute("SELECT COUNT(*) n FROM points").fetchone()["n"])
    details = list(db.conn.execute("SELECT * FROM point_details"))
    bindings = int(db.conn.execute("SELECT COUNT(*) n FROM point_detail_bindings").fetchone()["n"])
    assets = list(db.conn.execute("SELECT * FROM point_detail_assets"))
    shas = {row["asset_sha256"] for row in assets if row["asset_sha256"]}
    bytes_total = 0
    for sha in shas:
        matches = list((ASSETS / sha[:2]).glob(f"{sha}.*"))
        if matches:
            bytes_total += matches[0].stat().st_size
    nonempty = sum(1 for row in details if row["detail_state"] == "NONEMPTY")
    empty = sum(1 for row in details if row["detail_state"] == "EMPTY")
    with_text = sum(1 for row in details if row["plain_text"])
    with_image = sum(1 for row in details if int(db.conn.execute("SELECT COUNT(*) n FROM point_detail_assets WHERE detail_id = ? AND asset_sha256 IS NOT NULL", (row["id"],)).fetchone()["n"]))
    by_label = []
    for row in core.conn.execute(
        """
        SELECT l.source_id, l.name, COUNT(DISTINCT p.id) AS core_n
        FROM label_nodes l
        JOIN point_labels pl ON pl.label_id = l.id
        JOIN points p ON p.id = pl.point_id
        GROUP BY l.id
        ORDER BY core_n DESC
        """
    ):
        core_ids = [
            int(item["id"])
            for item in core.conn.execute(
                """
                SELECT p.id FROM points p
                JOIN point_labels pl ON pl.point_id = p.id
                JOIN label_nodes l ON l.id = pl.label_id
                WHERE l.source_id = ?
                """,
                (row["source_id"],),
            )
        ]
        detail_rows = []
        if core_ids and bindings:
            placeholders = ",".join("?" * len(core_ids))
            detail_rows = list(
                db.conn.execute(
                    f"""
                    SELECT d.is_empty, d.plain_text, d.id FROM point_details d
                    JOIN point_detail_bindings b ON b.detail_id = d.id
                    WHERE b.core_point_id IN ({placeholders})
                    """,
                    core_ids,
                )
            )
        by_label.append(
            {
                "label_id": row["source_id"],
                "name": row["name"],
                "core_point_count": row["core_n"],
                "nonempty": sum(1 for item in detail_rows if item["is_empty"] == 0),
                "empty": sum(1 for item in detail_rows if item["is_empty"] == 1),
                "with_text": sum(1 for item in detail_rows if item["plain_text"]),
                "with_image": sum(
                    1
                    for item in detail_rows
                    if db.conn.execute("SELECT COUNT(*) n FROM point_detail_assets WHERE detail_id = ? AND asset_sha256 IS NOT NULL", (item["id"],)).fetchone()["n"]
                ),
            }
        )
    return {
        "total_core_points": core_points,
        "unique_detail_requests": int(db.conn.execute("SELECT COUNT(*) n FROM detail_requests").fetchone()["n"]),
        "bindings": bindings,
        "nonempty_details": nonempty,
        "empty_details": empty,
        "details_with_text": with_text,
        "details_without_text": len(details) - with_text,
        "details_with_image": with_image,
        "details_without_image": len(details) - with_image,
        "image_count": len(assets),
        "unique_image_assets": len(shas),
        "image_bytes": bytes_total,
        "missing_images": sum(1 for row in assets if row["state"] == "PENDING"),
        "source_broken": sum(1 for row in assets if row["state"] == "SOURCE_ASSET_BROKEN"),
        "by_label": by_label,
        "request_states": db.request_counts(),
    }


def run_enrichment(**kwargs) -> dict[str, Any]:
    snapshot = kwargs.pop("snapshot_id")
    runner = DetailEnrichment(snapshot, resume=kwargs.pop("resume", False), retry_failed=kwargs.pop("retry_failed", False))
    return runner.run(**kwargs)
