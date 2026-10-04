"""M7.2 离线回填（a1-8-1 §六 / §十五）：从**现有快照**的 raw payload 算出地图图谱。

**不发任何网络请求、不写冻结快照。** 输入是 `data/snapshots/<id>/`：

    raw/map_tree.json        → TREE_CHILD / RELATED_MAP / MAP_GROUP
    raw/label_tree.json      → 只进 ID Reference Scanner（jump_target_id 要留在 registry）
    raw/map_info/*.json      → 真名 / preview / detail 事实 + 交叉校验（本快照 related_id 全 0）
    raw/point_list/*.json    → POINT_JUMP（related_jump_id）
    core.db (只读)           → points.raw_json 交叉校验 + points 主键（point_transitions 要用）

输出**永远写到一个显式指定的新库**（默认 `<data>/graph/core.db`），默认 dry-run：
不显式 `--write` 就只打印将要写多少条边、按类型与 discovery_source 的分布。

三条硬约束在这里体现成代码：

1. 源库只读打开（`immutable=1`），并且回填前后各记一次 sha256 —— `source_untouched` 是
   报告字段，不是口头承诺；
2. 输出路径落在任何 `snapshots/` 目录里一律拒绝（`FrozenSnapshotError`，CLI 退 2）；
3. 落库走 M7.1 的 `save_edges` / `save_point_transitions`（UNIQUE 幂等）：同一批边写两次，
   表里的条数不变。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from hsrmap.database import CoreDatabase
from hsrmap.discovery import (
    CandidateEdge,
    CandidateTransition,
    Extraction,
    MapProbeResult,
    PROBE_MAP_LIKE,
    PROBE_RENDERABLE,
    PROBE_UNKNOWN,
    SOURCE_LABEL_TREE,
    SOURCE_MAP_INFO,
    SOURCE_MAP_TREE,
    SOURCE_POINT_LIST,
    ScanReport,
    aggregate_scans,
    as_map_id,
    dump_json,
    extract_map_info,
    extract_point_list,
    extract_tree,
    is_absent,
    scan_id_references,
)
from hsrmap.graph import (
    MAP_GROUP,
    POINT_JUMP,
    RELATED_MAP,
    TREE_CHILD,
    Edge,
    PointTransition,
    edge_type_counts,
    now_iso,
    save_edges,
    save_point_transitions,
)
from hsrmap.normalize import flatten_map_nodes
from hsrmap.render_probe import VALID, probe_map_renderability

#: 默认输出库相对运行目录的位置。
GRAPH_DIR_NAME = "graph"
DEFAULT_OUT_NAME = "core.db"

#: M7.0 侦察报告里的产量基线（docs/runbooks/map-graph-m7.md §2）。对不上要在报告里解释。
EXPECTED_EDGE_COUNTS: Mapping[str, int] = {
    TREE_CHILD: 914,
    RELATED_MAP: 204,
    MAP_GROUP: 36,
    POINT_JUMP: 187,
}

#: 去重优先级：同一条边被多层 payload 发现时，保留**最先命中**的那一层的出处。
SOURCE_PRIORITY: tuple[str, ...] = (SOURCE_MAP_TREE, SOURCE_POINT_LIST, SOURCE_MAP_INFO)


class FrozenSnapshotError(RuntimeError):
    """想写冻结快照 / 交付镜像时的拒绝（CLI 退 2，不是执行错误）。"""


class BackfillError(RuntimeError):
    """回填跑不下去（缺快照、缺库、输出不是数据库……）：CLI 退 1。"""


# --------------------------------------------------------------------------- #
# 快照定位
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotSource:
    """一个快照目录的只读视图。"""

    root: Path

    @property
    def core_db(self) -> Path:
        return self.root / "core.db"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def map_tree_path(self) -> Path:
        return self.raw_dir / "map_tree.json"

    @property
    def label_tree_path(self) -> Path:
        return self.raw_dir / "label_tree.json"

    @property
    def map_info_dir(self) -> Path:
        return self.raw_dir / "map_info"

    @property
    def point_list_dir(self) -> Path:
        return self.raw_dir / "point_list"

    def exists(self) -> bool:
        return self.core_db.is_file()

    def load_json(self, path: Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def map_tree_payload(self) -> Any:
        if not self.map_tree_path.is_file():
            raise BackfillError(f"快照里没有 {self.map_tree_path}")
        return self.load_json(self.map_tree_path)

    def label_tree_payload(self) -> Any | None:
        return self.load_json(self.label_tree_path) if self.label_tree_path.is_file() else None

    def iter_map_info(self) -> Iterator[tuple[str, Any]]:
        if not self.map_info_dir.is_dir():
            return
        for path in sorted(self.map_info_dir.glob("*.json")):
            yield path.stem, self.load_json(path)

    def iter_point_list(self) -> Iterator[tuple[str, Any]]:
        if not self.point_list_dir.is_dir():
            return
        for path in sorted(self.point_list_dir.glob("*.json")):
            yield path.stem, self.load_json(path)

    def point_list_available(self) -> bool:
        return self.point_list_dir.is_dir() and any(self.point_list_dir.glob("*.json"))

    def open_readonly(self) -> CoreDatabase:
        """只读 + immutable 打开源库：物理上写不进去，也不可能顺手补 schema 迁移。"""
        if not self.core_db.is_file():
            raise BackfillError(f"快照里没有 {self.core_db}")
        return CoreDatabase(self.core_db, readonly=True, immutable=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": str(self.root),
            "core_db": str(self.core_db),
            "core_db_sha256": sha256_file(self.core_db) if self.core_db.is_file() else None,
        }


def data_dir_path(data_dir: str | Path | None = None) -> Path:
    """运行目录：显式 → HSRMAP_DATA_DIR → 仓库 data/ → 用户目录（与 hsrmap.runtime 同规则）。"""
    if data_dir not in (None, ""):
        return Path(data_dir).expanduser().resolve()
    from hsrmap.runtime import resolve_runtime

    return resolve_runtime().root


def snapshot_roots(data_dir: str | Path | None = None) -> tuple[Path, ...]:
    """所有「快照区」根目录：运行目录下的 snapshots/ + 仓库 data/snapshots/（都拒绝写）。"""
    from hsrmap.paths import ROOT

    candidates = [data_dir_path(data_dir) / "snapshots", Path(ROOT) / "data" / "snapshots"]
    out: list[Path] = []
    for item in candidates:
        resolved = item.resolve()
        if resolved not in out:
            out.append(resolved)
    return tuple(out)


def resolve_snapshot(spec: str | Path | None = None, *, data_dir: str | Path | None = None) -> SnapshotSource:
    """解析 `--snapshot`：目录 / 目录下的 core.db / 快照 id；空则读 current.json。"""
    if spec not in (None, ""):
        candidate = Path(spec).expanduser()
        if candidate.is_dir():
            return SnapshotSource(candidate.resolve())
        if candidate.is_file():
            return SnapshotSource(candidate.resolve().parent)
        by_id = data_dir_path(data_dir) / "snapshots" / str(spec)
        if by_id.is_dir():
            return SnapshotSource(by_id.resolve())
        raise BackfillError(f"找不到快照：{spec}")

    base = data_dir_path(data_dir)
    current = base / "current.json"
    if current.is_file():
        payload = json.loads(current.read_text(encoding="utf-8"))
        snapshot_id = str(payload.get("snapshot_id") or "")
        if snapshot_id:
            root = base / "snapshots" / snapshot_id
            if root.is_dir():
                return SnapshotSource(root.resolve())
    snapshots = base / "snapshots"
    if snapshots.is_dir():
        found = sorted((item for item in snapshots.iterdir() if (item / "core.db").is_file()), key=lambda p: p.name)
        if found:
            return SnapshotSource(found[-1].resolve())
    raise BackfillError(f"没有可用快照：{base} 下既没有 current.json 也没有 snapshots/<id>/core.db")


def default_out_path(data_dir: str | Path | None = None) -> Path:
    return data_dir_path(data_dir) / GRAPH_DIR_NAME / DEFAULT_OUT_NAME


def assert_writable_out(
    out: str | Path,
    *,
    source_db: str | Path,
    data_dir: str | Path | None = None,
) -> Path:
    """输出库的守卫：冻结快照 / 源库自己，一律拒绝（`FrozenSnapshotError`）。"""
    target = Path(out).expanduser().resolve()
    source = Path(source_db).expanduser().resolve()
    if target == source:
        raise FrozenSnapshotError(f"输出库不能就是源库本身：{target}")
    for root in snapshot_roots(data_dir):
        if target == root or root in target.parents:
            raise FrozenSnapshotError(
                f"拒绝写快照区：{target} 落在 {root} 里。冻结快照是只读文物（连字节都不许变），"
                f"请把 --out 指到一个新路径"
            )
    return target


# --------------------------------------------------------------------------- #
# 离线探测通道（§八 的「必须探测不能猜」在离线语境下的实现）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IdSpaceIndex:
    """本地已知的几个 id 空间：探测「这个数字是什么」的现场证据。"""

    maps: frozenset[str] = frozenset()
    tree: frozenset[str] = frozenset()
    labels: frozenset[str] = frozenset()
    points: frozenset[str] = frozenset()

    def overlaps(self, map_id: str) -> tuple[str, ...]:
        """这个数字同时出现在哪些别的 id 空间里（M7.0：180/187 与 label id 重叠）。"""
        found = []
        if map_id in self.maps:
            found.append("maps(可渲染地图)")
        if map_id in self.tree:
            found.append("map_nodes(树节点)")
        if map_id in self.labels:
            found.append("label_nodes(label id)")
        if map_id in self.points:
            found.append("points(point id)")
        return tuple(found)


def _table_ids(conn: sqlite3.Connection, table: str, column: str) -> frozenset[str]:
    from hsrmap.database import has_table

    if not has_table(conn, table):
        return frozenset()
    return frozenset(str(row[0]) for row in conn.execute(f"SELECT {column} FROM {table}"))


def load_id_space(conn: sqlite3.Connection) -> IdSpaceIndex:
    """本地 id 空间索引（4 条 SQL，不做逐点查询）。"""
    maps = _table_ids(conn, "maps", "source_id")
    tree = _table_ids(conn, "map_nodes", "source_id")
    labels = _table_ids(conn, "label_nodes", "source_id")
    points = _table_ids(conn, "points", "source_id")
    return IdSpaceIndex(maps=maps, tree=tree, labels=labels, points=points)


def local_map_probe(conn: sqlite3.Connection, *, id_space: IdSpaceIndex | None = None) -> Callable[[str], MapProbeResult]:
    """用**已经落库的证据**造一个探测通道（M7.1 的 `probe_map_renderability`）。

    - 在 `maps` 里且 raster 有效 → RENDERABLE；
    - 在 `map_nodes` 里但没有 raster 证据 → MAP_LIKE；
    - 本地没有这个 id 的任何证据 → **UNKNOWN**，不是 NOT_MAP：没问过 ≠ 不是地图。

    `NOT_MAP` 只会来自「键语义 + 其它 id 空间命中」这一侧（见 discovery._classify），
    离线证据不足时绝不猜。
    """
    probes = probe_map_renderability(conn)
    space = id_space if id_space is not None else load_id_space(conn)
    renderable = {map_id for map_id, probe in probes.items() if probe.state == VALID}
    source = "core.db:maps+map_fragments"

    def probe(map_id: str) -> MapProbeResult:
        wanted = str(map_id)
        overlaps = space.overlaps(wanted)
        if wanted in renderable:
            return MapProbeResult(
                wanted,
                PROBE_RENDERABLE,
                ("core.db 的 maps 表里有它，且 map_fragments 有带 url 的切片",),
                source,
                overlaps,
            )
        if wanted in space.tree:
            return MapProbeResult(
                wanted,
                PROBE_MAP_LIKE,
                ("在 map_nodes（官方树）里，但本地没有它的 map/info raster 证据",),
                source,
                overlaps,
            )
        if wanted in probes:
            return MapProbeResult(
                wanted,
                PROBE_MAP_LIKE,
                ("在 maps 里但 raster 证据不完整（不是可渲染地图）",),
                source,
                overlaps,
            )
        return MapProbeResult(
            wanted,
            PROBE_UNKNOWN,
            ("本地没有任何关于这个 id 的证据：没问过 ≠ 不是地图（M7.3 才有网络证据）",),
            source,
            overlaps,
        )

    return probe


# --------------------------------------------------------------------------- #
# 回填计划
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BackfillPlan:
    """一次回填的全部产物 + 证据（dry-run 与 --write 共用同一份计划）。"""

    snapshot: str
    source_db: str
    source_sha256: str
    edges: tuple[CandidateEdge, ...] = ()
    transitions: tuple[CandidateTransition, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    expected: Mapping[str, int] = field(default_factory=dict)
    by_source: Mapping[str, int] = field(default_factory=dict)
    duplicates: Mapping[str, int] = field(default_factory=dict)
    samples: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    tree: Mapping[str, Any] = field(default_factory=dict)
    map_info: Mapping[str, Any] = field(default_factory=dict)
    points: Mapping[str, Any] = field(default_factory=dict)
    scan: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def expected_match(self) -> bool:
        return all(int(self.counts.get(key, 0)) == int(value) for key, value in self.expected.items())

    @property
    def expected_delta(self) -> dict[str, dict[str, int]]:
        return {
            key: {"actual": int(self.counts.get(key, 0)), "expected": int(value),
                  "delta": int(self.counts.get(key, 0)) - int(value)}
            for key, value in self.expected.items()
        }

    def as_edges(self, discovered_at: str | None = None) -> list[Edge]:
        return [edge.as_edge(discovered_at) for edge in self.edges]

    def as_transitions(self) -> list[PointTransition]:
        return [item.as_transition() for item in self.transitions]

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "source_db": self.source_db,
            "source_sha256": self.source_sha256,
            "edges": dict(self.counts),
            "expected": dict(self.expected),
            "expected_match": self.expected_match,
            "expected_delta": self.expected_delta,
            "by_discovery_source": dict(self.by_source),
            "duplicates_dropped": dict(self.duplicates),
            "edges_total": len(self.edges),
            "transitions_total": len(self.transitions),
            "tree": dict(self.tree),
            "map_info": dict(self.map_info),
            "points": dict(self.points),
            "scan": dict(self.scan),
            "samples": {key: list(value) for key, value in self.samples.items()},
            "warnings": list(self.warnings),
            "diagnostics": list(self.diagnostics[:40]),
        }

    def render(self) -> str:
        """人话摘要（dry-run 与 --write 都先打这个）。"""
        lines = [
            "Map Graph 离线回填计划（M7.2，不联网）",
            f"  快照 ............ {self.snapshot}",
            f"  源库 ............ {self.source_db}",
            f"  源库 sha256 ..... {self.source_sha256[:16]}…",
            "",
            "  将要写入的边（按类型）：",
        ]
        for edge_type, expected in self.expected.items():
            actual = int(self.counts.get(edge_type, 0))
            mark = "OK" if actual == expected else "≠"
            lines.append(f"    {edge_type:<20} {actual:>5}  （M7.0 基线 {expected}） {mark}")
        others = {key: value for key, value in self.counts.items() if key not in self.expected}
        for edge_type, actual in sorted(others.items()):
            lines.append(f"    {edge_type:<20} {actual:>5}")
        lines.append(f"    {'合计':<20} {len(self.edges):>5}")
        lines.append(f"  点位跳转 point_transitions {len(self.transitions)}")
        lines.append("")
        lines.append("  出处分布（discovery_source）：")
        for source, count in sorted(self.by_source.items()):
            lines.append(f"    {source:<16} {count:>5}")
        if self.duplicates:
            lines.append("  多层 payload 重复发现（只留一条，按 map_tree > point_list > map_info 优先）：")
            for source, count in sorted(self.duplicates.items()):
                lines.append(f"    {source:<16} {count:>5}")
        lines.append("")
        lines.append("  交叉校验：")
        for key, value in self.tree.items():
            lines.append(f"    tree.{key} = {value}")
        for key, value in self.map_info.items():
            lines.append(f"    map_info.{key} = {value}")
        for key, value in self.points.items():
            lines.append(f"    points.{key} = {value}")
        scan = self.scan or {}
        if scan:
            by_conclusion = scan.get("by_conclusion") or {}
            lines.append(
                "    ID Scanner: "
                + ", ".join(f"{name}={count}" for name, count in by_conclusion.items())
                + f"（过滤掉的「无」值 {scan.get('skipped_absent', 0)}）"
            )
            unclaimed = scan.get("unclaimed_candidates") or []
            lines.append(f"    未认领的候选跳转（字段语义未知但探测到是地图）：{len(unclaimed)}")
        if self.warnings:
            lines.append("")
            lines.append("  警告：")
            for note in self.warnings:
                lines.append(f"    ! {note}")
        return "\n".join(lines) + "\n"


def _group_points_by_map(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """points.raw_json 按地图 source_id 归组（`related_jump_id` 就在 raw_json 里）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    sql = """
        SELECT m.source_id AS map_source_id, p.raw_json AS raw_json
        FROM points p JOIN maps m ON m.id = p.map_id
        ORDER BY m.source_id, p.id
    """
    for row in conn.execute(sql):
        try:
            payload = json.loads(row["raw_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping):
            grouped.setdefault(str(row["map_source_id"]), []).append(dict(payload))
    return grouped


def _point_pk_index(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """(map source_id, point source_id) → points.id（point_transitions 的主键）。"""
    sql = """
        SELECT m.source_id AS map_source_id, p.source_id AS point_source_id, p.id AS pk
        FROM points p JOIN maps m ON m.id = p.map_id
    """
    return {(str(row["map_source_id"]), str(row["point_source_id"])): int(row["pk"]) for row in conn.execute(sql)}


def _dedupe_edges(candidates: Iterable[CandidateEdge]) -> tuple[tuple[CandidateEdge, ...], dict[str, int]]:
    """按 map_edges 的 UNIQUE 键去重，保留最先命中的出处；返回 (边, 各层被丢掉的条数)。"""
    seen: dict[tuple[str, str, str, str | None], CandidateEdge] = {}
    dropped: Counter[str] = Counter()
    ordered = sorted(candidates, key=lambda edge: SOURCE_PRIORITY.index(edge.discovery_source)
                     if edge.discovery_source in SOURCE_PRIORITY else len(SOURCE_PRIORITY))
    for edge in ordered:
        if edge.key_tuple in seen:
            dropped[edge.discovery_source] += 1
            continue
        seen[edge.key_tuple] = edge
    return tuple(seen.values()), dict(dropped)


def build_backfill_plan(
    snapshot: SnapshotSource,
    *,
    bundle_path: str | Path | None = None,
    samples: int = 2,
    scan: bool = True,
    expected: Mapping[str, int] | None = None,
) -> BackfillPlan:
    """从快照算出边 / 跳转 / 事实，**不写任何东西**。

    `expected` 是产量基线：缺省用 M7.0 侦察报告的 `EXPECTED_EDGE_COUNTS`（那只对
    20261001T105105Z 有意义）；跑别的快照 / 测试夹具时传自己的数字，别拿旧基线吓自己。
    """
    if not snapshot.exists():
        raise BackfillError(f"快照不可用（缺 core.db）：{snapshot.root}")

    source_sha = sha256_file(snapshot.core_db)
    db = snapshot.open_readonly()
    diagnostics: list[str] = []
    warnings: list[str] = []
    try:
        # ---- 1) 树 ---------------------------------------------------------- #
        tree_payload = snapshot.map_tree_payload()
        tree_extraction = extract_tree(tree_payload)
        diagnostics.extend(tree_extraction.diagnostics)

        flat_nodes = flatten_map_nodes((tree_payload.get("data") or {}).get("tree") or [])
        normalize_children = {
            (str(node["parent_source_id"]), str(node["source_id"]))
            for node in flat_nodes
            if node["parent_source_id"] is not None
        }
        extractor_children = {
            (edge.source_map_id, edge.target_map_id)
            for edge in tree_extraction.edges
            if edge.edge_type == TREE_CHILD
        }
        tree_report = {
            "nodes": len(flat_nodes),
            "tree_leaf": sum(1 for node in flat_nodes if node["tree_leaf"]),
            "tree_child_from_normalize": len(normalize_children),
            "tree_child_from_extractor": len(extractor_children),
            "normalize_matches_extractor": normalize_children == extractor_children,
            "related_id": sum(1 for edge in tree_extraction.edges if edge.edge_type == RELATED_MAP),
            "related_group_map": sum(1 for edge in tree_extraction.edges if edge.edge_type == MAP_GROUP),
        }
        if not tree_report["normalize_matches_extractor"]:
            warnings.append(
                "extract_tree 的 TREE_CHILD 与 hsrmap.normalize.flatten_map_nodes 对不上："
                f"{sorted(extractor_children ^ normalize_children)[:5]}"
            )

        # ---- 2) map/info（真名 + 交叉校验） --------------------------------- #
        map_info_files = 0
        map_info_named = 0
        map_info_missing_name = 0
        map_info_facts = 0
        map_info_extractions: list[Extraction] = []
        for _map_id, payload in snapshot.iter_map_info():
            extraction = extract_map_info(payload)
            map_info_extractions.append(extraction)
            map_info_files += 1
            map_info_facts += len(extraction.facts)
            for fact in extraction.facts:
                if fact.key != "info":  # 只数「这张图自己」的事实，不数 children 条目
                    continue
                if fact.name_missing:
                    map_info_missing_name += 1
                else:
                    map_info_named += 1
            diagnostics.extend(extraction.diagnostics[:1])
        map_info_edges = [edge for extraction in map_info_extractions for edge in extraction.edges]
        map_info_report = {
            "files": map_info_files,
            "facts": map_info_facts,
            "named": map_info_named,
            "missing_name": map_info_missing_name,
            "edges": len(map_info_edges),
            "note": "本快照 624 张已同步地图的 map/info 都不带 children；容器 info 只在 live 上拿得到（M7.3）",
        }

        # ---- 3) point/list -------------------------------------------------- #
        point_pk = _point_pk_index(db.conn)
        grouped = _group_points_by_map(db.conn)
        point_extractions: list[Extraction] = []
        payload_sources: list[str] = []
        if snapshot.point_list_available():
            raw_iter = list(snapshot.iter_point_list())
            payload_sources.append("raw/point_list/*.json")
            for map_id, payload in raw_iter:
                point_extractions.append(extract_point_list(payload, str(map_id)))
        if not snapshot.point_list_available():
            #: raw 文件优先；DB raw_json 用来交叉校验（两边应当逐条一致）。
            payload_sources.append("points.raw_json")
            for map_id, rows in grouped.items():
                point_extractions.append(extract_point_list({"data": {"point_list": rows}}, str(map_id)))
        if not payload_sources:
            payload_sources.append("points.raw_json")

        point_edges = [edge for extraction in point_extractions for edge in extraction.edges]
        point_transitions: list[CandidateTransition] = []
        missing_pk = 0
        for extraction in point_extractions:
            #: 跳转本身只有 point source id；它所属的地图从同一份 payload 的边上拿，
            #: 这样 (map, point) → points.id 的查找是有据可依的，不靠「point id 全局唯一」这个假设。
            owner = {
                str(edge.source_point_id): edge.source_map_id
                for edge in extraction.edges
                if edge.source_point_id is not None
            }
            for item in extraction.transitions:
                map_id = owner.get(str(item.source_point_id))
                pk = None if map_id is None else point_pk.get((str(map_id), str(item.source_point_id)))
                if pk is None:
                    missing_pk += 1
                    continue
                point_transitions.append(replace(item, point_id=pk))
        if missing_pk:
            warnings.append(f"{missing_pk} 条跳转的 point 不在 points 表里：transition 建不起来但边保留")
        diagnostics.extend(note for extraction in point_extractions for note in extraction.diagnostics[:1])

        # ---- 4) 交叉校验：raw/point_list vs points.raw_json ------------------ #
        db_jumps = {
            (str(map_id), str(as_map_id(row.get("id"))), str(as_map_id(row.get("related_jump_id"))))
            for map_id, rows in grouped.items()
            for row in rows
            if not is_absent(row.get("related_jump_id")) and as_map_id(row.get("related_jump_id")) is not None
        }
        raw_jumps = {
            (edge.source_map_id, str(edge.source_point_id), edge.target_map_id) for edge in point_edges
        }
        points_report = {
            "payload_sources": ", ".join(payload_sources),
            "points_in_db": sum(len(rows) for rows in grouped.values()),
            "jump_targets_from_db": len(db_jumps),
            "jump_targets_from_payload": len(raw_jumps),
            "jump_targets_agree": db_jumps == raw_jumps,
            "point_transitions": len(point_transitions),
        }
        if snapshot.point_list_available() and not points_report["jump_targets_agree"]:
            warnings.append(
                "raw/point_list 与 points.raw_json 的跳转不一致："
                f"only_db={sorted(db_jumps - raw_jumps)[:3]} only_payload={sorted(raw_jumps - db_jumps)[:3]}"
            )

        # ---- 5) 去重 + 计数 ------------------------------------------------- #
        all_edges = [*tree_extraction.edges, *point_edges, *map_info_edges]
        edges, duplicates = _dedupe_edges(all_edges)
        if duplicates:
            diagnostics.append(f"多层 payload 重复发现：{duplicates}（已按优先级去重）")
        counts = edge_type_counts(edges)
        by_source = dict(sorted(Counter(edge.discovery_source for edge in edges).items()))

        # ---- 6) ID Reference Scanner（§八） ---------------------------------- #
        scan_report: dict[str, Any] = {}
        if scan:
            probe = local_map_probe(db.conn)
            reports: list[ScanReport] = [
                scan_id_references(tree_payload, endpoint=SOURCE_MAP_TREE, probe=probe),
            ]
            label_payload = snapshot.label_tree_payload()
            if label_payload is not None:
                #: label/tree 的 parent_id 是 label 空间（同名不同空间），扫描时按 payload 覆盖。
                reports.append(scan_id_references(label_payload, endpoint=SOURCE_LABEL_TREE, probe=probe))
            for _map_id, payload in snapshot.iter_map_info():
                reports.append(scan_id_references(payload, endpoint=SOURCE_MAP_INFO, probe=probe))
            for map_id, rows in grouped.items():
                reports.append(
                    scan_id_references(
                        {"data": {"point_list": rows}}, endpoint=SOURCE_POINT_LIST, probe=probe
                    )
                )
            scan_report = aggregate_scans(reports, examples=samples)

        # ---- 7) bundle 契约（可选） ------------------------------------------ #
        bundle_report: dict[str, Any] = {}
        if bundle_path not in (None, ""):
            from hsrmap.discovery import bundle_key_contracts, extract_bundle_contract

            text = Path(bundle_path).read_text(encoding="utf-8", errors="replace")
            bundle = extract_bundle_contract(text)
            bundle_report = {
                "path": str(bundle_path),
                "bytes": len(text),
                "found": [hit.symbol for hit in bundle.contracts if hit.found],
                "missing": [hit.symbol for hit in bundle.contracts if not hit.found],
                "constants": [item.as_dict() for item in bundle.constants],
                "contracts": bundle_key_contracts(bundle),
                "diagnostics": list(bundle.diagnostics),
            }

        samples_out: dict[str, tuple[dict[str, Any], ...]] = {}
        for edge_type in EXPECTED_EDGE_COUNTS:
            picked = tuple(edge.as_dict() for edge in edges if edge.edge_type == edge_type)[:samples]
            samples_out[edge_type] = picked

        baseline = dict(EXPECTED_EDGE_COUNTS if expected is None else expected)
        for edge_type, want in baseline.items():
            got = int(counts.get(edge_type, 0))
            if got != want:
                warnings.append(
                    f"{edge_type} 实测 {got} 条，与 M7.0 侦察基线 {want} 不一致（差 {got - want}）"
                )
        return BackfillPlan(
            snapshot=str(snapshot.root),
            source_db=str(snapshot.core_db),
            source_sha256=source_sha,
            edges=edges,
            transitions=tuple(point_transitions),
            counts=counts,
            expected=baseline,
            by_source=by_source,
            duplicates=duplicates,
            samples=samples_out,
            tree=tree_report,
            map_info=map_info_report,
            points=points_report,
            scan={**scan_report, **({"bundle": bundle_report} if bundle_report else {})},
            warnings=tuple(warnings),
            diagnostics=tuple(diagnostics),
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #


def write_backfill(
    plan: BackfillPlan,
    *,
    out: str | Path,
    data_dir: str | Path | None = None,
    force: bool = False,
    discovered_at: str | None = None,
) -> dict[str, Any]:
    """把计划写进**输出库**（不是快照）。返回实测结果（条数从库里重新数出来）。"""
    target = assert_writable_out(out, source_db=plan.source_db, data_dir=data_dir)
    source = Path(plan.source_db)
    target.parent.mkdir(parents=True, exist_ok=True)

    mode = "copy"
    if target.exists() and not force:
        #: 已有的图谱库：就地增量写入（幂等 upsert），这样「写两次条数不变」是可验证的。
        mode = "in-place"
        try:
            conn = sqlite3.connect(target)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            conn.close()
        except sqlite3.DatabaseError as exc:
            raise BackfillError(f"{target} 已存在且不是 SQLite 数据库：{exc}（用 --force 覆盖）") from exc
    if mode == "copy":
        shutil.copy2(source, target)

    before_sha = sha256_file(source)
    try:
        db = CoreDatabase(target)  # 加性迁移：只补新表 / 新列
        try:
            stamp = discovered_at or now_iso()
            save_edges(db.conn, plan.as_edges(stamp))
            save_point_transitions(db.conn, plan.as_transitions())
            #: §6.11 第 1 步：树里有 350 个节点没名字（或只有占位符「特殊房间」），真名在**父容器**的
            #: map/info → children[].name 里。顺手收进**派生库**的 node_display_names 表 ——
            #: 只写这张派生表，core schema 与判定层（_map_path 仍用 map_nodes.name）都不动。
            names_written = 0
            raw_dir = source.parent / "raw" / "map_info"
            if raw_dir.is_dir():
                from hsrmap.graph_names import collect as collect_names, write as write_names

                names_written = write_names(db.conn, collect_names(raw_dir))
            counts = {
                "map_edges": int(db.conn.execute("SELECT COUNT(*) FROM map_edges").fetchone()[0]),
                "point_transitions": int(db.conn.execute("SELECT COUNT(*) FROM point_transitions").fetchone()[0]),
            }
            by_type = {
                str(row[0]): int(row[1])
                for row in db.conn.execute("SELECT edge_type, COUNT(*) FROM map_edges GROUP BY edge_type ORDER BY edge_type")
            }
            by_source = {
                str(row[0]): int(row[1])
                for row in db.conn.execute(
                    "SELECT discovery_source, COUNT(*) FROM map_edges GROUP BY discovery_source ORDER BY discovery_source"
                )
            }
            orphan_transitions = int(
                db.conn.execute(
                    """
                    SELECT COUNT(*) FROM point_transitions t
                    LEFT JOIN points p ON p.id = t.point_id
                    WHERE p.id IS NULL
                    """
                ).fetchone()[0]
            )
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        raise BackfillError(f"写 {target} 失败：{exc}") from exc

    after_sha = sha256_file(source)
    return {
        "out": str(target),
        "mode": mode,
        "written_at": discovered_at or now_iso(),
        "map_edges": counts["map_edges"],
        "point_transitions": counts["point_transitions"],
        "by_type": by_type,
        "by_discovery_source": by_source,
        "orphan_transitions": orphan_transitions,
        "node_display_names": names_written,
        "source_db": str(source),
        "source_sha256_before": before_sha,
        "source_sha256_after": after_sha,
        "source_untouched": before_sha == after_sha,
        "plan_edges": len(plan.edges),
        "plan_transitions": len(plan.transitions),
    }


def render_write_result(result: Mapping[str, Any]) -> str:
    lines = [
        "Map Graph 离线回填（已写入）",
        f"  输出库 .......... {result['out']}（{result['mode']}）",
        f"  map_edges ....... {result['map_edges']}",
        f"  point_transitions {result['point_transitions']}",
        f"  节点真名 ........ {result.get('node_display_names', 0)}",
        "  按类型：" + ", ".join(f"{key}={value}" for key, value in sorted((result.get("by_type") or {}).items())),
        "  按出处：" + ", ".join(
            f"{key}={value}" for key, value in sorted((result.get("by_discovery_source") or {}).items())
        ),
        f"  源库没被改 ....... {result['source_untouched']}（sha256 {result['source_sha256_after'][:16]}…）",
    ]
    if result.get("orphan_transitions"):
        lines.append(f"  ! point_transitions 里指不到 points 的条数：{result['orphan_transitions']}")
    return "\n".join(lines) + "\n"


__all__ = [
    "BackfillError",
    "BackfillPlan",
    "DEFAULT_OUT_NAME",
    "EXPECTED_EDGE_COUNTS",
    "FrozenSnapshotError",
    "GRAPH_DIR_NAME",
    "IdSpaceIndex",
    "SOURCE_PRIORITY",
    "SnapshotSource",
    "assert_writable_out",
    "build_backfill_plan",
    "data_dir_path",
    "default_out_path",
    "dump_json",
    "load_id_space",
    "local_map_probe",
    "render_write_result",
    "resolve_snapshot",
    "sha256_file",
    "snapshot_roots",
    "write_backfill",
]
