from __future__ import annotations

from typing import Any

from hsrmap.http import RateLimitedClient
from hsrmap.paths import REGISTRY_PATH
from hsrmap.preflight import load_registry, preflight
from hsrmap.providers.hybrid import HybridProvider
from hsrmap.providers.live import LiveProvider, LiveSession
from hsrmap.providers.snapshot import SnapshotProvider
from hsrmap.viewer_bind import ViewerContext


def connect_live(client: Any | None = None) -> LiveSession:
    client = client or RateLimitedClient(min_interval=0.3)
    result = preflight(load_registry(REGISTRY_PATH), client)
    compatible = bool(result.get("ok"))
    return LiveSession(
        host=result.get("public_host") or "",
        app_version=result.get("app_version") or "",
        bundle_sha256=result.get("bundle_sha256") or "",
        compatible=compatible,
        bundle_changed=bool(result.get("bundle_changed")),
    )


def build_provider(
    ctx: ViewerContext,
    mode: str = "offline",
    live_client: Any | None = None,
    live_session: LiveSession | None = None,
    raster_fetch: Any | None = None,
) -> Any:
    snapshot = SnapshotProvider(ctx)
    if mode == "offline":
        return snapshot
    session = live_session
    if session is None and live_client is not None:
        session = LiveSession(host="https://example.invalid", app_version="x", bundle_sha256="", compatible=True)
    if session is None:
        try:
            session = connect_live(live_client)
        except Exception:
            return snapshot
    live = LiveProvider(live_client or RateLimitedClient(min_interval=0.3), session)
    hybrid = HybridProvider(live, snapshot, force_live=mode == "live", raster_fetch=raster_fetch)
    if not session.compatible:
        hybrid._incompatible = True
        hybrid._last_error = "官方地图接口已变化；离线地图仍可正常使用。"
    return hybrid
