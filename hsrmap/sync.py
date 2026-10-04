from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hsrmap.assets import AssetStore
from hsrmap.database import CoreDatabase
from hsrmap.http import RateLimitedClient
from hsrmap.inspect_map import compose_map
from hsrmap.jobs import JobStore
from hsrmap.normalize import flatten_label_nodes, flatten_map_nodes, normalize_map_info, normalize_point
from hsrmap.paths import (
    ASSETS,
    BASELINE,
    CURRENT_PATH,
    DATA,
    GOLDEN_PATH,
    LOCK_PATH,
    LOGS,
    REGISTRY_PATH,
    SNAPSHOTS,
    STAGING,
)
from hsrmap.preflight import load_registry, preflight
from hsrmap.reports import build_statistics, write_json
from hsrmap.schema import check_payload
from hsrmap.validate import golden_point_errors

PLANETS = ("空间站", "雅利洛", "罗浮", "匹诺康尼", "翁法罗斯", "二相乐园", "千星城")


def descendant_source_ids(db: CoreDatabase, root_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for node in db.conn.execute("SELECT source_id, parent_source_id FROM map_nodes"):
        children.setdefault(node["parent_source_id"] or "", []).append(node["source_id"])
    found = {root_id}
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def first_descendant_map_with_points(db: CoreDatabase, root_id: str):
    for source_id in sorted(descendant_source_ids(db, root_id), key=lambda value: int(value) if str(value).isdigit() else str(value)):
        candidate = db.map_by_source(source_id)
        if candidate and db.count_points_for_map(source_id) > 0:
            return candidate
    return None


def run_smoke_overlays(db: CoreDatabase, debug_dir: Path) -> list[dict[str, Any]]:
    debug_dir.mkdir(parents=True, exist_ok=True)
    results = []
    nodes = list(db.conn.execute("SELECT source_id, name FROM map_nodes WHERE parent_source_id IS NULL"))
    for planet in PLANETS:
        node = next((n for n in nodes if planet in str(n["name"])), None)
        if not node:
            results.append({"planet": planet, "ok": False, "reason": "top-level missing"})
            continue
        row = first_descendant_map_with_points(db, node["source_id"])
        if row is None:
            results.append({"planet": planet, "ok": False, "reason": "no map with points"})
            continue
        try:
            image = compose_map(db, row)
            from PIL import ImageDraw
            draw = ImageDraw.Draw(image)
            pts = db.points_for_map(row["id"])[:5]
            for point in pts:
                x, y = float(point["raster_x"]), float(point["raster_y"])
                draw.ellipse((x - 8, y - 8, x + 8, y + 8), outline=(255, 0, 0, 255), width=3)
            dest = debug_dir / f"smoke-{planet}-{row['source_id']}.png"
            image.save(dest)
            results.append({"planet": planet, "ok": True, "map": row["source_id"], "points": len(pts), "file": str(dest)})
        except Exception as exc:
            results.append({"planet": planet, "ok": False, "reason": str(exc)})
    return results


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _setup_log(snapshot_id: str) -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"hsrmap.sync.{snapshot_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(LOGS / f"sync-{snapshot_id}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def _acquire_lock() -> Path:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        raise SystemExit(f"sync lock exists: {LOCK_PATH}")
    LOCK_PATH.write_text("locked", encoding="utf-8")
    return LOCK_PATH


def _query(app_version: str, **extra) -> dict[str, Any]:
    q = {"app_sn": "sr_map", "lang": "zh-cn", "app_version": app_version}
    q.update(extra)
    return q


def _save_raw(path: Path, payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _sha(text.encode("utf-8"))


def _set_state(status_path: Path, status: dict[str, Any], state: str) -> None:
    status["state"] = state
    write_json(status_path, status)


class CoreSync:
    def __init__(self, resume: bool = False):
        self.resume = resume
        self.client = RateLimitedClient(min_interval=0.3)
        self.registry = load_registry(REGISTRY_PATH)
        self.assets = AssetStore(ASSETS)
        self.snapshot_id = None
        self.root = None
        self.jobs = None
        self.db = None
        self.log = None
        self.status = {}
        self.missing_assets: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        lock = _acquire_lock()
        try:
            return self._run()
        finally:
            if lock.exists():
                lock.unlink()

    def _prepare(self) -> None:
        STAGING.mkdir(parents=True, exist_ok=True)
        existing = sorted(p for p in STAGING.iterdir() if p.is_dir() and (p / "status.json").exists())
        if self.resume and existing:
            self.root = existing[-1]
            self.snapshot_id = self.root.name
            self.status = json.loads((self.root / "status.json").read_text(encoding="utf-8"))
        else:
            self.snapshot_id = _now_id()
            self.root = STAGING / self.snapshot_id
            self.root.mkdir(parents=True)
            self.status = {"snapshot_id": self.snapshot_id, "state": "CREATED", "created_at": datetime.now(timezone.utc).isoformat()}
            write_json(self.root / "status.json", self.status)
        self.log = _setup_log(self.snapshot_id)
        self.jobs = JobStore(self.root / "jobs.json")
        self.jobs.reset_running_to_pending()
        self.db = CoreDatabase(self.root / "core.db")

    def _run(self) -> dict[str, Any]:
        self._prepare()
        status_path = self.root / "status.json"
        _set_state(status_path, self.status, "PREFLIGHT")
        flight = preflight(self.registry, self.client)
        write_json(self.root / "reports" / "preflight.json", flight)
        if not flight["ok"]:
            _set_state(status_path, self.status, "FAILED_SCHEMA")
            raise SystemExit("preflight failed schema compatibility")
        host = flight["public_host"]
        app_version = flight["app_version"]
        if flight.get("registry_candidate"):
            write_json(self.root / "registry-candidate.json", flight["registry_candidate"])

        _set_state(status_path, self.status, "FETCH_TREE")
        tree_payload = self._get_tree(host, app_version, "/v1/map/tree", "map_tree.json", "map_tree")
        label_payload = self._get_tree(host, app_version, "/v1/map/label/tree", "label_tree.json", "label_tree")
        map_nodes = flatten_map_nodes(((tree_payload.get("data") or {}).get("tree") or []))
        label_nodes, bindings = flatten_label_nodes(((label_payload.get("data") or {}).get("tree") or []))
        self.db.insert_map_nodes(map_nodes)
        self.db.insert_label_nodes(label_nodes)
        self.db.insert_semantic_bindings(bindings)

        renderable = [n for n in map_nodes if n["is_renderable"]]
        unknown = [n for n in map_nodes if n.get("node_type") not in (1, 2)]
        self.status["tree"] = {
            "total": len(map_nodes),
            "renderable": len(renderable),
            "unknown": len(unknown),
        }
        if unknown:
            self.log.warning("unknown node_types: %s", Counter := {n["node_type"] for n in unknown})

        for node in renderable:
            self.jobs.enqueue("map_info", node["source_id"], persist=False)
            self.jobs.enqueue("point_list", node["source_id"], persist=False)
        self.jobs.save()

        _set_state(status_path, self.status, "FETCH_MAP_INFO")
        self._run_map_jobs(host, app_version, "map_info", "/v1/map/info", self.root / "raw" / "map_info")
        _set_state(status_path, self.status, "FETCH_POINT_LIST")
        self._run_map_jobs(host, app_version, "point_list", "/v1/map/point/list", self.root / "raw" / "point_list")

        _set_state(status_path, self.status, "FETCH_ASSETS")
        self._download_assets()

        _set_state(status_path, self.status, "VALIDATE")
        reports = self._validate(renderable, unknown)
        write_json(self.root / "reports" / "sync-report.json", reports["sync"])
        write_json(self.root / "reports" / "validation-report.json", reports["validation"])
        write_json(self.root / "reports" / "missing-assets.json", self.missing_assets)
        write_json(self.root / "reports" / "point-statistics.json", reports["stats"])
        write_json(self.root / "reports" / "schema-report.json", flight["schema"])

        if not reports["validation"]["passed"]:
            _set_state(status_path, self.status, "FAILED_VALIDATION")
            raise SystemExit("core validation failed")

        _set_state(status_path, self.status, "READY" if not reports["validation"].get("warnings") else "READY_WITH_WARNINGS")
        published = self._publish()
        _set_state(status_path, self.status, "PUBLISHED")
        return published

    def _get_tree(self, host, app_version, path, filename, endpoint):
        raw_path = self.root / "raw" / filename
        if raw_path.exists():
            return json.loads(raw_path.read_text(encoding="utf-8"))
        payload = None
        for map_id in (0, 38, 842):
            resp = self.client.api_get(host, path, _query(app_version, map_id=map_id))
            payload = resp.json()
            if payload.get("retcode") == 0 and ((payload.get("data") or {}).get("tree")):
                break
        check = check_payload(endpoint, payload, self.registry.get("schema_fingerprints", {}).get(endpoint))
        if not check.ok:
            raise SystemExit(f"{endpoint} required schema failed: {check.missing}")
        _save_raw(raw_path, payload)
        return payload

    def _run_map_jobs(self, host, app_version, job_type, path, raw_dir):
        pending = self.jobs.pending(job_type)
        self.log.info("%s pending %s", job_type, len(pending))

        def one(resource_id: str):
            self.jobs.mark_running(job_type, resource_id)
            dest = raw_dir / f"{resource_id}.json"
            try:
                if dest.exists():
                    payload = json.loads(dest.read_text(encoding="utf-8"))
                    digest = _sha(dest.read_bytes())
                else:
                    resp = self.client.api_get(host, path, _query(app_version, map_id=resource_id))
                    payload = resp.json()
                    digest = _save_raw(dest, payload)
                if payload.get("retcode") != 0:
                    raise ValueError(payload.get("message"))
                check = check_payload(job_type, payload, self.registry.get("schema_fingerprints", {}).get(job_type))
                if not check.ok:
                    raise ValueError(f"schema {check.missing}")
                if job_type == "map_info":
                    mapped = normalize_map_info(payload)
                    self.db.insert_map(mapped, map_info_sha256=digest)
                else:
                    map_row = self.db.map_by_source(resource_id)
                    if map_row is None:
                        raise ValueError("map missing before point/list")
                    points = [
                        normalize_point(p, resource_id, map_row["origin_x"], map_row["origin_y"])
                        for p in ((payload.get("data") or {}).get("point_list") or [])
                    ]
                    self.db.insert_points(points)
                self.jobs.mark_success(job_type, resource_id, str(dest), digest)
                return resource_id, None
            except Exception as exc:
                self.jobs.mark_failed(job_type, resource_id, str(exc))
                return resource_id, str(exc)

        # Sequential per worker via two-thread pool; client is rate-limited.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(one, job.resource_id) for job in pending]
            done = 0
            for future in as_completed(futures):
                resource_id, error = future.result()
                done += 1
                if error:
                    self.log.error("%s %s failed: %s", job_type, resource_id, error)
                elif done % 50 == 0:
                    self.log.info("%s %s/%s", job_type, done, len(pending))
        failed = [j.resource_id for j in self.jobs.pending(job_type)]
        if failed:
            raise SystemExit(f"{job_type} still failed: {failed[:10]}")

    def _download_assets(self) -> None:
        urls: list[tuple[str, str, bool]] = []
        for frag in self.db.conn.execute("SELECT id, remote_url FROM map_fragments"):
            if frag["remote_url"]:
                urls.append((frag["remote_url"], "fragment", True))
        for label in self.db.conn.execute("SELECT source_id, icon_remote_url FROM label_nodes WHERE is_selectable = 1"):
            if label["icon_remote_url"]:
                urls.append((label["icon_remote_url"], f"icon:{label['source_id']}", False))
        unique = {}
        for url, kind, critical in urls:
            unique.setdefault(url, (kind, critical))

        def fetch(url: str, kind: str, critical: bool):
            job_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            self.jobs.enqueue("asset", job_id)
            if self.jobs.get("asset", job_id).state == "SUCCESS":
                return
            self.jobs.mark_running("asset", job_id)
            try:
                body = self.client.get(url).body
                stored = self.assets.ingest_bytes(body, remote_url=url)
                rel = str(stored.local_path.relative_to(DATA)).replace("\\", "/")
                with self.db._lock:
                    self.db.conn.execute(
                        """
                        INSERT OR REPLACE INTO assets(sha256, mime_type, extension, byte_size, width, height, local_relpath)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            stored.sha256,
                            stored.mime_type,
                            stored.local_path.suffix,
                            stored.byte_size,
                            stored.width,
                            stored.height,
                            rel,
                        ),
                    )
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO asset_sources(remote_url, sha256) VALUES (?, ?)",
                        (url, stored.sha256),
                    )
                    if kind == "fragment":
                        self.db.conn.execute(
                            "UPDATE map_fragments SET asset_sha256 = ? WHERE remote_url = ?",
                            (stored.sha256, url),
                        )
                    elif kind.startswith("icon:"):
                        source_id = kind.split(":", 1)[1]
                        self.db.conn.execute(
                            "UPDATE label_nodes SET icon_asset_sha256 = ? WHERE source_id = ?",
                            (stored.sha256, source_id),
                        )
                    self.db.conn.commit()
                self.jobs.mark_success("asset", job_id, rel, stored.sha256)
            except Exception as exc:
                self.jobs.mark_failed("asset", job_id, str(exc))
                self.missing_assets.append({"url": url, "kind": kind, "critical": critical, "error": str(exc)})
                if critical:
                    raise

        items = list(unique.items())
        self.log.info("assets unique %s", len(items))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fetch, url, kind, critical) for url, (kind, critical) in items]
            done = 0
            for future in as_completed(futures):
                future.result()
                done += 1
                if done % 50 == 0:
                    self.log.info("assets %s/%s", done, len(items))

    def _validate(self, renderable, unknown) -> dict[str, Any]:
        stats = build_statistics(self.db)
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        golden_err = golden_point_errors(self.db, golden)
        warnings = []
        failures = []
        if unknown:
            warnings.append("unknown_nodes")
        if abs(stats["maps"]["renderable_maps"] - BASELINE["renderable_maps"]) / BASELINE["renderable_maps"] > 0.2:
            failures.append("SUSPICIOUS_DATA_CHANGE_maps")
        if abs(stats["labels"]["total"] - BASELINE["labels"]) / BASELINE["labels"] > 0.2:
            failures.append("SUSPICIOUS_DATA_CHANGE_labels")
        if stats["maps"]["renderable_maps"] != len(renderable):
            failures.append("renderable_count_mismatch")
        missing_info = [n["source_id"] for n in renderable if self.db.map_by_source(n["source_id"]) is None]
        if missing_info:
            failures.append("missing_map_info")
        missing_points = []
        for n in renderable:
            raw = self.root / "raw" / "point_list" / f"{n['source_id']}.json"
            if not raw.exists():
                missing_points.append(n["source_id"])
        if missing_points:
            failures.append("missing_point_list")
        missing_frags = list(
            self.db.conn.execute("SELECT id FROM map_fragments WHERE asset_sha256 IS NULL OR asset_sha256 = ''")
        )
        if missing_frags:
            failures.append("missing_critical_fragments")
        if golden_err["mean"] != 0 or golden_err["max"] != 0:
            failures.append("golden_points")
        unknown_labels = list(
            self.db.conn.execute("SELECT source_id FROM label_nodes WHERE name IS NULL")
        )
        if unknown_labels:
            failures.append("unknown-label-reference")
        if stats["coordinates"]["outside_ratio"] > 0.25:
            failures.append("outside_ratio_high")
        smoke = self._smoke_overlays()
        validation = {
            "passed": not failures,
            "failures": failures,
            "warnings": warnings,
            "golden": golden_err,
            "smoke": smoke,
            "orphan_check": self._orphans(),
        }
        sync = {
            "snapshot_id": self.snapshot_id,
            "status": "PASS" if validation["passed"] else "FAIL",
            "counts": self.db.counts(),
        }
        return {"validation": validation, "sync": sync, "stats": stats}

    def _orphans(self) -> dict[str, int]:
        return {
            "points_without_map": int(self.db.conn.execute("SELECT COUNT(*) n FROM points p LEFT JOIN maps m ON m.id = p.map_id WHERE m.id IS NULL").fetchone()["n"]),
            "fragments_without_map": int(self.db.conn.execute("SELECT COUNT(*) n FROM map_fragments f LEFT JOIN maps m ON m.id = f.map_id WHERE m.id IS NULL").fetchone()["n"]),
        }

    def _descendant_source_ids(self, root_id: str) -> set[str]:
        return descendant_source_ids(self.db, root_id)

    def _smoke_overlays(self) -> list[dict[str, Any]]:
        return run_smoke_overlays(self.db, self.root / "debug")

    def _publish(self) -> dict[str, Any]:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        dest = SNAPSHOTS / self.snapshot_id
        if dest.exists():
            raise SystemExit(f"snapshot already exists: {dest}")
        counts = self.db.counts()
        self.db.close()
        self.db = None
        self.root.replace(dest)
        self.root = dest
        current = {
            "snapshot_id": self.snapshot_id,
            "path": f"snapshots/{self.snapshot_id}",
            "core_db": f"snapshots/{self.snapshot_id}/core.db",
        }
        write_json(dest / "manifest.json", {**current, "status": self.status.get("state"), "counts": counts, "registry": {
            "bundle_sha256": self.registry["entry"]["bundle_sha256"],
            "public_api_host": self.registry["hosts"]["public"],
            "app_version": self.registry["client"]["app_version"],
        }})
        tmp = CURRENT_PATH.with_suffix(".json.tmp")
        CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CURRENT_PATH)
        return current


def run_sync(resume: bool = False) -> dict[str, Any]:
    return CoreSync(resume=resume).run()
