# Guide Atlas V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a1-5.md: generalize the floating-grease pipeline into a Guide Atlas Engine.

**Architecture:** Topic Registry + Inventory + Guide DB V3 (POINT / MAP_LABEL / POINT_SET). Existing grease pipeline stays the Puzzle/POINT golden. No `if topic == floating_grease` in the main flow.

**Tech Stack:** Python 3.11, sqlite3, pytest, existing hsrmap.guides

**Spec:** `a1-5.md`

## Global Constraints

- AI 可以漏，不能编
- 匹配不确定就进 Review
- Point ID 只能来自官方 core.db
- Published Guide 只能来自人工 Approve
- Viewer 不发外网
- 不自动 Publish
- 浮脂 48/48 不退化

## Review Focus

- 1006 selectable labels all classified
- Unknown labels become NEEDS_REVIEW, never GUIDE_TOPIC by guess
- Existing published grease entries still resolve after V3 migration
- Matcher never invents source_point_id

## Sprint order (from a1-5)

V3.0 Inventory → V3.1 Topic Registry → V3.2 DB V3 → V3.3 grease migrate → V3.4–6 generic profiles → V3.9 dream ticker → V3.10 origami bird
