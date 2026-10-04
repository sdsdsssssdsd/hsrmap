# Contributing

Thanks for taking a look. This project is small but has one non-negotiable rule, so please read the
first section before changing anything.

## The one rule: judgement semantics are frozen

The six-state result (`NO_EVIDENCE` / `SCOPE_ONLY` / `LOCATE_MISSING` / `LOCATE_COMPLETE` /
`SOLVE_MISSING` / `COMPLETE`) is a **contract**, not an implementation detail. Evidence levels,
audit tiers, coverage numbers and UI are annotations around it.

```text
docs/runbooks/baseline-hygiene.json   frozen snapshot: 1006 points, status matrix + sha256
tests/test_claims_shadow.py           verifies the matrix hash per point, for every lookup mode
```

If your change could alter a state, you must prove it doesn't:

1. run the shadow test before and after:
   `python -m pytest --run-data-e2e tests/test_claims_shadow.py -q`;
2. the digest must stay `d5b8bda61715a690…` (see the baseline file);
3. if it genuinely must change, say so explicitly in the PR and explain why the new matrix is correct.

Performance work must keep the structural budget too: `hsrmap guides completeness --stats` prints the
SQL query count, which must not scale with the number of points.

## Getting set up

```bash
python -m pip install -r requirements.lock.txt     # runtime + test dependencies
python -m pip install -r requirements-vision.txt   # optional: opencv template matching, playwright
python -m hsrmap runtime                           # where mutable state lives
python -m hsrmap doctor                            # closed loop: entry points, ports, first paint
```

Optional dependencies are lazily imported on purpose (`try/except ImportError`); a missing extra must
degrade to "capability unavailable", never to an import error at module scope.

## Before you commit

```bash
python -m pytest -q                      # unit + integration, no real data needed
python -m ruff check hsrmap tests tools  # lint
python -m hsrmap repo-hygiene            # no new pollution (baseline: tools/hygiene_baseline.json)
python -m hsrmap dod                     # Definition of Done, 12 machine-checked items
```

All four must be green. `pytest -q` must never write into the repository's `data/` — the test session
points `HSRMAP_DATA_DIR` at a temp directory, and cases that need the real snapshot are marked
`data`/`e2e` and skipped unless you pass `--run-data-e2e`.

## Where things live

| Path | What belongs there |
| --- | --- |
| `hsrmap/guides/stages.py` | the completion model — requirement × evidence → six states |
| `hsrmap/guides/claims.py` | evidence claims: level, grounding tier, asset, inference basis |
| `hsrmap/guides/audit.py` | grounding checks and the image-transcription channel |
| `hsrmap/guides/publishing/` | diff, gates, staging, atomic switch, manifest |
| `hsrmap/guides/topics/<topic>/` | per-topic profiles and seeds (a new collectible type starts here) |
| `hsrmap/viewer_app.py` | the three routers: map (read), guide (read-only, both apps), review (write) |
| `web/src/` | front-end source; `web/dist` is a build product that ships with releases |
| `docs/specs`, `docs/runbooks` | specs and operational notes (Chinese, that is the project convention) |

Runtime data (`data/`, databases, crawled pages, assets, reports) never enters the source tree; the
hygiene checker and `.gitignore` both enforce it.

## Adding a topic

A new collectible type is a directory under `hsrmap/guides/topics/` with a profile (completion
requirement, label names, matcher/vision profile) plus a seed set. Register it, add a golden set, and
make sure the gates still pass — `hsrmap guides closure-check` will tell you whether the new topic is
covered or merely registered.

## Refreshing the screenshots

`docs/images/*.png` are real captures of the running apps (no mockups). Start the two services, then:

```powershell
tools/screenshot.ps1 -MapUrl "http://127.0.0.1:8766/#/map/158?point=1932" \
                     -ReviewUrl "http://127.0.0.1:8767/review" -MonitorUrl "http://127.0.0.1:8768/"
```

The script drives headless Chrome (or Edge) and writes into `docs/images/`. Point the map URL at a
point that actually has evidence, otherwise the drawer in the screenshot says "no guide yet".

## Releases

```bash
python -m hsrmap release            # rebuild submit/ + submit.zip + release-manifest.json
python tools/privacy_scan.py submit # must come back clean before publishing
```

The package is the source tree minus the hygiene-forbidden set, plus `web/dist`. `hsrmap dod` item 6
verifies that the directory and the manifest agree exactly, so never hand-copy files into `submit/`.
See `docs/runbooks/publish-github.md` for the publish flow.

## Conduct

Be precise and be kind. When you disagree with a result, quote the source page or the failing check —
"the engine says X, the official map says Y" is a bug report we can act on.
