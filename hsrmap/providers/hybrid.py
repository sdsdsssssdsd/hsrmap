from __future__ import annotations

from typing import Any, Callable

from hsrmap.providers.live import LiveIncompatible

LIVE_ERRORS = (OSError, TimeoutError, ConnectionError, LiveIncompatible, RuntimeError, ValueError)

TTL_TREE_SEC = 30 * 60
TTL_LABEL_SEC = 30 * 60
TTL_MAP_SEC = 8 * 60
TTL_POINT_SEC = 30 * 60

SCHEMA_MESSAGE = "官方地图接口已变化；离线地图仍可正常使用。"
NETWORK_MESSAGE = "官方连接失败"


class HybridProvider:
    def __init__(
        self,
        live: Any,
        snapshot: Any,
        force_live: bool = False,
        raster_fetch: Callable[[str], Any] | None = None,
        now: Callable[[], float] | None = None,
    ):
        import time

        self.live = live
        self.snapshot = snapshot
        self.force_live = force_live
        self.raster_fetch = raster_fetch
        self._now = now or time.monotonic
        self._source = "snapshot"
        self._mem: dict[str, tuple[float, Any]] = {}
        self._last_error: str | None = None
        self._incompatible = False

    def provenance(self) -> dict[str, Any]:
        session = getattr(self.live, "session", None)
        bundle = getattr(session, "bundle_sha256", None) if session is not None else None
        changed = bool(getattr(session, "bundle_changed", False)) if session is not None else False
        if self._source == "live":
            prov = dict(self.live.provenance())
            prov["bundle_changed"] = changed
            prov["message"] = None
            return prov
        if self._source == "live-cache":
            prov = dict(self.live.provenance())
            prov["source"] = "live-cache"
            prov["stale"] = True
            prov["snapshot_fallback"] = self.snapshot.provenance().get("snapshot_fallback")
            prov["bundle_changed"] = changed
            prov["message"] = self._last_error
            return prov
        prov = dict(self.snapshot.provenance())
        prov["bundle_sha256"] = bundle
        prov["bundle_changed"] = changed
        if self._last_error:
            prov["stale"] = True
            prov["message"] = self._last_error
        return prov

    def _fallback(self, error: Exception) -> None:
        if self.force_live:
            raise error

    def _read(self, key: str, ttl: float, refresh: bool) -> Any | None:
        if refresh:
            return None
        hit = self._mem.get(key)
        if hit is None:
            return None
        if self._now() - hit[0] >= ttl:
            return None
        return hit[1]

    def _write(self, key: str, value: Any) -> None:
        self._mem[key] = (self._now(), value)

    def _mark_network(self) -> None:
        self._last_error = NETWORK_MESSAGE

    def _mark_schema(self) -> None:
        self._incompatible = True
        self._last_error = SCHEMA_MESSAGE

    def get_tree(self, refresh: bool = False) -> list[dict[str, Any]]:
        cached = self._read("tree", TTL_TREE_SEC, refresh)
        if cached is not None:
            self._source = "live"
            return cached
        if self._incompatible:
            if self.force_live:
                raise LiveIncompatible("session marked incompatible")
            self._source = "snapshot"
            return self.snapshot.get_tree()
        try:
            tree = self.live.get_tree()
            self._write("tree", tree)
            self._source = "live"
            self._last_error = None
            return tree
        except LiveIncompatible as exc:
            self._fallback(exc)
            self._mark_schema()
            self._source = "snapshot"
            return self.snapshot.get_tree()
        except LIVE_ERRORS as exc:
            self._fallback(exc)
            self._mark_network()
            cached = self._mem.get("tree")
            if cached is not None:
                self._source = "live-cache"
                return cached[1]
            self._source = "snapshot"
            return self.snapshot.get_tree()

    def _live_map_batch(self, map_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        info = self.live.get_map(map_id)
        if info is None:
            raise LiveIncompatible("live map missing")
        url = (info.get("raster") or {}).get("live_url")
        if url and self.raster_fetch is not None:
            self.raster_fetch(url)
        points = self.live.get_points(map_id)
        return info, points

    def get_map(self, map_id: str, refresh: bool = False) -> dict[str, Any] | None:
        key = f"map:{map_id}"
        cached = self._read(key, TTL_MAP_SEC, refresh)
        if cached is not None:
            self._source = "live"
            return cached[0]
        if self._incompatible:
            if self.force_live:
                raise LiveIncompatible("session marked incompatible")
            self._source = "snapshot"
            return self.snapshot.get_map(map_id)
        try:
            info, points = self._live_map_batch(map_id)
            self._write(key, (info, points))
            self._source = "live"
            self._last_error = None
            return info
        except LiveIncompatible as exc:
            self._fallback(exc)
            self._mark_schema()
            self._source = "snapshot"
            return self.snapshot.get_map(map_id)
        except LIVE_ERRORS as exc:
            self._fallback(exc)
            self._mark_network()
            stale = self._mem.get(key)
            if stale is not None:
                self._source = "live-cache"
                return stale[1][0]
            self._source = "snapshot"
            return self.snapshot.get_map(map_id)

    def get_points(
        self,
        map_id: str,
        semantic_key: str | None = None,
        label_id: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        key = f"map:{map_id}"
        if refresh or self._read(key, TTL_MAP_SEC, False) is None or self._source == "snapshot":
            self.get_map(map_id, refresh=refresh)
        if self._source == "snapshot":
            return self.snapshot.get_points(map_id, semantic_key=semantic_key, label_id=label_id)
        cached = self._mem.get(key)
        points = cached[1][1] if cached else []
        if self._source == "live-cache":
            pass
        elif cached is not None:
            self._source = "live"
        if label_id:
            return [p for p in points if any(l.get("id") == str(label_id) for l in p.get("labels") or [])]
        return points

    def get_labels(self, map_id: str, refresh: bool = False) -> list[dict[str, Any]] | None:
        key = f"labels:{map_id}"
        cached = self._read(key, TTL_LABEL_SEC, refresh)
        if cached is not None:
            self._source = "live"
            return cached
        if self._incompatible or self._source == "snapshot":
            if self.force_live and self._incompatible:
                raise LiveIncompatible("session marked incompatible")
            self._source = "snapshot"
            return self.snapshot.get_labels(map_id)
        try:
            labels = self.live.get_labels(map_id)
            self._write(key, labels)
            self._source = "live"
            self._last_error = None
            return labels
        except LiveIncompatible as exc:
            self._fallback(exc)
            self._mark_schema()
            self._source = "snapshot"
            return self.snapshot.get_labels(map_id)
        except LIVE_ERRORS as exc:
            self._fallback(exc)
            self._mark_network()
            stale = self._mem.get(key)
            if stale is not None:
                self._source = "live-cache"
                return stale[1]
            self._source = "snapshot"
            return self.snapshot.get_labels(map_id)

    def get_point(self, core_point_id: int, refresh: bool = False) -> dict[str, Any] | None:
        key = f"point:{core_point_id}"
        cached = self._read(key, TTL_POINT_SEC, refresh)
        if cached is not None:
            self._source = "live"
            return cached
        if self._incompatible:
            if self.force_live:
                raise LiveIncompatible("session marked incompatible")
            self._source = "snapshot"
            return self.snapshot.get_point(core_point_id)
        try:
            detail = self.live.get_point(core_point_id)
            self._write(key, detail)
            self._source = "live"
            self._last_error = None
            return detail
        except LiveIncompatible as exc:
            self._fallback(exc)
            self._mark_schema()
            self._source = "snapshot"
            return self.snapshot.get_point(core_point_id)
        except LIVE_ERRORS as exc:
            self._fallback(exc)
            self._mark_network()
            stale = self._mem.get(key)
            if stale is not None:
                self._source = "live-cache"
                return stale[1]
            self._source = "snapshot"
            return self.snapshot.get_point(core_point_id)

    def point_list_hash(self, map_id: str) -> str | None:
        try:
            return self.live.point_list_hash(map_id)
        except LIVE_ERRORS:
            return None
