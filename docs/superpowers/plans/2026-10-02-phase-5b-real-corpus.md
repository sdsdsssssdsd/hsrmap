# Phase 5B Real Guide Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze G0–G4 adapters and close the loop: canary pages → derived LLM extract → golden metrics → human review edit → approve-only publish, with Point Match Coverage reported separately from Real Guide Coverage.

**Architecture:** Tests keep `FakeGuideLLMProvider`. Real DeepSeek is a second provider behind `DEEPSEEK_API_KEY` and YAML model names. LLM writes only `data/guides/derived/`. Snapshot publish requires `APPROVED`. Viewer stays `connect-src 'self'`.

**Tech Stack:** SQLite review drafts, FastAPI `/review` console, stdlib HTTP for DeepSeek, existing Viewer.

**Spec:** user Phase 5B–5J note (this session). G0–G4 frozen.

## Global Constraints

- No new Source Adapters.
- Do not publish 48 points in this round.
- API key only from `DEEPSEEK_API_KEY`. Never write key to guide.db / manifest / logs / committed config.
- LLM output never overwrites Raw HTML or DocumentBlocks.
- Hallucinated steps = 0 is a hard fail.
- Official assets remain read-only.

## Review Focus

- Auto match must not become published.
- Fake provider must not invent steps absent from blocks.
- Coverage JSON must not treat matcher 48/48 as approved guides.
- Viewer must not fetch original article URLs.
- Dedup must cluster reprints without deleting raw archives.

---

## Task B1 — Canary import QA

**Files:** `hsrmap/guides/qa.py`; `tests/fixtures/guides/canary/*`; `tests/test_guide_qa.py`

**Produces:** 7 structural canary pages; `inspect_import` gate; ads dropped.

## Task B2 — LLM providers + derived store

**Files:** `hsrmap/guides/llm/provider.py`, `fake.py`, `deepseek.py`, `config.py`; `hsrmap/guides/derived.py`; CLI `extract --page`

## Task B3 — Golden 20 + split metrics

**Files:** `phase1/calibration/golden_guides.json`; `hsrmap/guides/golden.py`; `tests/test_guide_golden.py`

## Task B4 — Review Console + approve-only publish

**Files:** `hsrmap/guides/review/service.py`, `console.html`; `hsrmap/viewer_app.py`; `tests/test_guide_review_api.py`

## Task B5 — Coverage split + ingest CLI

**Files:** `hsrmap/guides/coverage.py`; `hsrmap/guides/dedup.py`; CLI `ingest`, `coverage`; Viewer empty/source/tooltip.
