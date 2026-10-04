# Phase 5 Guide Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the hand-written `guide.db` into a local guide pipeline: discover → fetch/import → parse to DocumentBlocks → optional DeepSeek → point match → review → publish → offline Viewer.

**Architecture:** Viewer never crawls. New `hsrmap/guides/` package writes only to `data/guides/` and `data/guide-assets/`. Official `core.db` / `detail.db` / `data/assets` stay read-only. LLM is optional and behind fixtures in tests.

**Tech Stack:** SQLite V2 schema, httpx/urllib fetch, HTML parser, optional Playwright fallback later, DeepSeek JSON via injected client, existing FastAPI Viewer.

**Spec:** `a1-3.md`

## Global Constraints

- Personal local use. Do not redistribute third-party article text/images.
- Rate-limited, robots-aware, no captcha/proxy/login bypass. Hard sites → `NEEDS_MANUAL_IMPORT`.
- Discovery is seed URLs for Floating Grease only, not whole-site crawl.
- Tests never require live sites or DeepSeek. Use HTML fixtures / fake clients.
- Existing `/api/v1/guides` local-entry API must keep working.
- Week-1 gate: no AI required to turn a page into source + blocks + local images + raw archive.

## Review Focus

- Published guides must survive Viewer with `connect-src 'self'` and zero outbound requests.
- Duplicate reprints cluster, not three independent guides.
- Matcher never writes official point coordinates from LLM guesswork.
- Raw archive remains after parser/prompt/model change.
- High-confidence auto-accept does not skip publish confirmation.

---

## Task G0 — Guide DB V2

**Files:** `hsrmap/guide_db.py`; `tests/test_guide_db_v2.py`

**Produces:** tables `guide_source`, `guide_page`, `guide_point_binding`, `crawl_run`, `extraction_run`, `review_item`, `guide_cluster`; existing `guide_entry`/`guide_steps`/`guide_assets` unchanged.

**Status:** GREEN (`tests/test_guide_db_v2.py`)

**RED:** `db.upsert_source({"name":"17173","domain":"news.17173.com"})` returns id and `list_sources()`.

---

## Task G1 — Raw Guide Store

**Files:** `hsrmap/guides/store.py`; `hsrmap/paths.py`; `tests/test_guide_store.py`

**Produces:** `data/guides/raw/<run>/pages|extracted|manifests`; `data/guide-assets/sha256/<aa>/<sha>`; never writes `data/assets/sha256`.

---

## Task G2 — TapTap + 17173 + import-page

**Files:** `hsrmap/guides/crawler/base.py`, `fetch.py`, `robots.py`; `hsrmap/guides/sources/taptap.py`, `site17173.py`; `hsrmap/guides/seeds.yaml`; CLI `guides import-page`; fixtures under `tests/fixtures/guides/`.

**Produces:** adapter `discover/fetch/parse_page/extract_assets/canonicalize`. Import local HTML without network.

---

## Task G3 — GamerSky + 3DM

**Files:** `hsrmap/guides/sources/gamersky.py`, `three_dm.py`; fixture tests.

---

## Task G4 — DocumentBlocks

**Files:** `hsrmap/guides/extract/blocks.py`; `tests/test_guide_blocks.py`

**Produces:** heading/paragraph/image/list/quote/video_link/separator from fixture HTML; image assets via store.

---

## Task G5/G6 — DeepSeek text + vision (fake)

**Files:** `hsrmap/guides/llm/client.py`, `extract.py`; `hsrmap/guides/prompts/*_v1.txt`; `tests/test_guide_llm.py`

**Produces:** strict JSON extract + cache key `sha(content+prompt+model)`; no live API in tests.

---

## Task G7/G8 — Point Matcher

**Files:** `hsrmap/guides/matching/matcher.py`; `tests/test_guide_match.py`

**Produces:** candidates + score from map name / grease topic / ordinal; CV hook is optional no-op unless fixture hash given.

---

## Task G9/G10 — Review + Publisher

**Files:** `hsrmap/guides/review/queue.py`; `hsrmap/guides/publish/publisher.py`; tests

**Produces:** review items; publish copies bindings into Viewer `guide_entry` without touching detail.db.

---

## Task G11/G12 — Viewer + CLI

**Files:** `hsrmap/cli.py`; `web` drawer/badge; `tests/test_guides.py`

**Produces:** `python -m hsrmap guides {discover,fetch,parse,extract,match,review,publish,sync,import-page}`; Viewer lists published guides offline; marker book badge if a point has a published guide.

## Ledger

Rulings go in `docs/superpowers/sdd/2026-10-02-phase-5-guide-overlay/progress.md`.
