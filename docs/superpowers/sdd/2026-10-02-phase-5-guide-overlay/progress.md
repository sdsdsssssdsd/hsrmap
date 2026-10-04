# Phase 5 progress

## G0 Guide DB V2
- status: GREEN
- tests: tests/test_guide_db_v2.py

## G1 Raw Guide Store
- status: GREEN
- tests: tests/test_guide_store.py

## G2 TapTap + 17173 + import-page
- status: GREEN
- tests: tests/test_guide_adapters.py, tests/test_guide_import.py, tests/test_guide_cli.py
- Ruling: live fetch without HTML is `NEEDS_MANUAL_IMPORT`; fixtures cover week-1 gate.

## G3 GamerSky + 3DM
- status: GREEN
- tests: tests/test_guide_adapters.py

## G4 DocumentBlocks
- status: GREEN
- tests: tests/test_guide_blocks.py

## G5-G6 DeepSeek text + vision
- status: GREEN (fixture/fake)
- tests: tests/test_guide_llm.py
- Ruling: tests never call live DeepSeek.

## G7-G8 Point Matcher + CV hook
- status: GREEN
- tests: tests/test_guide_match.py
- Ruling: empty-name maps match via path/map_id. CV is no-op unless fixture hash.

## G9-G10 Review + Publisher
- status: GREEN
- tests: tests/test_guide_publish.py
- Ruling: `guides publish` / `sync` never auto-publish.

## G11-G12 Viewer + CLI
- status: GREEN
- tests: tests/test_guide_index.py, tests/test_guides.py, tests/test_guide_cli.py
- Viewer: `/api/v1/guides/index`, `/guide-assets/{sha}`, marker book badge
- CLI: `python -m hsrmap guides {discover,fetch,parse,extract,match,review,publish,sync,import-page}`

## Coverage
- Matcher covers official 48 Floating Grease points from the local snapshot + synthetic section headings.
- Real 4–5 site pages are fixture HTML, not live crawl.
- Official `data/assets` stays read-only.

## Rulings
- Week-1 gate uses local HTML fixtures, not live crawl.
- `guides fetch` without HTML returns `NEEDS_MANUAL_IMPORT`.
- `guides publish` / `sync` never auto-publish.
- Official `data/assets` stays read-only; guide images use `data/guide-assets`.
