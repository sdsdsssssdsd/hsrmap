# Phase 4L Live Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI-side Live/Hybrid/Snapshot data layer so the Viewer can show current official HSR public map data while the browser stays on `127.0.0.1` and frozen `core.db`/`detail.db` remain read-only.

**Architecture:** Browser continues same-origin `/api/v1/*`. A `MapDataProvider` protocol has `SnapshotProvider` (current DBs), `LiveProvider` (server-side HoYo public GETs + `data/live-cache/`), and `HybridProvider` (live → live-cache → snapshot; map+points are one batch). Live never writes official DBs. Discovery reuses `discover_frontend` + required-schema preflight.

**Tech Stack:** FastAPI, existing `RateLimitedClient` (min_interval ≥ 0.3s), Phase 1 registry/schema fingerprints, React provenance chip.

**Spec:** `a1-2.md`

## Global Constraints

- Browser never talks to HoYo. CSP `connect-src 'self'` stays.
- `core.db` / `detail.db` / `data/assets` remain read-only snapshot.
- Live payloads go only to `data/live-cache/` (HTTP cache; deletable).
- Do not mix snapshot map/info with live point/list.
- Personal local use: anonymous, read-only, low concurrency. Not an official API; do not redistribute dumped official assets.
- Tests never require a live HoYo network. Use fixtures/fakes.
- `TestClient(create_app())` stays snapshot/offline so existing suite does not call the network.
- Production `serve` default mode is hybrid (在线优先) per spec; stored in `user.db` metadata `data_mode`.

## Review Focus

- Schema-incompatible live payload must disable Live, not crash Viewer.
- Raster live fail rolls back the whole map (info+points), not a half-live map.
- Open point detail remains readable if the network drops mid-session.
- Live cache deletion cannot touch snapshot or user/guide DBs.
- CORS browser-direct to HoYo is explicitly out of product scope.

---

## Task L0 — Discovery spike (throwaway)

**Files:** none kept. Use existing `hsrmap.preflight.preflight`.

1. Run `preflight(load_registry(REGISTRY_PATH))` once.
2. Record: bundle SHA vs registry, public host, app_version, schema ok/fail.
3. Do **not** commit a CORS browser experiment as product. Conclusion: server-side only.

**Expected:** `ok` True or `LIVE_INCOMPATIBLE` fallback path justified. Probe labeled throwaway.

---

## Task L1 — DataProvider + SnapshotProvider

**Files:** `hsrmap/providers/base.py`, `hsrmap/providers/snapshot.py`, `hsrmap/providers/__init__.py`; `hsrmap/viewer_app.py`; `tests/test_provider_snapshot.py`

**Produces:** `MapDataProvider` with `get_tree/get_map/get_points/get_labels/get_point`. Snapshot delegates to `viewer_repo`. App uses provider. Existing viewer tests still pass with default offline.

**RED:** `provider.get_tree()` has 923 nodes / 624 renderable.

**GREEN:** SnapshotProvider wraps `map_tree_payload`.

---

## Task L2 — LiveProvider (fixtures, not live net in tests)

**Files:** `hsrmap/providers/live.py`, `hsrmap/live_session.py`; `tests/test_provider_live.py`; fixtures under `tests/fixtures/live/`

**Produces:** Transform official `retcode=0` map/tree, map/info, point/list, point/info into Viewer shapes. Schema fail → `LiveIncompatible`. RateLimitedClient injected.

**RED:** fake client returning Phase-1-shaped JSON yields map 842 name 海原市 and points with x/y.

**GREEN:** live adapter using `flatten_map_nodes` / `normalize_map_info` / `normalize_point`.

---

## Task L3 — HybridProvider

**Files:** `hsrmap/providers/hybrid.py`; `tests/test_provider_hybrid.py`

**Produces:** live success → live; live timeout/500/DNS → cache; miss → snapshot. Map usable? yes → map+points live; no → both snapshot.

**RED:** fake live raises → tree still 923 from snapshot.

---

## Task L4 — Live asset cache gateway

**Files:** `hsrmap/live_assets.py`; `hsrmap/viewer_app.py` `/api/v1/live-assets/{key}`; `tests/test_live_assets.py`

**Produces:** Remote image streamed to `data/live-cache/assets/`, served locally. Browser never loads hoyolab CDN URLs. Raster fail → whole map snapshot.

---

## Task L5 — Provenance UI + data mode

**Files:** `hsrmap/viewer_app.py` `GET /api/v1/data-source`, `PUT /api/v1/data-source`; `web/src` status chip + settings radios; tests `tests/test_data_source.py`

**Produces:** `{mode, source, fetched_at, snapshot_id, stale, bundle_sha, message}`. UI: 在线 / 离线 / 已回退. Settings: 完全离线 / 在线优先 / 强制在线. Refresh current map ignores TTL.

---

## Task L6 — Active map refresh

**Files:** `hsrmap/providers/hybrid.py` hash of normalized point/list; `web` 5min poll + banner

**Produces:** SHA change → “官方地图已更新 [应用更新]”, no silent marker swap.

---

## Task L7 — Snapshot bridge (prompt only)

**Files:** settings “构建最新离线快照” calls existing CLI sync **or** returns `{action: "run_cli", command: "python -m hsrmap sync"}` without mixing live writes into snapshot. Prefer invoking documented CLI subprocess only if lock-free; otherwise instruct + status. Do not run full 6.8GiB sync in tests.

**Produces:** Live discovery `bundle_changed` → UI “有新官方数据” + button that is **not** the same as Live refresh.

---

## Ledger

Rulings go in `docs/superpowers/sdd/2026-10-02-phase-4l-live-data-layer/progress.md`.
