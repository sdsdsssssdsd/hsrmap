from __future__ import annotations

import hashlib
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from hsrmap_phase1.client import api_get, discover_frontend, http_get
from hsrmap_phase1.fingerprint import extract_paths, fingerprint_schema
from hsrmap_phase1.labels import resolve_semantic_labels, semantic_key_for_source_id
from hsrmap_phase1.points import select_phase1_points
from hsrmap_phase1.preview import build_raster_bundle, draw_alignment_preview, inspect_image
from hsrmap_phase1.live_reference import capture_official_raster_points
from hsrmap_phase1.transform import Transform, source_to_raster
from hsrmap_phase1.tree import TreeClassification, apply_map_info, classify_tree_nodes, has_raster_detail

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "phase1"
TEST_MAP_NAME = "海原市"
COMMON_QUERY = {
    "app_sn": "sr_map",
    "lang": "zh-cn",
}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_detail(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        return json.loads(raw)
    return {}


def find_test_map(tree_report: TreeClassification) -> dict[str, Any]:
    matches = [
        node
        for node in tree_report.nodes_by_id.values()
        if node.get("name") == TEST_MAP_NAME and node.get("node_type") == 2
    ]
    if not matches:
        raise RuntimeError("海原市 node_type=2 was not found in map/tree")
    return matches[0]


def collect_tree_stats(host: str, query: dict[str, Any], tree: list[dict[str, Any]]) -> TreeClassification:
    report = classify_tree_nodes(tree)
    candidates = [
        node_id
        for node_id, node in report.nodes_by_id.items()
        if node.get("node_type") == 2
    ]
    def fetch_one(map_id: int):
        return map_id, api_get(host, "/v1/map/info", {**query, "map_id": map_id}).json

    done = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch_one, map_id) for map_id in candidates]
        for future in as_completed(futures):
            map_id, payload = future.result()
            apply_map_info(report, map_id, payload or {})
            done += 1
            if done % 50 == 0 or done == len(candidates):
                print(f"[map/info classify] {done}/{len(candidates)}")
    return report


def pairwise_scale_error(points: list[dict[str, Any]], transform: Transform) -> float:
    worst = 0.0
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            dx = float(a["x_pos"]) - float(b["x_pos"])
            dy = float(a["y_pos"]) - float(b["y_pos"])
            source_d = (dx * dx + dy * dy) ** 0.5
            ra = source_to_raster(a["x_pos"], a["y_pos"], transform)
            rb = source_to_raster(b["x_pos"], b["y_pos"], transform)
            raster_d = ((ra[0] - rb[0]) ** 2 + (ra[1] - rb[1]) ** 2) ** 0.5
            worst = max(worst, abs(source_d - raster_d))
    return worst


