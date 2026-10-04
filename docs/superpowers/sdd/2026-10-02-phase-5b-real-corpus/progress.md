# Phase 5B progress

## Scope
G0–G4 frozen. This round is canary → extract → golden → review. No 48-point publish.

## Results
- Structural canary fixtures: 7 / 7 PASS
- Real pages raw archived: 5 / 5
- Real import QA: 2 / 5 PASS (17173, GamerSky). 3DM/TapTap/ol.3dm FAIL on local images
- DocumentBlocks chrome nav dropped; QA now checks exact asset files
- DeepSeek real canary page 1: JSON valid, hallucinated steps 0, 2 sections, 0 extracted steps
- Golden 20: 20/20. Real Golden 10: empty
- Point Match Coverage: 48 / 48
- Matcher accuracy reviewed/correct: 0 / 0 (human only)
- Real Guide Coverage: 0 / 48 (nothing APPROVED)
- CV canary: 8 tested, 0 MATCH, 8 NO_MATCH, 0 wrong
- Dedup: 1 cluster, canonical page 1, pages 1+2+3 PROBABLE
- AUTO_ACCEPT treated as AUTO_SUGGEST; 25 items queued
- Review console: `/review`
- Canary publish: FAIL (no human APPROVED)
- Remaining 38: BLOCKED

## Rulings
- Structural canary fixtures (7 pages) stand in until the operator drops browser-saved HTML into `data/guides/inbox/`.
- `auto` match enqueues `AUTO_ACCEPTED` and does not publish. Only `APPROVED` writes snapshot entries.
- DeepSeek live canary runs only when `DEEPSEEK_API_KEY` is present; tests always use fake.
- Coverage JSON must report Point Match and Real Guide separately.
