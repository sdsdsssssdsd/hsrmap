# Phase 4L progress

## L0
- status: pass
- bundle_sha256 unchanged, schema OK
- Ruling: product stays server-side Live; no browser CORS.

## L1
- status: pass — SnapshotProvider, existing API bodies unchanged, TestClient default offline

## L2
- status: pass — LiveProvider + LiveIncompatible, fixture tests

## L3
- status: pass — HybridProvider DNS/error → snapshot; map+points same batch

## L4
- status: pass — LiveAssetStore under data/live-cache/; `/api/v1/live-assets/{key}`

## L5
- status: pass — `/api/v1/data-source` + UI chip/settings radios
- Ruling: default TestClient offline; `serve` uses hybrid
- Ruling: API bodies stay unwrapped; provenance is `/api/v1/data-source` + chip (avoid breaking existing Viewer JSON).

## L6
- status: pass — `/api/v1/maps/{id}/revision` + 5min poll banner, apply is explicit

## L7
- status: pass (prompt-only) — settings shows `python -m hsrmap sync`; live vs snapshot not mixed
- Ruling: do not spawn the 6.8GiB sync from the UI; button only shows the documented CLI.

## Fault / TTL closeout
- status: pass
- Hybrid TTL: tree/labels 30min, map+points 8min, point 30min; `?refresh=true` bypasses
- Raster fetch is part of the live map batch; CDN fail rolls the whole map to snapshot
- 500 / timeout / DNS → snapshot + stale +「官方连接失败」
- Schema fail → disable live for the session +「官方地图接口已变化…」
- Point detail remains readable from in-memory live-cache after disconnect
- Next refresh retries live
- force-live network fail → HTTP 503
- JSON live-cache stays in-memory; disk is assets only (deletable `data/live-cache/`)