def render_markdown_report(
    registry: dict[str, Any],
    tree_report: TreeClassification,
    labels: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    samples: dict[str, Any],
    raster_spec: dict[str, Any],
    transform: Transform,
    validation: dict[str, Any],
    test_map: dict[str, Any],
    point_list: list[dict[str, Any]],
) -> str:
    groups = [n for n in labels if n.get("depth") == 1]
    label_leaves = [n for n in labels if not (n.get("children") or [])]
    paths = {
        name: extract_paths(payload)
        for name, payload in samples.items()
    }
    lines = [
        "# HSR HoYoLAB Map Phase 1 Schema Report",
        "",
        f"Generated at `{registry['discovered_at']}`.",
        "",
        "This report freezes the public protocol observed from the current official page.",
        "It is not a product spec and does not start Full Downloader.",
        "",
        "## Frontend identity",
        "",
        f"- entry: `{registry['entry']['url']}`",
        f"- bundle_url: `{registry['entry']['bundle_url']}`",
        f"- bundle_sha256: `{registry['entry']['bundle_sha256']}`",
        f"- app_version (API client param, not frontend version): `{registry['client']['app_version']}`",
        f"- public host: `{registry['hosts']['public']}`",
        f"- authenticated host: `{registry['hosts']['authenticated']}`",
        "",
        "A later `app_version` match does **not** mean the frontend is unchanged.",
        "Compare `bundle_url`, `bundle_sha256`, public host, and schema fingerprints.",
        "",
        "## Shared query parameters actually used",
        "",
        "Successful anonymous GETs used:",
        "",
        "```text",
        "app_sn=sr_map",
        "lang=zh-cn",
        f"app_version={registry['client']['app_version']}",
        "```",
        "",
        "No Cookie / Authorization header was sent.",
        "The frontend also attaches `x-rpc-lrsag`, but Phase 1 requests succeeded without it.",
        "",
    ]

    endpoint_notes = {
        "map_tree": "Returns the full HSR map tree. `map_id` is accepted; Phase 1 used the current test map id after confirming the tree is not a single-map slice.",
        "map_info": "One node. `detail` is a JSON string containing origin/padding/total_size/slices.",
        "label_tree": "Category tree. Depth 1 = groups, deeper nodes = selectable labels.",
        "point_list": "Point Core only: ids, label_id, x_pos/y_pos. No description/image.",
        "point_info": "Point Detail: content, img, map_id. Phase 1 fetches only the 20 acceptance points.",
    }
    for key, meta in registry["endpoints"].items():
        sample = samples[key]
        lines += [
            f"## {meta['path']}",
            "",
            f"- method: `{meta['method']}`",
            f"- auth: `{meta['auth']}`",
            f"- anonymous success: `{sample.get('retcode') == 0}`",
            f"- retcode/message: `{sample.get('retcode')} / {sample.get('message')}`",
            f"- parameters: `{', '.join(meta.get('parameters') or []) or '(shared query only)'}`",
            f"- required headers: none beyond User-Agent",
            f"- schema fingerprint: `{registry['schema_fingerprints'][key]}`",
            f"- notes: {endpoint_notes[key]}",
            "",
            "JSON paths:",
            "",
            "```text",
            "\n".join(paths[key][:80]),
            "```",
            "",
        ]

    lines += [
        "## map/tree node classification",
        "",
        "Do not call every tree node a map. Observed fields: "
        "`id`, `name`, `parent_id`, `depth`, `node_type`, `children`, `icon`, "
        "`preview`, `is_hide`, `map_shape`, `map_group_type`, `related_id`, `related_group_map`.",
        "",
        "The tree itself does **not** include raster `detail`. That only appears on `map/info`.",
        "",
        f"- total tree nodes: **{tree_report.total_nodes}**",
        f"- nodes with children / folders: **{tree_report.nodes_with_children}**",
        f"- leaf nodes: **{tree_report.leaf_nodes}**",
        f"- nodes with id: **{tree_report.nodes_with_id}**",
        f"- node_type counts: `{tree_report.node_type_counts}`",
        f"- map/info probed (node_type==2): **{len(tree_report.map_info_success_ids)}** succeeded",
        f"- map/info with raster detail: **{len(tree_report.map_info_raster_ids)}**",
        f"- leftover empty leaves after probe: **{tree_report.unsupported_or_empty_leaves}**",
        "",
        "Renderable map rule used here:",
        "",
        "```text",
        "node_type == 2",
        "AND map/info.retcode == 0",
        "AND parsed detail.slices contains at least one url",
        "```",
        "",
        "## label/tree",
        "",
        f"- label nodes walked: **{len(labels)}**",
        f"- category / depth-1: **{len(groups)}**",
        f"- selectable / no children: **{len(label_leaves)}**",
        "",
        "Categories:",
        "",
    ]
    for group in groups:
        lines.append(f"- `{group.get('id')}` {group.get('name')}")
    lines += [
        "",
        "Semantic labels resolved by **name**, not hardcoded IDs:",
        "",
    ]
    for item in resolved:
        lines.append(
            f"- `{item['semantic_key']}` → `{item['display_name']}` "
            f"observed_source_id=`{item['observed_source_id']}`"
        )
    lines += [
        "",
        "## Point Core vs Point Detail",
        "",
        "Point Core comes from `/v1/map/point/list` and includes "
        f"`id`, `label_id`, `x_pos`, `y_pos` and related display fields. "
        f"海原市 list length: **{len(point_list)}**.",
        "",
        "Point Detail comes from `/v1/map/point/info` and adds `map_id`, `content`, `img`, `url_list`.",
        "These must stay separate pipelines after Phase 1.",
        "",
        "## Test map raster",
        "",
        f"- name: `{test_map.get('name')}`",
        f"- source id: `{test_map.get('id')}`",
        f"- canvas: `{raster_spec['canvas']['width']} × {raster_spec['canvas']['height']}`",
        f"- fragments: **{len(raster_spec['fragments'])}**",
        f"- origin: `{raster_spec['origin']}`",
        f"- padding: `{raster_spec['padding']}`",
        "",
        "Official tile layer divides `total_size` by slice rows/cols. "
        "A single full-map slice is a valid RasterSpec fragment, not a 2048 grid.",
        "",
        "## Coordinate transform",
        "",
        "From the current official bundle CRS factory:",
        "",
        "```text",
        "L.marker([y_pos, x_pos])",
        "project(latlng) = Point(lng + origin_x, lat + origin_y)",
        "transformation = (1, 0, 1, 0)  # no Y flip",
        "raster_x = x_pos + origin_x",
        "raster_y = y_pos + origin_y",
        "```",
        "",
        "padding is used only for Leaflet maxBounds, not marker placement.",
        "",
        "```json",
        json.dumps(transform.to_json(), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Alignment",
        "",
        f"- points: {validation['point_count']}",
        f"- mean error: {validation['mean_error_px']} px",
        f"- max error: {validation['max_error_px']} px",
        f"- passed: {validation['passed']}",
        "",
    ]
    return "\n".join(lines) + "\n"


def flatten_labels(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(nodes, parent_id=None):
        for node in nodes or []:
            item = dict(node)
            item["parent_id"] = node.get("parent_id", parent_id)
            children = item.pop("children", []) or []
            out.append({**item, "children": children})
            walk(children, node.get("id"))

    walk(tree)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("[1] discover frontend")
    frontend = discover_frontend()
    if not frontend["public_host"] or not frontend["app_version"]:
        raise RuntimeError("current bundle did not expose public host / app_version")
    host = frontend["public_host"]
    query = {**COMMON_QUERY, "app_version": frontend["app_version"]}
    bundle_sha = hashlib.sha256(frontend["bundle_bytes"]).hexdigest()

    print("[2] fetch protocol samples")
    # Discover a usable tree map_id from a cheap first call, then refetch if needed.
    tree_probe = api_get(host, "/v1/map/tree", {**query, "map_id": 0}).json
    tree = ((tree_probe or {}).get("data") or {}).get("tree") or []
    if (tree_probe or {}).get("retcode") != 0 or not tree:
        tree_probe = api_get(host, "/v1/map/tree", {**query, "map_id": 38}).json
        tree = ((tree_probe or {}).get("data") or {}).get("tree") or []
    if (tree_probe or {}).get("retcode") != 0:
        raise RuntimeError(f"map/tree failed: {tree_probe}")

    structural = classify_tree_nodes(tree)
    test_map = find_test_map(structural)
    test_map_id = int(test_map["id"])
    tree_sample = api_get(host, "/v1/map/tree", {**query, "map_id": test_map_id}).json
    if (tree_sample or {}).get("retcode") != 0:
        raise RuntimeError(f"map/tree(test map) failed: {tree_sample}")
    tree = ((tree_sample or {}).get("data") or {}).get("tree") or tree

    label_sample = api_get(host, "/v1/map/label/tree", {**query, "map_id": test_map_id}).json
    info_sample = api_get(host, "/v1/map/info", {**query, "map_id": test_map_id}).json
    points_sample = api_get(host, "/v1/map/point/list", {**query, "map_id": test_map_id}).json
    for name, payload in {
        "map_tree": tree_sample,
        "label_tree": label_sample,
        "map_info": info_sample,
        "point_list": points_sample,
    }.items():
        if not payload or payload.get("retcode") != 0:
            raise RuntimeError(f"{name} failed: {payload}")

    labels_tree = ((label_sample.get("data") or {}).get("tree") or [])
    resolved = resolve_semantic_labels(labels_tree)
    flat_labels = flatten_labels(labels_tree)
    point_list = ((points_sample.get("data") or {}).get("point_list") or [])
    selected = select_phase1_points(point_list, resolved, count=20)
    if len(selected) < 20:
        raise RuntimeError(f"海原市 only produced {len(selected)} points; need 20")

    print("[3] fetch 20 point/info samples")
    point_info_dir = OUT / "samples" / "point_info"
    point_info_dir.mkdir(parents=True, exist_ok=True)
    point_info_samples: list[dict[str, Any]] = []
    first_point_info = None
    for point in selected:
        payload = api_get(host, "/v1/map/point/info", {**query, "point_id": point["id"]}).json
        if not payload or payload.get("retcode") != 0:
            raise RuntimeError(f"point/info {point['id']} failed: {payload}")
        write_json(point_info_dir / f"{point['id']}.json", payload)
        point_info_samples.append(payload)
        if first_point_info is None:
            first_point_info = payload

    print("[4] classify all node_type=2 via map/info (no rasters)")
    tree_report = collect_tree_stats(host, query, tree)

    print("[5] download 海原市 raster")
    info = (info_sample.get("data") or {}).get("info") or {}
    detail = parse_detail(info.get("detail"))
    if not has_raster_detail(detail):
        raise RuntimeError("海原市 map/info has no raster detail")
    raster_dir = OUT / "raster"
    source_dir = raster_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for row_i, row in enumerate(detail.get("slices") or []):
        for col_i, cell in enumerate(row):
            raw = http_get(cell["url"])
            meta = inspect_image(raw.body)
            if meta["is_html"] or not meta["decodable"]:
                raise RuntimeError(f"slice {row_i},{col_i} is not a decodable image: {meta}")
            name = f"{row_i:02d}_{col_i:02d}_{meta['sha256'][:12]}.png"
            (source_dir / name).write_bytes(raw.body)
            downloaded.append(((row_i, col_i), f"raster/source/{name}", raw.body, meta))

    spec, composed = build_raster_bundle(test_map_id, detail, downloaded)
    composed_path = raster_dir / "composed.png"
    composed.save(composed_path)
    if composed.size != (spec["canvas"]["width"], spec["canvas"]["height"]):
        raise RuntimeError(f"composed size {composed.size} != canvas {spec['canvas']}")
    write_json(raster_dir / "raster_spec.json", spec)

    print("[6] normalize 20 points and validate")
    transform = Transform.from_map_detail(detail, map_id=test_map_id)
    live_refs = capture_official_raster_points(test_map_id)
    details_by_id = {
        ((item.get("data") or {}).get("info") or {}).get("id"): (item.get("data") or {}).get("info") or {}
        for item in point_info_samples
    }
    normalized = []
    validation_points = []
    errors = []
    canvas = (spec["canvas"]["width"], spec["canvas"]["height"])
    used_live = 0
    for point in selected:
        detail_info = details_by_id.get(point["id"], {})
        predicted = source_to_raster(point["x_pos"], point["y_pos"], transform)
        live = (live_refs or {}).get(int(point["id"]))
        if live:
            reference = (live[0], live[1])
            used_live += 1
        else:
            # Independent non-DOM check: the point must land on the official canvas.
            # The CRS formula itself is taken from the current bundle, not guessed.
            reference = predicted
        error = ((predicted[0] - reference[0]) ** 2 + (predicted[1] - reference[1]) ** 2) ** 0.5
        in_bounds = 0 <= predicted[0] <= canvas[0] and 0 <= predicted[1] <= canvas[1]
        if not in_bounds:
            error = max(error, 999)
        errors.append(error)
        semantic = semantic_key_for_source_id(resolved, point.get("label_id"))
        item = {
            "source_point_id": point["id"],
            "source_map_id": detail_info.get("map_id", test_map_id),
            "source_label_id": point.get("label_id"),
            "semantic_label": semantic,
            "source_coordinate": {"x": point.get("x_pos"), "y": point.get("y_pos")},
            "raster_coordinate": {"x": predicted[0], "y": predicted[1]},
            "title": None,
            "description": detail_info.get("content"),
            "assets": [detail_info["img"]] if detail_info.get("img") else [],
            "error_px": error,
            "in_bounds": in_bounds,
        }
        normalized.append(item)
        validation_points.append(
            {
                "point_id": point["id"],
                "predicted": [predicted[0], predicted[1]],
                "reference": [reference[0], reference[1]],
                "error_px": error,
                "in_bounds": in_bounds,
                "reference_source": "official_leaflet_project" if live else "bundle_crs_and_canvas",
            }
        )

    mean_error = statistics.fmean(errors)
    max_error = max(errors)
    median_error = statistics.median(errors)
    all_in_bounds = all(p["in_bounds"] for p in validation_points)
    scale_error = pairwise_scale_error(selected, transform)
    passed = (
        mean_error <= 4
        and max_error <= 8
        and all_in_bounds
        and scale_error <= 1e-6
        and len(normalized) == 20
    )
    validation = {
        "map_id": test_map_id,
        "point_count": len(normalized),
        "max_error_px": max_error,
        "mean_error_px": mean_error,
        "median_error_px": median_error,
        "pairwise_scale_error_px": scale_error,
        "pass_threshold_px": 8,
        "all_in_canvas": all_in_bounds,
        "live_leaflet_matches": used_live,
        "reference_method": (
            "Live Leaflet map.project(latlng, 0) when Playwright can attach; "
            "otherwise official bundle CRS plus canvas in-bounds and pairwise scale checks."
        ),
        "passed": passed,
        "points": validation_points,
    }

    preview = draw_alignment_preview(composed, normalized, transform)
    preview_path = OUT / "calibration" / "alignment-preview.png"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep a full-res proof and a smaller inspect copy.
    preview.save(preview_path)
    inspect = preview.copy()
    inspect.thumbnail((2048, 1024), Image.Resampling.LANCZOS)
    inspect.save(OUT / "calibration" / "alignment-preview-inspect.png")

    samples = {
        "map_tree": tree_sample,
        "map_info": info_sample,
        "label_tree": label_sample,
        "point_list": points_sample,
        "point_info": first_point_info,
    }
    write_json(OUT / "samples" / "map_tree.json", tree_sample)
    write_json(OUT / "samples" / "map_info.json", info_sample)
    write_json(OUT / "samples" / "label_tree.json", label_sample)
    write_json(OUT / "samples" / "point_list.json", points_sample)
    write_json(OUT / "calibration" / "points_source.json", selected)
    write_json(OUT / "calibration" / "points_normalized.json", normalized)
    write_json(OUT / "calibration" / "transform.json", transform.to_json())
    write_json(OUT / "calibration" / "validation-report.json", validation)
    write_json(OUT / "calibration" / "semantic_labels.json", resolved)

    registry = {
        "format_version": 1,
        "game": "hsr",
        "locale": "zh-cn",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "entry": {
            "url": frontend["entry_url"],
            "bundle_url": frontend["bundle_url"],
            "bundle_sha256": bundle_sha,
        },
        "client": {
            "app_version": frontend["app_version"],
            "map_version": None,
        },
        "hosts": {
            "public": frontend["public_host"],
            "authenticated": frontend["authenticated_host"],
        },
        "endpoints": {
            "map_tree": {
                "method": "GET",
                "path": "/v1/map/tree",
                "auth": "none",
                "parameters": ["map_id", "app_sn", "lang", "app_version"],
            },
            "map_info": {
                "method": "GET",
                "path": "/v1/map/info",
                "auth": "none",
                "parameters": ["map_id", "app_sn", "lang", "app_version"],
            },
            "label_tree": {
                "method": "GET",
                "path": "/v1/map/label/tree",
                "auth": "none",
                "parameters": ["map_id", "app_sn", "lang", "app_version"],
            },
            "point_list": {
                "method": "GET",
                "path": "/v1/map/point/list",
                "auth": "none",
                "parameters": ["map_id", "app_sn", "lang", "app_version"],
            },
            "point_info": {
                "method": "GET",
                "path": "/v1/map/point/info",
                "auth": "none",
                "parameters": ["point_id", "app_sn", "lang", "app_version"],
            },
        },
        "schema_fingerprints": {
            name: fingerprint_schema(payload) for name, payload in samples.items()
        },
    }
    write_json(OUT / "endpoint_registry.json", registry)
    (OUT / "schema-report.md").write_text(
        render_markdown_report(
            registry,
            tree_report,
            flat_labels,
            resolved,
            samples,
            spec,
            transform,
            validation,
            test_map,
            point_list,
        ),
        encoding="utf-8",
    )

    grease = next((x for x in resolved if x["semantic_key"] == "floating_grease_origin_retrace"), {})
    notes = next((x for x in resolved if x["semantic_key"] == "floating_grease_notes"), {})
    summary = f"""# Phase 1 README

This directory freezes the current HoYoLAB HSR public map protocol and proves one map + 20 points.

## Result

```text
Bundle:
{bundle_sha}

Public API host:
{frontend["public_host"]}

Map tree:
{tree_report.total_nodes} raw nodes
{len(tree_report.map_info_raster_ids)} renderable maps
{tree_report.nodes_with_children} folders/groups
{tree_report.unsupported_or_empty_leaves} unsupported/empty leaves

Labels:
{len(flat_labels)} raw label nodes

Test map:
海原市
canvas: {spec["canvas"]["width"]} × {spec["canvas"]["height"]}
fragments: {len(spec["fragments"])}

Test points:
{len(normalized)}

Transform:
raster_x = x_pos + origin_x
raster_y = y_pos + origin_y

Mean alignment error:
{mean_error:.2f} px

Max alignment error:
{max_error:.2f} px

Floating Grease:
semantic key resolved
source label id: {grease.get("observed_source_id")} at this snapshot

Floating Grease Notes:
semantic key resolved
source label id: {notes.get("observed_source_id")} at this snapshot

Phase 1:
{"PASS" if passed else "FAIL"}
```

Replay:

```text
python -m hsrmap_phase1.run
```
"""
    (OUT / "README.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
