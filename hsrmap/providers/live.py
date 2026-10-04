from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from hsrmap.detail_normalize import normalize_point_info
from hsrmap.normalize import flatten_map_nodes, normalize_map_info, normalize_point
from hsrmap.render_probe import TREE_MAP_NODE_TYPE
from hsrmap.schema import check_payload
from hsrmap.viewer_crs import get_max_bounds, get_raster_bounds

ENDPOINT_SCHEMA = {
    "/v1/map/tree": "map_tree",
    "/v1/map/info": "map_info",
    "/v1/map/point/list": "point_list",
    "/v1/map/point/info": "point_info",
    "/v1/map/label/tree": "label_tree",
}


class LiveIncompatible(RuntimeError):
    pass


@dataclass
class LiveSession:
    host: str
    app_version: str
    bundle_sha256: str
    compatible: bool = True
    bundle_changed: bool = False

    def params(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {"app_sn": "sr_map", "lang": "zh-cn", "app_version": self.app_version, **extra}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def live_asset_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def _viewer_tree(flat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """live 树：没有任何落库证据，probe 一律 UNKNOWN。

    这里只能按官方树的结构提示（node_type=2）决定节点是否可点进去——那**不是**可渲染判定
    （a1-8-1 §九）：真的点开时 `get_map()` 会去拉 map/info，没有 raster 就返回 None。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for row in flat:
        renderable = row["is_renderable"]
        if renderable is None:
            renderable = row["node_type"] == TREE_MAP_NODE_TYPE
        by_id[row["source_id"]] = {
            "id": row["source_id"],
            "name": row["name"] or row["source_id"],
            "type": "map" if renderable else "folder",
            "renderable": bool(renderable),
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for row in flat:
        node = by_id[row["source_id"]]
        parent = row["parent_source_id"]
        if parent and parent in by_id:
            by_id[parent]["children"].append(node)
        else:
            roots.append(node)
    return roots


class LiveProvider:
    def __init__(self, client: Any, session: LiveSession):
        self.client = client
        self.session = session
        self._map_cache: dict[str, dict[str, Any]] = {}
        self._point_list_cache: dict[str, list[dict[str, Any]]] = {}
        self._fetched_at = _now()

    def provenance(self) -> dict[str, Any]:
        return {
            "source": "live",
            "fetched_at": self._fetched_at,
            "snapshot_fallback": None,
            "stale": False,
            "bundle_sha256": self.session.bundle_sha256,
        }

    def _get(self, path: str, extra: dict[str, Any]) -> dict[str, Any]:
        if not self.session.compatible:
            raise LiveIncompatible("session marked incompatible")
        payload = self.client.api_get(self.session.host, path, self.session.params(extra)).json()
        schema_name = ENDPOINT_SCHEMA.get(path)
        if schema_name:
            check = check_payload(schema_name, payload, None)
            if not check.ok:
                raise LiveIncompatible(check.reason or "schema")
        if payload.get("retcode") != 0:
            raise LiveIncompatible(f"retcode {payload.get('retcode')}")
        self._fetched_at = _now()
        return payload

    def get_tree(self, refresh: bool = False) -> list[dict[str, Any]]:
        payload = self._get("/v1/map/tree", {"map_id": 0})
        tree = (payload.get("data") or {}).get("tree") or []
        return _viewer_tree(flatten_map_nodes(tree))

    def get_map(self, map_id: str, refresh: bool = False) -> dict[str, Any] | None:
        payload = self._get("/v1/map/info", {"map_id": map_id})
        try:
            spec = normalize_map_info(payload)
        except ValueError:
            return None
        self._map_cache[str(map_id)] = spec
        fragment = spec["fragments"][0] if spec["fragments"] else {}
        remote = fragment.get("remote_url")
        bounds = get_raster_bounds(spec["origin_x"], spec["origin_y"], spec["canvas_width"], spec["canvas_height"])
        max_bounds = get_max_bounds(
            spec["origin_x"], spec["origin_y"], spec["canvas_width"], spec["canvas_height"], spec.get("padding_json")
        )
        return {
            "id": spec["source_id"],
            "name": spec["name"],
            "width": int(spec["canvas_width"]),
            "height": int(spec["canvas_height"]),
            "origin": [float(spec["origin_x"]), float(spec["origin_y"])],
            "padding": spec.get("padding_json"),
            "raster": {
                "asset": live_asset_key(remote) if remote else None,
                "live_url": remote,
            },
            "bounds": bounds,
            "max_bounds": max_bounds,
            "crs": {
                "projection": "LonLat",
                "transformation": [1, float(spec["origin_x"]), 1, float(spec["origin_y"])],
                "marker": ["y_pos", "x_pos"],
            },
        }

    def get_points(
        self,
        map_id: str,
        semantic_key: str | None = None,
        label_id: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        spec = self._map_cache.get(str(map_id))
        if spec is None:
            if self.get_map(map_id) is None:
                return []
            spec = self._map_cache[str(map_id)]
        payload = self._get("/v1/map/point/list", {"map_id": map_id})
        points = []
        for raw in (payload.get("data") or {}).get("point_list") or []:
            if label_id and str(raw.get("label_id")) != str(label_id):
                continue
            norm = normalize_point(raw, str(map_id), spec["origin_x"], spec["origin_y"])
            lid = None if raw.get("label_id") is None else str(raw.get("label_id"))
            if semantic_key:
                continue
            points.append(
                {
                    "id": int(raw["id"]),
                    "source_id": norm["source_id"],
                    "x": float(norm["x_pos"]),
                    "y": float(norm["y_pos"]),
                    "raster_x": float(norm["raster_x"]),
                    "raster_y": float(norm["raster_y"]),
                    "labels": [{"id": lid or "none", "name": raw.get("label_name") or "", "icon": None}],
                }
            )
        self._point_list_cache[str(map_id)] = points
        return points

    def get_labels(self, map_id: str, refresh: bool = False) -> list[dict[str, Any]] | None:
        points = self.get_points(map_id)
        counts: dict[str, dict[str, Any]] = {}
        for point in points:
            for label in point["labels"]:
                item = counts.setdefault(label["id"], {"id": label["id"], "name": label["name"], "icon": None, "count": 0, "semantic_key": None})
                item["count"] += 1
        if not counts:
            return []
        return [{"category": {"id": "0", "name": "标签"}, "labels": list(counts.values())}]

    def get_point(self, core_point_id: int, refresh: bool = False) -> dict[str, Any] | None:
        payload = self._get("/v1/map/point/info", {"point_id": core_point_id})
        info = normalize_point_info(payload)
        images = []
        for image in info.get("images") or []:
            url = image.get("remote_url")
            if not url:
                continue
            images.append({"url": f"/api/v1/live-assets/{live_asset_key(url)}", "role": image.get("role") or "image", "live_url": url})
        map_id = None
        x = y = 0.0
        for cached in self._point_list_cache.values():
            hit = next((p for p in cached if p["id"] == int(core_point_id) or p["source_id"] == str(core_point_id)), None)
            if hit:
                x, y = hit["x"], hit["y"]
                break
        return {
            "core": {
                "point_id": str(core_point_id),
                "source_id": str(info.get("source_point_id") or core_point_id),
                "map_id": map_id,
                "x": x,
                "y": y,
            },
            "labels": [],
            "detail": {
                "state": info["detail_state"],
                "text": info.get("plain_text") or None,
                "images": images,
            },
        }

    def point_list_hash(self, map_id: str) -> str:
        points = self.get_points(map_id)
        blob = json_dumps_stable(points)
        return sha256(blob.encode("utf-8")).hexdigest()


def json_dumps_stable(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
