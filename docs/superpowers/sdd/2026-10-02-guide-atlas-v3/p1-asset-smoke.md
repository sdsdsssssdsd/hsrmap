# P1.1 Asset smoke matrix

same stored pages, command: guides asset-smoke --hosts 3dmgame.com,9game.cn,17173.com --limit 8
the fixed bug was our own extractor (it treated script src as an image), not host blocking.

## before

| Host | images | FETCHED | CACHE | BLOCKED | NOT_IMAGE |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3dmgame.com | 186 | 36 | 54 | 0 | 96 |
| 9game.cn | 256 | 56 | 64 | 0 | 136 |
| 17173.com | 284 | 124 | 44 | 0 | 116 |

## after

| Host | images | FETCHED | CACHE | BLOCKED | NOT_IMAGE | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 3dmgame.com | 360 | 45 | 315 | 0 | 0 | A: generic policy works |
| 9game.cn | 132 | 3 | 129 | 0 | 0 | A: generic policy works |
| 17173.com | 352 | 97 | 255 | 0 | 0 | A: generic policy works |

Conclusion: all three hosts are Case A (generic policy is enough); HOST_POLICIES stays empty.
One image is downloaded once across articles (CACHE_HIT dominates); cache lives in data/guide-cache/.

## P1.3 QA rescan (fixed extractor + fixed block builder)

Two more root causes surfaced while rescanning:

1. `html_to_blocks` read `data-src` / `src` only, while 3DM lazy-loads with
   `data-original` — the fetched asset never matched the block that needed it.
   Both sides now call `pick_image_src` (one priority list).
2. `<img>` tags without a usable source still produced image blocks, and QA
   demanded a stored asset for **every** image. Empty images are dropped now,
   and asset coverage is a ratio (`ASSET_COVERAGE_MIN = 0.9`).

`guides qa-rescan --hosts 3dmgame.com,9game.cn,miyoushe.com,ali213.net,17173.com --apply`:

| result | count |
| --- | ---: |
| pages rescanned | 64 |
| QA_PASS | **62** |
| QA_FAIL | 2 |

The two remaining failures are 米游社 pages with zero content blocks
(`JS_RENDER_REQUIRED`) — recorded as Case C, no browser automation, no
anti-bot work.

`guides qa-quarantine` now finds **0** review items to reject: the historical
"QA_FAIL pollution" was our own extractor, and those pages legitimately pass
once it is fixed. The gate still quarantines any future failing source.
