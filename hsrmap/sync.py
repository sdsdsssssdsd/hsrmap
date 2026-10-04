"""核心同步（a1-8 / a1-8-1 §六 / §七 / §九 / §十五）。

两阶段 Discovery（§十五）：

* **Pass A — cheap**：`map/tree` + `map/list`（label tree）+ **全部 923 个树节点**的
  `map/info`（含 299 个 `node_type=1` 容器：真名只在容器的 `children[].name` 里）+
  624 张地图的 `point/list`；
* **Pass B — suspicious point expansion**：只对**有 `related_jump_id` 的可疑点位**做
  `point/info`（关键词只用于调度，收录依据仍是 payload / 探测）。

然后跑 §六 的**递归闭包**：每个新 payload 先过 ID Reference Scanner（§八），未认领候选进
frontier；`closure(seeds, expand)` 跑到 `frontier == ()`。`expand` 返回 `None` 表示这次拿不到
证据 —— 记 `unevidenced`，**不假装收敛**。

边落库走 M7.1 的 `save_edges` / `save_point_transitions`（UNIQUE 幂等）；发布前跑 §二十四 的
Map Graph 门禁，gate 不 ok 就**不发布**（`current.json` 不动），退出码 2。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hsrmap.assets import AssetStore
from hsrmap.database import CoreDatabase, has_table
from hsrmap.discovery import (
    SOURCE_LABEL_TREE,
    SOURCE_MAP_INFO,
    SOURCE_MAP_TREE,
    SOURCE_POINT_INFO,
    SOURCE_POINT_LIST,
    aggregate_scans,
    as_map_id,
    extract_map_info,
    extract_point_list,
    is_absent,
    scan_id_references,
)
from hsrmap.graph import (
    PointTransition,
    closure,
    edge_type_counts,
    known_map_ids,
    load_edges,
    load_point_transitions,
    navigable_edges,
    save_edges,
    save_point_transitions,
)
from hsrmap.graph_audit import AuditOptions, audit_graph
from hsrmap.graph_backfill import SnapshotSource, build_backfill_plan, local_map_probe
from hsrmap.http import RateLimitedClient
from hsrmap.inspect_map import compose_map
from hsrmap.jobs import JobStore
from hsrmap.normalize import flatten_label_nodes, flatten_map_nodes, normalize_map_info, normalize_point
from hsrmap.render_probe import probe_summary, refresh_render_probes, tree_map_candidates
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

#: 任务类型（jobs.json 的 job_type）。point_info 是 §十五 的 Pass B。
JOB_MAP_INFO = "map_info"
JOB_POINT_LIST = "point_list"
JOB_POINT_INFO = "point_info"

#: maps.name_source 的两个取值：真名只可能来自 map/info。
NAME_SOURCE_CHILDREN = "map_info:children[].name"
NAME_SOURCE_OWN = "map_info:info.name"

#: 发布门禁（§二十四）在 validation-report 里的 failure id。
GATE_FAILURE = "map_graph_gate"



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


class _RequestCounter:
    """数**真实发出的 HTTP 请求**（报告里要给出抓取请求数，不能靠估）。

    只包一层计数，不碰限速：内层还是同一个 RateLimitedClient 实例，min_interval 照旧生效。
    """

    def __init__(self, client: RateLimitedClient):
        self._client = client
        self.api_requests = 0
        self.asset_requests = 0

    def api_get(self, host: str, path: str, params: dict[str, Any]):
        self.api_requests += 1
        return self._client.api_get(host, path, params)

    def get(self, url: str, timeout: int = 30, headers: dict[str, str] | None = None):
        self.asset_requests += 1
        return self._client.get(url, timeout=timeout, headers=headers)

    def get_unchecked(self, url: str, timeout: int = 30):
        self.asset_requests += 1
        return self._client.get_unchecked(url, timeout=timeout)

    def as_dict(self) -> dict[str, int]:
        return {
            "api": self.api_requests,
            "asset": self.asset_requests,
            "total": self.api_requests + self.asset_requests,
        }


class CoreSync:
    def __init__(self, resume: bool = False):
        self.resume = resume
        self.client = _RequestCounter(RateLimitedClient(min_interval=0.3))
        self.registry = load_registry(REGISTRY_PATH)
        self.assets = AssetStore(ASSETS)
        self.snapshot_id = None
        self.root = None
        self.jobs = None
        self.db = None
        self.log = None
        self.status = {}
        self.missing_assets: list[dict[str, Any]] = []
        #: Pass A 的抓取范围（§十五）：map/info 覆盖**全部树节点**（含容器），
        #: point/list 只覆盖官方标成地图（node_type=2）的结构候选。
        self.map_info_targets: list[str] = []
        self.point_list_targets: list[str] = []
        self._structural_targets: set[str] = set()
        self.suspicious_points: list[str] = []
        #: host / app_version 在 preflight 之后才知道，闭包下探（expand）要用。
        self.host: str | None = None
        self.app_version: str | None = None
        self.started_at = 0.0
        self.graph_report: dict[str, Any] = {}
        self.name_report: dict[str, Any] = {}
        self._payload_cache: dict[tuple[str, str], Any] = {}

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
        self.started_at = time.monotonic()
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
        self.host = host
        self.app_version = app_version
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

        #: **Pass A 的抓取范围**（a1-8-1 §十五 / M7.0）：
        #: - map/info → **全部树节点**（923，含 299 个 node_type=1 容器）。真名只在容器的
        #:   children[].name 里，只抓 624 个可渲染叶子就永远补不上 341 个名字缺口；
        #: - point/list → 官方标成地图的结构候选（624）。容器没有自己的标点层，多抓只会污染
        #:   5330 这个点位不变量。
        #: 「候选」仍然只是「去问哪些 map/info」的起点，不是可渲染判定（§三/§九）。
        candidates = tree_map_candidates(map_nodes)
        unknown = [n for n in map_nodes if n.get("node_type") not in (1, 2)]
        self.map_info_targets = [str(n["source_id"]) for n in map_nodes]
        self.point_list_targets = [str(n["source_id"]) for n in candidates]
        self._structural_targets = set(self.point_list_targets)
        self.status["tree"] = {
            "total": len(map_nodes),
            "tree_leaf": sum(1 for n in map_nodes if n["tree_leaf"]),
            "containers": sum(1 for n in map_nodes if n.get("node_type") == 1),
            "candidates": len(candidates),
            "map_info_targets": len(self.map_info_targets),
            "point_list_targets": len(self.point_list_targets),
            "unknown": len(unknown),
        }
        if unknown:
            self.log.warning("unknown node_types: %s", Counter := {n["node_type"] for n in unknown})

        for source_id in self.map_info_targets:
            self.jobs.enqueue(JOB_MAP_INFO, source_id, persist=False)
        for source_id in self.point_list_targets:
            self.jobs.enqueue(JOB_POINT_LIST, source_id, persist=False)
        self.jobs.save()

        _set_state(status_path, self.status, "FETCH_MAP_INFO")
        self._run_map_jobs(host, app_version, JOB_MAP_INFO, "/v1/map/info", self.root / "raw" / "map_info")
        _set_state(status_path, self.status, "FETCH_POINT_LIST")
        self._run_map_jobs(host, app_version, JOB_POINT_LIST, "/v1/map/point/list", self.root / "raw" / "point_list")

        _set_state(status_path, self.status, "PASS_B_EXPANSION")
        self._run_pass_b(host, app_version)

        _set_state(status_path, self.status, "GRAPH_CLOSURE")
        self.graph_report = self._run_graph_closure()

        _set_state(status_path, self.status, "APPLY_NAMES")
        self.name_report = self._apply_display_names()

        #: map/info 落库之后才谈「可渲染」：探测的证据就是刚落库的 maps + map_fragments（§九）。
        #: 老库回退与 624 的不变量都在这里体现——is_renderable 只由证据写，不从 node_type 推。
        known_ids = {n["source_id"] for n in map_nodes}
        known_ids.update(str(row["source_id"]) for row in self.db.conn.execute("SELECT source_id FROM maps"))
        probes = refresh_render_probes(
            self.db.conn, sorted(known_ids), extra_evidence=self._non_raster_evidence()
        )
        renderable = [n for n in candidates if probes[n["source_id"]].renderable is True]
        self.status["tree"]["renderable"] = len(renderable)
        self.status["tree"]["render_probe"] = probe_summary(probes)
        self.status["graph"] = {
            "edges_total": self.graph_report.get("edges_total"),
            "edges": self.graph_report.get("edges"),
            "point_transitions": self.graph_report.get("point_transitions"),
            "closure": self.graph_report.get("closure"),
            "unclaimed_candidates": len(self.graph_report.get("unclaimed_candidates") or []),
        }
        self.status["naming"] = self.name_report
        write_json(status_path, self.status)

        _set_state(status_path, self.status, "FETCH_ASSETS")
        self._download_assets()

        _set_state(status_path, self.status, "VALIDATE")
        reports = self._validate(candidates, renderable, probes, unknown)
        write_json(self.root / "reports" / "sync-report.json", reports["sync"])
        write_json(self.root / "reports" / "validation-report.json", reports["validation"])
        write_json(self.root / "reports" / "missing-assets.json", self.missing_assets)
        write_json(self.root / "reports" / "point-statistics.json", reports["stats"])
        write_json(self.root / "reports" / "schema-report.json", flight["schema"])
        write_json(self.root / "reports" / "map-graph-report.json", self.graph_report)
        write_json(self.root / "reports" / "naming-report.json", self.name_report)

        return self._decide_publication(reports)

    def _decide_publication(self, reports: dict[str, Any]) -> dict[str, Any]:
        """发布判定（§二十四）。

        - 校验通过 → 原子切换 current.json（_publish）；
        - **Map Graph 门禁不通过 → 不发布、退出码 2**，staging 原样留着可以 --resume；
        - 其它校验失败 → 不发布、退出码 1。

        「不发布」是可测的契约：_publish 一个字节都不写，current.json 保持指向旧快照。
        """
        status_path = self.root / "status.json"
        validation = reports["validation"]
        if validation["passed"]:
            _set_state(status_path, self.status, "READY" if not validation.get("warnings") else "READY_WITH_WARNINGS")
            published = self._publish()
            #: _publish 已经把 staging 整个搬成快照目录，self.root 也换过去了：状态要写到**新家**，
            #: 否则会在 staging 下重新创建一个同名目录（resume 会把它当成一个可续跑的 staging）。
            _set_state(self.root / "status.json", self.status, "PUBLISHED")
            return published
        _set_state(status_path, self.status, "FAILED_VALIDATION")
        if GATE_FAILURE in validation["failures"]:
            reasons = (validation.get("map_graph_gate") or {}).get("reasons") or [GATE_FAILURE]
            print("map graph gate FAILED --- 本次 sync 不发布：", file=sys.stderr)
            for reason in reasons:
                print(f"  - {reason}", file=sys.stderr)
            print(f"  staging: {self.root}", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit("core validation failed")

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

    def _run_map_jobs(self, host, app_version, job_type, path, raw_dir, *, query_key: str = "map_id"):
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
                    resp = self.client.api_get(host, path, _query(app_version, **{query_key: resource_id}))
                    payload = resp.json()
                    digest = _save_raw(dest, payload)
                if payload.get("retcode") != 0:
                    raise ValueError(payload.get("message"))
                #: 容器（不在结构候选里）的 map/info 合法地没有 raster detail → 用它的形状要求；
                #: node_type=2 的候选仍然必须带 detail（缺了会在 _validate 的 missing_map_info 里炸）。
                endpoint = job_type
                if job_type == JOB_MAP_INFO and resource_id not in self._structural_targets:
                    endpoint = "map_info_container"
                check = check_payload(endpoint, payload, self.registry.get("schema_fingerprints", {}).get(endpoint))
                if not check.ok:
                    raise ValueError(f"schema {check.missing}")
                if job_type == JOB_MAP_INFO:
                    #: 容器（node_type=1）与「有 children 的地图」的 map/info **合法地**没有 raster
                    #: detail（§三：node_type 不是可渲染判据）。这类 payload 是**名字与带名边的
                    #: 来源**，不产生 maps 行；node_type=2 的缺口由 _validate 的 missing_map_info
                    #: 逐条兜住——这里放宽的不是检查，是「容器不该被当成坏数据」。
                    try:
                        mapped = normalize_map_info(payload)
                    except ValueError as exc:
                        self.log.info("map_info %s 没有 raster detail（容器 / 非地图节点）：%s", resource_id, exc)
                    else:
                        self.db.insert_map(mapped, map_info_sha256=digest)
                elif job_type == JOB_POINT_LIST:
                    map_row = self.db.map_by_source(resource_id)
                    if map_row is None:
                        raise ValueError("map missing before point/list")
                    points = [
                        normalize_point(p, resource_id, map_row["origin_x"], map_row["origin_y"])
                        for p in ((payload.get("data") or {}).get("point_list") or [])
                    ]
                    self.db.insert_points(points)
                #: JOB_POINT_INFO（Pass B）：只留 raw payload，边与跳转在闭包那一步统一跑
                #: extract_point_info 交叉校验后落库。
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


    # ------------------------------------------------------------------ Pass B
    # §十五 Pass B：只对**可疑点位**做 point/info。关键词只用于调度优先级，
    # 收录依据永远是 payload / bundle / render probe，不是「名字里有没有 JUMP」。
    # ------------------------------------------------------------------ #

    @staticmethod
    def _point_rows(payload: Any) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("point_list") if isinstance(data, dict) else None
        return [row for row in (rows or []) if isinstance(row, dict)]

    def _suspicious_point_ids(self) -> list[str]:
        """有 related_jump_id 的点位 id（§十五 的嫌疑判据 = 存在相关字段）。"""
        found: set[str] = set()
        for map_id in self.point_list_targets:
            payload = self._payload(JOB_POINT_LIST, map_id)
            if payload is None:
                continue
            for point in self._point_rows(payload):
                if is_absent(point.get("related_jump_id")):
                    continue
                point_id = as_map_id(point.get("id"))
                if point_id is not None:
                    found.add(point_id)
        return sorted(found, key=lambda value: int(value))

    def _run_pass_b(self, host, app_version) -> dict[str, Any]:
        self.suspicious_points = self._suspicious_point_ids()
        for point_id in self.suspicious_points:
            self.jobs.enqueue(JOB_POINT_INFO, point_id, persist=False)
        self.jobs.save()
        self._run_map_jobs(
            host, app_version, JOB_POINT_INFO, "/v1/map/point/info", self.root / "raw" / "point_info",
            query_key="point_id",
        )
        fetched = sum(1 for point_id in self.suspicious_points if self._payload(JOB_POINT_INFO, point_id) is not None)
        report = {"suspicious_points": len(self.suspicious_points), "point_info_payloads": fetched}
        self.status["pass_b"] = report
        self.log.info("Pass B: %s 个可疑点位，拿到 %s 份 point/info", report["suspicious_points"], fetched)
        return report

    # ------------------------------------------------------------------ raw payload

    def _payload(self, kind: str, source_id: str) -> Any | None:
        """读一份已经落盘的 raw payload（**retcode 0 才算证据**），带进程内缓存。

        拿不到证据一律返回 None —— 闭包会把它记进 unevidenced，不假装收敛（§六）。
        """
        key = (str(kind), str(source_id))
        if key in self._payload_cache:
            return self._payload_cache[key]
        path = self.root / "raw" / str(kind) / f"{source_id}.json"
        payload: Any | None = None
        if path.is_file():
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                candidate = None
            if isinstance(candidate, dict) and candidate.get("retcode") == 0:
                payload = candidate
        self._payload_cache[key] = payload
        return payload

    def _fetch_payload(self, kind: str, path: str, query_key: str, resource_id: str) -> Any | None:
        """闭包下探（§六）：树外的新地图没有本地证据时，去官方问一次。

        失败 / 非 0 retcode 一律返回 None（= 这次没有证据），**不抛异常、不编数据**。
        """
        if not self.host or not self.app_version:
            return None
        try:
            response = self.client.api_get(self.host, path, _query(self.app_version, **{query_key: resource_id}))
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - 下探失败是正常的「没证据」，不是崩溃
            self.log.warning("%s 下探 %s 失败：%s", kind, resource_id, exc)
            return None
        if not isinstance(payload, dict) or payload.get("retcode") != 0:
            self.log.warning("%s 下探 %s 没有 retcode 0", kind, resource_id)
            return None
        _save_raw(self.root / "raw" / kind / f"{resource_id}.json", payload)
        self._payload_cache[(kind, str(resource_id))] = payload
        self.log.info("闭包下探发现新地图：%s %s", kind, resource_id)
        return payload

    def _non_raster_evidence(self) -> dict[str, Any]:
        """有 map/info 证据、但 payload 里没有 raster detail 的节点（容器 / 非地图节点）。

        §九 的三态里这是 **INVALID**（有证据说它不是一张可渲染地图），不是 UNKNOWN（没问过）：
        M7.3 把 map/info 的抓取范围扩到全部 923 个树节点之后，这 299 个容器不再靠 node_type 猜，
        而是**问过官方**才写下的结论。
        """
        from hsrmap.render_probe import RenderEvidence

        out: dict[str, Any] = {}
        for map_id in self.map_info_targets:
            if self.db.map_by_source(map_id) is not None:
                continue  # 已经落库 → evidence_from_db 自己有完整证据，别覆盖
            payload = self._payload(JOB_MAP_INFO, map_id)
            if payload is None:
                continue  # 真没问到 → 保持 UNKNOWN，不许写成 INVALID
            out[map_id] = RenderEvidence(map_id=map_id, map_info_present=True, raster_fragment_count=0)
        return out

    # ------------------------------------------------------------------ 递归闭包

    def _expand_map(self, map_id: str):
        """§六 的 expand 注入点：返回以 map_id 为 source 的边；None = 这次拿不到证据。

        本地没有 map/info 就下探一次官方（递归闭包必须能发现树外的新地图）。返回 None 时
        closure 会记进 unevidenced、converged=False —— 那是如实报告，不是把失败藏起来。
        """
        info = self._payload(JOB_MAP_INFO, map_id)
        discovered = False
        if info is None:
            info = self._fetch_payload(JOB_MAP_INFO, "/v1/map/info", "map_id", map_id)
            discovered = True
        if info is None:
            return None
        edges = list(extract_map_info(info).edges_from(map_id))
        points = self._payload(JOB_POINT_LIST, map_id)
        if points is None and discovered:
            #: 只有**新发现**的地图才补抓标点层；容器本来就没有自己的 point/list。
            points = self._fetch_payload(JOB_POINT_LIST, "/v1/map/point/list", "map_id", map_id)
        if points is not None:
            edges.extend(extract_point_list(points, map_id).edges_from(map_id))
        return tuple(edges)

    def _scan_reports(self, probe) -> list[Any]:
        """§八：**每一个新 payload** 都先过 ID Reference Scanner。"""
        snapshot = SnapshotSource(self.root)
        reports: list[Any] = [scan_id_references(snapshot.map_tree_payload(), endpoint=SOURCE_MAP_TREE, probe=probe)]
        label_payload = snapshot.label_tree_payload()
        if label_payload is not None:
            reports.append(scan_id_references(label_payload, endpoint=SOURCE_LABEL_TREE, probe=probe))
        for map_id in self.map_info_targets:
            payload = self._payload(JOB_MAP_INFO, map_id)
            if payload is not None:
                reports.append(scan_id_references(payload, endpoint=SOURCE_MAP_INFO, probe=probe))
        for map_id in self.point_list_targets:
            payload = self._payload(JOB_POINT_LIST, map_id)
            if payload is not None:
                reports.append(scan_id_references(payload, endpoint=SOURCE_POINT_LIST, probe=probe))
        for point_id in self.suspicious_points:
            payload = self._payload(JOB_POINT_INFO, point_id)
            if payload is not None:
                reports.append(scan_id_references(payload, endpoint=SOURCE_POINT_INFO, probe=probe))
        return reports

    def _run_graph_closure(self) -> dict[str, Any]:
        """§六 递归闭包 + §八 Scanner + §二十四 门禁输入。

        边与跳转的算法与 M7.2 的离线回填**完全同一套**（build_backfill_plan），这样新快照里的
        1341 / 187 与旁挂图库 data/graph/core.db 逐条对得上；闭包负责证明「可达集合收敛」。
        """
        conn = self.db.conn
        snapshot = SnapshotSource(self.root)
        plan = build_backfill_plan(snapshot, scan=False)
        for note in plan.warnings:
            self.log.warning("回填告警：%s", note)

        probe = local_map_probe(conn)
        reports = self._scan_reports(probe)
        scan = aggregate_scans(reports)
        unclaimed = [item.as_dict() for report in reports for item in report.unclaimed_candidates()]

        #: frontier = 已知地图 ∪ Scanner 的未认领候选（官方加了新机制就靠这条冒出来，§六 / §八）。
        seeds = sorted(known_map_ids(conn) | {str(item["map_id"]) for item in unclaimed})
        result = closure([edge.as_edge() for edge in plan.edges], seeds=seeds, expand=self._expand_map)

        save_edges(conn, [edge.as_edge() for edge in plan.edges])
        save_point_transitions(conn, [item.as_transition() for item in plan.transitions])
        edges = load_edges(conn)
        transitions = load_point_transitions(conn)
        report = {
            "edges_total": len(edges),
            "edges": edge_type_counts(edges),
            "expected": dict(plan.expected),
            "duplicates": dict(plan.duplicates),
            "point_transitions": len(transitions),
            "plan_warnings": list(plan.warnings),
            "scan": scan,
            "unclaimed_candidates": unclaimed,
            "closure": {
                "seeds": len(result.seeds),
                "visited": len(result.visited),
                "steps": result.steps,
                "cycles": len(result.cycles),
                "frontier": list(result.frontier),
                "unevidenced": list(result.unevidenced),
                "converged": bool(result.converged),
            },
            "tree": plan.tree,
            "points": plan.points,
            "map_info": plan.map_info,
        }
        self.log.info(
            "图谱闭包：edges %s / transitions %s / frontier %s / unevidenced %s",
            report["edges_total"], report["point_transitions"], len(result.frontier), len(result.unevidenced),
        )
        return report

    # ------------------------------------------------------------------ 真名

    def _name_sources(self) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
        """(容器 children[].name 表, 地图自己 info.name 表)，值都是 (名字, json 路径)。"""
        children: dict[str, tuple[str, str]] = {}
        own: dict[str, tuple[str, str]] = {}
        for map_id in self.map_info_targets:
            payload = self._payload(JOB_MAP_INFO, map_id)
            if payload is None:
                continue
            for fact in extract_map_info(payload).facts:
                name = str(fact.name or "").strip()
                if not name:
                    continue
                if fact.key == "children[].name":
                    children.setdefault(fact.map_id, (name, fact.json_path))
                else:
                    own[fact.map_id] = (name, fact.json_path)
        return children, own

    def _name_gap(self, *, use_display: bool) -> dict[str, int]:
        """名字缺口（M7.3 的硬数字）。

        use_display=False = 只看**官方树的原始名**（M7.2 的 341/624、142/187 就是这个口径）；
        use_display=True  = 真名（maps.display_name）∪ maps.name ∪ 树名。
        """
        conn = self.db.conn
        named: set[str] = set()
        unnamed = 0
        for row in conn.execute(
            "SELECT m.source_id AS sid, m.display_name AS display_name, m.name AS own_name, n.name AS tree_name"
            " FROM maps m LEFT JOIN map_nodes n ON n.source_id = m.source_id"
        ):
            values = [row["tree_name"]]
            if use_display:
                values = [row["display_name"], row["own_name"], row["tree_name"]]
            if any(str(value or "").strip() for value in values):
                named.add(str(row["sid"]))
            else:
                unnamed += 1
        jump_targets: list[str] = []
        if has_table(conn, "map_edges"):
            jump_targets = [
                str(row["target_map_id"])
                for row in conn.execute("SELECT DISTINCT target_map_id FROM map_edges WHERE edge_type = 'POINT_JUMP'")
            ]
        return {
            "renderable_maps": int(conn.execute("SELECT COUNT(*) n FROM maps").fetchone()["n"]),
            "unnamed_renderables": unnamed,
            "jump_targets": len(jump_targets),
            "unnamed_jump_targets": sum(1 for target in jump_targets if target not in named),
            "tree_nodes_without_name": int(
                conn.execute("SELECT COUNT(*) n FROM map_nodes WHERE COALESCE(TRIM(name), '') = ''").fetchone()["n"]
            ),
        }

    def _apply_display_names(self) -> dict[str, Any]:
        """把容器的 children[].name 写进 maps.display_name（**不覆盖** tree 的原始 name）。

        优先级：children[].name（M7.0：真名的唯一来源）> map/info 自己的 name（兜底）。
        maps.name / map_nodes.name 一个字节都不动。
        """
        before = self._name_gap(use_display=False)
        children, own = self._name_sources()
        conflicts = [
            {"map_id": map_id, "children_name": children[map_id][0], "own_name": own[map_id][0]}
            for map_id in sorted(set(children) & set(own))
            if children[map_id][0] != own[map_id][0]
        ]
        items: list[dict[str, Any]] = []
        for map_id in sorted(set(children) | set(own), key=lambda value: int(value) if str(value).isdigit() else 0):
            if map_id in children:
                name, path = children[map_id]
                source = NAME_SOURCE_CHILDREN
            else:
                name, path = own[map_id]
                source = NAME_SOURCE_OWN
            items.append({"source_id": map_id, "display_name": name, "name_source": source, "json_path": path})
        updated = self.db.update_map_names(items)
        after = self._name_gap(use_display=True)
        by_source: dict[str, int] = {}
        for row in self.db.conn.execute(
            "SELECT name_source, COUNT(*) n FROM maps WHERE display_name IS NOT NULL GROUP BY name_source"
        ):
            by_source[str(row["name_source"])] = int(row["n"])
        report = {
            "display_names_written": updated,
            "names_available": len(items),
            "name_source_counts": dict(sorted(by_source.items())),
            "before": before,
            "after": after,
            "children_vs_own_conflicts_total": len(conflicts),
            "children_vs_own_conflicts": conflicts[:10],
        }
        self.log.info(
            "真名：写入 %s 条；可渲染无名 %s → %s，跳转目标无名 %s → %s",
            updated, before["unnamed_renderables"], after["unnamed_renderables"],
            before["unnamed_jump_targets"], after["unnamed_jump_targets"],
        )
        return report

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

    def _validate(self, candidates, renderable, probes, unknown) -> dict[str, Any]:
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
        #: 「同步下来的地图」与「探测判为可渲染的地图」必须一一对应（§九）：
        #: 少了 = 有 map/info 落库但 raster 无效，多了 = 有地图没同步下来。
        probed_renderable = {map_id for map_id, probe in probes.items() if probe.renderable is True}
        if stats["maps"]["renderable_maps"] != len(probed_renderable):
            failures.append("renderable_count_mismatch")
        #: 初始 frontier 的每个候选都必须有 map/info：否则它既不是 VALID 也不是 INVALID，
        #: 只是「没证据」——那是同步没做完，不是数据正常（§九 的 UNKNOWN 不许当成通过）。
        missing_info = [n["source_id"] for n in candidates if self.db.map_by_source(n["source_id"]) is None]
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
        #: M7.3 的新口径常量也要守（数据没变就还是原值；±20% 之外才算「可疑的数据变化」）。
        counts = self.db.counts()
        for key, column in (
            ("tree_nodes", "map_nodes"),
            ("points", "points"),
            ("map_edges", "map_edges"),
            ("point_transitions", "point_transitions"),
        ):
            want = BASELINE.get(key)
            got = int(counts.get(column, 0))
            if want and abs(got - int(want)) / int(want) > 0.2:
                failures.append(f"SUSPICIOUS_DATA_CHANGE_{key}")
        #: §八：新 payload 里出现 extractor 不认识的候选跳转 = 官方加了新机制。不许静默丢掉。
        unclaimed = self.graph_report.get("unclaimed_candidates") or []
        if unclaimed:
            warnings.append("unclaimed_scan_candidates")
        #: M7.3 的核心交付：可渲染地图的真名。补不上就**如实报还剩多少**，不静默、也不放宽检查。
        unnamed_after = int((self.name_report.get("after") or {}).get("unnamed_renderables") or 0)
        if unnamed_after:
            warnings.append(f"renderable_maps_without_name:{unnamed_after}")
        closure_info = self.graph_report.get("closure") or {}
        if closure_info and not closure_info.get("converged", True):
            #: §六：有 unevidenced / frontier 就是**没收敛**，如实记，不假装。
            warnings.append("graph_closure_not_converged")
        gate = self._map_graph_gate()
        if not gate["ok"]:
            failures.append(GATE_FAILURE)
        smoke = self._smoke_overlays()
        validation = {
            "passed": not failures,
            "failures": failures,
            "warnings": warnings,
            "golden": golden_err,
            "smoke": smoke,
            "orphan_check": self._orphans(),
            "map_graph_gate": gate,
        }
        sync = {
            "snapshot_id": self.snapshot_id,
            "status": "PASS" if validation["passed"] else "FAIL",
            "counts": self.db.counts(),
            "elapsed_seconds": round(time.monotonic() - self.started_at, 1),
            "requests": self.client.as_dict(),
            "graph": {
                "edges_total": self.graph_report.get("edges_total"),
                "edges": self.graph_report.get("edges"),
                "point_transitions": self.graph_report.get("point_transitions"),
                "closure": closure_info,
            },
            "naming": self.name_report,
        }
        return {"validation": validation, "sync": sync, "stats": stats}

    def _map_graph_gate(self) -> dict[str, Any]:
        """§二十四 的发布 invariant + §二十三 的 canary，在**新快照**上跑一次。

        invariant 的口径是 runbook §6.3 定稿的那条：

            forall edge in NAVIGABLE_EDGE_TYPES: edge.target_map_id in synced_renderable_maps

        结构边（TREE_CHILD / FLOOR / RELATED_MAP / MAP_GROUP）的目标本来就是树里的容器节点，
        单独统计、**不算违规**；只有 NAVIGABLE_EDGE_TYPES（POINT_JUMP / PORTAL / RETURN /
        UNKNOWN_TRANSITION）要求 target 是一张已同步的可渲染地图。
        """
        conn = self.db.conn
        edges = load_edges(conn)
        navigable = navigable_edges(edges)
        renderable = known_map_ids(conn, renderable_only=True)
        violations = [
            {
                "source_map_id": edge.source_map_id,
                "target_map_id": edge.target_map_id,
                "edge_type": edge.edge_type,
                "discovery_source": edge.discovery_source,
            }
            for edge in navigable
            if edge.target_map_id not in renderable
        ]
        audit = audit_graph(
            conn,
            options=AuditOptions(
                db_path=str(self.root / "core.db"),
                run_canary=True,
                run_chains=False,
            ),
        )
        reasons: list[str] = []
        if violations:
            reasons.append(
                f"§二十四 invariant 违规：{len(violations)} 条可导航边的 target 不在已同步的可渲染集合里 "
                f"（样本 {[item['target_map_id'] for item in violations[:5]]}）"
            )
        for name, ok in audit["gate"]["checks"].items():
            if not ok:
                reasons.append(f"graph audit gate 不通过：{name}")
        report = {
            "ok": not reasons,
            "reasons": reasons,
            "navigable_edges": len(navigable),
            "navigable_edge_types": sorted({edge.edge_type for edge in navigable}),
            "structural_edge_types": sorted({edge.edge_type for edge in edges} - {edge.edge_type for edge in navigable}),
            "renderable_maps": len(renderable),
            "violations_total": len(violations),
            "violations": violations[:20],
            "audit_gate": audit["gate"],
            "audit_counts": {
                "tree_nodes": audit["tree_nodes"],
                "renderable_maps": audit["renderable_maps"],
                "edges_total": audit["edges_total"],
                "edges": audit["edges"],
                "point_transitions": audit["point_transitions"],
                "unresolved_targets_total": audit["unresolved_targets_total"],
                "unresolved_navigable_targets_total": audit["unresolved_navigable_targets_total"],
                "orphan_renderables_total": audit["orphan_renderables_total"],
                "naming": audit["naming"],
            },
            "canary": audit.get("canary"),
        }
        write_json(self.root / "reports" / "map-graph-gate.json", report)
        return report

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
        write_json(dest / "manifest.json", {
            **current,
            "status": self.status.get("state"),
            "counts": counts,
            "graph": {
                "edges_total": self.graph_report.get("edges_total"),
                "edges": self.graph_report.get("edges"),
                "point_transitions": self.graph_report.get("point_transitions"),
                "closure": self.graph_report.get("closure"),
            },
            "naming": self.name_report,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 1),
            "requests": self.client.as_dict(),
            "registry": {
                "bundle_sha256": self.registry["entry"]["bundle_sha256"],
                "public_api_host": self.registry["hosts"]["public"],
                "app_version": self.registry["client"]["app_version"],
            },
        })
        tmp = CURRENT_PATH.with_suffix(".json.tmp")
        CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CURRENT_PATH)
        return current


def run_sync(resume: bool = False) -> dict[str, Any]:
    return CoreSync(resume=resume).run()
