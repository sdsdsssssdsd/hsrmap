## What and why

One paragraph: what changes, and what problem it solves.

## Semantics

- [ ] This does NOT change the six-state result for any point, or
- [ ] It does, and a shadow comparison is attached (old vs new, point by point)

## Checks

Paste the last lines of each:

- python -m pytest -q
- python -m ruff check hsrmap tests tools
- python -m hsrmap repo-hygiene
- python -m hsrmap dod

## Notes

Anything a reviewer should know: data migrations, new CLI commands, port changes.
