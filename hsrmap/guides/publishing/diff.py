"""Publish-time snapshot diff and coverage regression gate.

`publish-snapshot` copies every working entry whose status is `published` into
the published DB and deletes the entries that are no longer published, so a
regression is silent data loss unless the publish path compares both states
first. This module builds that comparison, validates the hard invariants
(bindings resolve in core, assets exist on disk) and turns the result into
blocking reasons for the publish gate.

Terms:

- **current** — the live published DB (`data/guides/published.db`).
- **candidate** — what the working DB would publish right now, i.e. exactly the
  rows `sync_published` would copy (`guide_entry.status = 'published'`).
- **added / removed / changed** — entry ids present only in the candidate, only
  in the published DB, or present in both with a different fingerprint.
- **coverage** — per topic, how many official targets the state covers; computed
  through :func:`hsrmap.guides.ledger.topic_ledger` when official points are
  available, otherwise from the materialized `guide_target_status` ledger.

The gate policy (:func:`blocking_reasons`) is:

- broken binding / missing asset / missing core target → always blocking, even
  with `force`;
- hallucinated or ungrounded step → always blocking (a1-8 六: the published
  corpus must be checkable against its own source, no grandfathering);
- evidence gaps (a1-8 六: transcription without its picture, cross inference
  without basis refs, a required claim without provenance, a declaration that no
  longer matches the text) → always blocking;
- evidence removed for a guide the published snapshot had declared → blocking;
- direct → transcription/inference downgrade → REVIEW REQUIRED, waivable through
  `allowed_regressions.yaml`;
- coverage regression — which includes removing an already published entry —
  blocking by default, tolerated for non gate-protected topics with
  `allow_coverage_drop` (removals included), tolerated for every topic with
  `force`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ledger import build_gates, topic_ledger
from hsrmap.guides.topics.loader import list_topics
from hsrmap.paths import GUIDE_ASSETS, GUIDE_DB

#: The one status `sync_published` copies into the published DB.
PUBLISHABLE_STATUS = "published"

DIFF_REPORT_PATH = GUIDE_DB.parent / "reports" / "publish-diff.json"
DIFF_MARKDOWN_PATH = GUIDE_DB.parent / "reports" / "snapshot-diff.md"
MANIFEST_PATH = GUIDE_DB.parent / "reports" / "snapshot-manifest.json"

#: Snapshot manifest schema (a1-6 §16；4 = 加入 evidence digest，见 a1-8 六).
MANIFEST_SCHEMA_VERSION = 4

#: Change vocabulary (a1-6 §17) — one type per line of a snapshot diff.
CHANGE_TYPES = (
    "TARGET_ADDED",
    "TARGET_REMOVED",
    "GUIDE_ADDED",
    "GUIDE_REMOVED",
    "GUIDE_CHANGED",
    "BINDING_ADDED",
    "BINDING_REMOVED",
    "BINDING_CHANGED",
    "ASSET_ADDED",
    "ASSET_REMOVED",
    "COVERAGE_INCREASED",
    "COVERAGE_DECREASED",
    #: 步骤文字没变，证据等级却更间接了（a1-8 六）。
    "EVIDENCE_DOWNGRADED",
)

#: Changes that only need to be known, never to be blocked (a1-6 §18).
REVIEW_CHANGE_TYPES = frozenset(
    {
        "GUIDE_ADDED",
        "GUIDE_CHANGED",
        "GUIDE_MERGED",
        "BINDING_ADDED",
        "BINDING_CHANGED",
        "ASSET_ADDED",
        "COVERAGE_INCREASED",
        #: 退化要人过目，但可以在 allowed_regressions.yaml 里明确豁免。
        "EVIDENCE_DOWNGRADED",
    }
)

#: Intentional regressions are declared here (a1-6 §19).
ALLOWED_REGRESSIONS_PATH = GUIDE_DB.parent / "allowed_regressions.yaml"

#: Tables a published snapshot must expose to be valid.
REQUIRED_TABLES = ("guide_entry", "guide_steps", "guide_assets")


class PublishBlocked(RuntimeError):
    """Raised when a candidate snapshot would regress or break the published DB."""

    def __init__(self, reasons: list[str], report: dict[str, Any] | None = None) -> None:
        self.reasons = list(reasons)
        self.report = report or {}
        super().__init__("; ".join(self.reasons) or "publish blocked")


# --------------------------------------------------------------------------- #
# state extraction
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entry_snapshot(db: GuideDatabase) -> dict[int, dict[str, Any]]:
    """Publishable rows of one DB, with the content that identity depends on."""
    out: dict[int, dict[str, Any]] = {}
    for row in db.conn.execute(
        """
        SELECT id, source_point_id, title, point_stable_key, source_url,
               IFNULL(source_kind, '') AS source_kind, IFNULL(status, '') AS status
        FROM guide_entry WHERE IFNULL(status, '') = ? ORDER BY id
        """,
        (PUBLISHABLE_STATUS,),
    ):
        guide_id = int(row["id"])
        steps = [
            str(item["text"] or "")
            for item in db.conn.execute(
                "SELECT text FROM guide_steps WHERE guide_id = ? ORDER BY step_index, id",
                (guide_id,),
            )
        ]
        asset_rows = [
            {"sha256": str(item["asset_sha256"] or ""), "step_index": int(item["step_index"] or 0)}
            for item in db.conn.execute(
                "SELECT asset_sha256, step_index FROM guide_assets WHERE guide_id = ? ORDER BY step_index, id",
                (guide_id,),
            )
            if item["asset_sha256"]
        ]
        assets = [row["sha256"] for row in asset_rows]
        body = {
            "id": guide_id,
            "source_point_id": str(row["source_point_id"] or ""),
            "title": str(row["title"] or ""),
            "point_stable_key": str(row["point_stable_key"] or ""),
            "source_url": str(row["source_url"] or ""),
            #: 审计要用它区分「社区攻略对来源页」和「官方点位条目对官方点位说明」。
            #: 它不参与指纹：来源种类变了但内容没变，不该算一次 GUIDE_CHANGED。
            "source_kind": str(row["source_kind"] or ""),
            "steps": steps,
            "assets": assets,
            "asset_rows": asset_rows,
        }
        body["fingerprint"] = _fingerprint(body)
        out[guide_id] = body
    return out


def _fingerprint(body: dict[str, Any]) -> str:
    payload = json.dumps(
        [body["source_point_id"], body["title"], body["steps"], body["assets"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _published_ids(db: GuideDatabase) -> set[str]:
    return {
        str(row["source_point_id"])
        for row in db.conn.execute(
            "SELECT source_point_id FROM guide_entry WHERE IFNULL(status, '') = ?",
            (PUBLISHABLE_STATUS,),
        )
        if row["source_point_id"]
    }


def _entry_brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "source_point_id": entry["source_point_id"],
        "title": entry["title"],
        "steps": len(entry["steps"]),
        "assets": len(entry["assets"]),
    }


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #

def diff_entries(
    current: dict[int, dict[str, Any]],
    candidate: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """added / removed / changed entries between two snapshots."""
    added = sorted(set(candidate) - set(current))
    removed = sorted(set(current) - set(candidate))
    changed: list[dict[str, Any]] = []
    for guide_id in sorted(set(current) & set(candidate)):
        before, after = current[guide_id], candidate[guide_id]
        if before["fingerprint"] == after["fingerprint"]:
            continue
        fields = [
            name
            for name in ("source_point_id", "title", "steps", "assets")
            if before[name] != after[name]
        ]
        changed.append({"id": guide_id, "fields": fields, "before": _entry_brief(before), "after": _entry_brief(after)})
    return {
        "added": [_entry_brief(candidate[guide_id]) for guide_id in added],
        "removed": [_entry_brief(current[guide_id]) for guide_id in removed],
        "changed": changed,
        "unchanged": len(set(current) & set(candidate)) - len(changed),
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }


def diff_assets(
    current: dict[int, dict[str, Any]],
    candidate: dict[int, dict[str, Any]],
    *,
    assets_root: Path | None,
) -> dict[str, Any]:
    """Asset-set diff plus the on-disk existence check for the candidate."""
    current_shas = {sha for entry in current.values() for sha in entry["assets"]}
    candidate_shas = {sha for entry in candidate.values() for sha in entry["assets"]}
    changed = sorted(
        guide_id
        for guide_id in set(current) & set(candidate)
        if current[guide_id]["assets"] != candidate[guide_id]["assets"]
    )
    root = Path(assets_root) if assets_root is not None else None
    missing: list[str] = []
    if root is not None:
        missing = sorted(sha for sha in candidate_shas if not _asset_file(root, sha))
    return {
        "added": sorted(candidate_shas - current_shas),
        "removed": sorted(current_shas - candidate_shas),
        "changed": changed,
        "missing_files": missing,
        "files_checked": len(candidate_shas),
        "checked": root is not None,
        "counts": {
            "current": len(current_shas),
            "candidate": len(candidate_shas),
            "missing": len(missing),
        },
    }


def _asset_file(root: Path, sha: str) -> Path | None:
    folder = root / sha[:2]
    if not folder.is_dir():
        return None
    for path in folder.glob(f"{sha}.*"):
        if path.is_file():
            return path
    return None


def validate_bindings(keys: set[str], ctx: Any) -> list[dict[str, Any]]:
    """Bindings that do not resolve in core.

    Point ids must exist in `points.source_id`; a `map:` key must carry a map id
    that exists in `maps.source_id`; a `set:` key names its member points in the
    key itself (`set:4139-4142:topic:…`), so every member is checked; `global:`
    keys target no official row and always resolve.
    """
    if ctx is None:
        return []
    broken: list[dict[str, Any]] = []
    for key in sorted(keys):
        if not key or key.startswith("global:"):
            continue
        if key.startswith("set:"):
            parts = key.split(":")
            members = [member for member in (parts[1] if len(parts) > 1 else "").split("-") if member]
            missing = [
                member
                for member in members
                if ctx.core.conn.execute(
                    "SELECT 1 FROM points WHERE source_id = ? LIMIT 1", (member,)
                ).fetchone()
                is None
            ]
            if missing:
                broken.append({"key": key, "kind": "set", "missing": ",".join(missing)})
            continue
        if key.startswith("map:"):
            parts = key.split(":")
            map_id = parts[1] if len(parts) > 1 else ""
            found = ctx.core.conn.execute(
                "SELECT 1 FROM maps WHERE source_id = ? LIMIT 1", (map_id,)
            ).fetchone()
            if found is None:
                broken.append({"key": key, "kind": "map", "missing": map_id})
            continue
        found = ctx.core.conn.execute(
            "SELECT 1 FROM points WHERE source_id = ? LIMIT 1", (key,)
        ).fetchone()
        if found is None:
            broken.append({"key": key, "kind": "point", "missing": key})
    return broken


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #

def _enabled_topics() -> list[str]:
    try:
        return [str(item["topic_key"]) for item in list_topics(enabled_only=True)]
    except Exception:  # pragma: no cover - a broken registry must not break publishing
        return []


def _key_belongs_to_topic(key: str, topic_key: str) -> bool:
    if key == f"global:topic:{topic_key}":
        return True
    return key.startswith(("map:", "set:")) and key.endswith(f":topic:{topic_key}")


def _ledger_point_ids(working: GuideDatabase, topic_key: str) -> list[str]:
    return [
        str(row["source_point_id"])
        for row in working.conn.execute(
            "SELECT source_point_id FROM guide_target_status WHERE topic_key = ? ORDER BY target_key",
            (topic_key,),
        )
        if row["source_point_id"]
    ]


def topic_coverage(
    working: GuideDatabase,
    published_db: GuideDatabase,
    *,
    ctx: Any = None,
    official_points: dict[str, list[dict[str, Any]]] | None = None,
    topics: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per topic covered/official target counts for one published state."""
    explicit = official_points or {}
    out: dict[str, dict[str, Any]] = {}
    for key in topics if topics is not None else _enabled_topics():
        points = explicit.get(key)
        if points is None and ctx is not None:
            try:
                from hsrmap.guides.topics.official import official_points_for_topic

                points = official_points_for_topic(key, ctx=ctx)
            except Exception:
                points = None
        if points:
            report = topic_ledger(working, key, official_points=points, published_db=published_db)
            out[key] = {
                "covered": int(report.get("published") or 0),
                "targets": int(report.get("official_targets") or 0),
                "basis": "official",
            }
            continue
        ledger_ids = _ledger_point_ids(working, key)
        if ledger_ids:
            published_ids = _published_ids(published_db)
            out[key] = {
                "covered": sum(1 for pid in ledger_ids if pid in published_ids),
                "targets": len(ledger_ids),
                "basis": "ledger",
            }
            continue
        published_ids = _published_ids(published_db)
        covered = sum(1 for pid in published_ids if _key_belongs_to_topic(pid, key))
        out[key] = {"covered": covered, "targets": covered, "basis": "entries"}
    return out


def diff_coverage(
    current: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    topics: dict[str, dict[str, Any]] = {}
    regressions: list[str] = []
    improvements: list[str] = []
    for key in sorted(set(current) | set(candidate)):
        before = current.get(key) or {"covered": 0, "targets": 0, "basis": "unknown"}
        after = candidate.get(key) or {"covered": 0, "targets": 0, "basis": "unknown"}
        delta = int(after["covered"]) - int(before["covered"])
        topics[key] = {
            "current": int(before["covered"]),
            "candidate": int(after["covered"]),
            "delta": delta,
            "targets": int(after["targets"]),
            "basis": after["basis"],
        }
        if delta < 0:
            regressions.append(key)
        elif delta > 0:
            improvements.append(key)
    return {"topics": topics, "regressions": regressions, "improvements": improvements}


# --------------------------------------------------------------------------- #
# report + gate
# --------------------------------------------------------------------------- #

def snapshot_diff(
    working: GuideDatabase,
    published: GuideDatabase,
    *,
    ctx: Any = None,
    assets_root: Path | None = GUIDE_ASSETS,
    official_points: dict[str, list[dict[str, Any]]] | None = None,
    topics: list[str] | None = None,
    coverage: bool = True,
    gates: bool = True,
    core_check: bool = True,
    waivers: list[dict[str, Any]] | None = None,
    audit: bool = True,
    evidence: bool = True,
) -> dict[str, Any]:
    """Compare the live published DB with what the working DB would publish."""
    current = entry_snapshot(published)
    candidate = entry_snapshot(working)
    report: dict[str, Any] = {
        "generated_at": _now(),
        "published_db": str(published.path),
        "working_db": str(working.path),
        "totals": {
            "current_entries": len(current),
            "candidate_entries": len(candidate),
        },
        "entries": diff_entries(current, candidate),
        "assets": diff_assets(current, candidate, assets_root=assets_root),
    }
    keys = {entry["source_point_id"] for entry in candidate.values()}
    keys |= {entry["source_point_id"] for entry in current.values()}
    report["bindings"] = {
        "changed": sorted(
            guide_id
            for guide_id in set(current) & set(candidate)
            if current[guide_id]["source_point_id"] != candidate[guide_id]["source_point_id"]
        ),
        "broken": validate_bindings(keys, ctx) if core_check else [],
        "core_checked": bool(ctx is not None and core_check),
    }
    current_cov: dict[str, dict[str, Any]] = {}
    candidate_cov: dict[str, dict[str, Any]] = {}
    if coverage:
        current_cov = topic_coverage(
            working, published, ctx=ctx, official_points=official_points, topics=topics
        )
        candidate_cov = topic_coverage(
            working,
            _candidate_view(working),
            ctx=ctx,
            official_points=official_points,
            topics=topics,
        )
        report["coverage"] = diff_coverage(current_cov, candidate_cov)
    else:
        report["coverage"] = {"topics": {}, "regressions": [], "improvements": [], "skipped": True}
    if gates and report["coverage"].get("topics"):
        summaries = {
            key: {"published": int(item["candidate"])}
            for key, item in report["coverage"]["topics"].items()
        }
        current_summaries = {
            key: {"published": int(item["current"])}
            for key, item in report["coverage"]["topics"].items()
        }
        report["gates"] = {
            "current": build_gates(current_summaries),
            "candidate": build_gates(summaries),
        }
        report["gates"]["protected_regressions"] = _protected_regressions(
            report["gates"], set(report["coverage"]["topics"])
        )
    else:
        report["gates"] = {"current": None, "candidate": None, "protected_regressions": [], "skipped": True}
    if audit:
        from hsrmap.guides.audit import audit_entries

        report["audit"] = audit_entries(candidate, working, assets_root=assets_root)
        fresh = {
            guide_id
            for guide_id in candidate
            if guide_id not in current
            or current[guide_id]["fingerprint"] != candidate[guide_id]["fingerprint"]
        }
        #: 新旧仍然分开报（closure 要看的债和这次发布会引入的问题是两件事），
        #: 但自从 a1-8 六起，两者都是 HARD FAIL：带捏造步骤的快照不允许发布。
        report["audit"]["new_hallucinations"] = [
            item for item in report["audit"]["hallucinated"] if item["guide_id"] in fresh
        ]
        report["audit"]["pre_existing_hallucinations"] = len(
            report["audit"]["hallucinated"]
        ) - len(report["audit"]["new_hallucinations"])
    else:
        report["audit"] = {
            "guides_checked": 0,
            "counts": {},
            "findings": [],
            "hallucinated": [],
            "ungrounded": [],
            "new_hallucinations": [],
            "pre_existing_hallucinations": 0,
            "skipped": True,
        }
    #: 证据层要在硬错误之前算：证据缺口本身就是硬错误（a1-8 六）。
    report["evidence"] = _evidence_section(
        working, published, current, candidate, assets_root, enabled=evidence
    )
    report["hard_errors"] = _hard_errors(report)
    report["waivers"] = list(waivers or [])
    topic_of = _topic_of(working)
    report["changes"] = classify_changes(
        current, candidate, report.get("coverage"), topic_of=topic_of
    )
    report["changes"].extend(_evidence_changes(report, candidate, topic_of))
    report["change_counts"] = change_counts(report["changes"])
    manifest_evidence = report["evidence"]
    report["manifest"] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "current_digest": manifest_digest(
            _manifest_body(
                current, current_cov if coverage else {}, manifest_evidence["current"]["by_guide"]
            )
        ),
        "candidate_digest": manifest_digest(
            _manifest_body(
                candidate, candidate_cov if coverage else {}, manifest_evidence["candidate"]["by_guide"]
            )
        ),
    }
    report["gate"] = evaluate_gate(report, waivers=report["waivers"])
    report["blocking_reasons"] = report["gate"]["reasons"]
    report["ok"] = not report["blocking_reasons"]
    return report


def _evidence_section(
    working: GuideDatabase,
    published: GuideDatabase,
    current: dict[int, dict[str, Any]],
    candidate: dict[int, dict[str, Any]],
    assets_root: Path | None,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """证据层：候选快照的声明能不能发布，以及相对已发布快照变了什么（a1-8 六）。"""
    empty = {
        "adopted": False,
        "claims": 0,
        "guides": 0,
        "steps": 0,
        "by_level": {},
        "counts": {},
        "findings": [],
        "assets_checked": False,
        "downgrades": [],
        "upgrades": [],
        "losses": [],
        "current": {"claims": 0, "digest": "", "by_level": {}, "by_guide": {}},
        "candidate": {"claims": 0, "digest": "", "by_level": {}, "by_guide": {}},
    }
    if not enabled:
        return {**empty, "skipped": True}
    from hsrmap.guides import claims as claims_mod

    candidate_ids = [int(guide_id) for guide_id in candidate]
    current_ids = [int(guide_id) for guide_id in current]
    candidate_rows = claims_mod.claim_rows(working, candidate_ids)
    current_rows = claims_mod.claim_rows(published, current_ids)
    gate = claims_mod.evidence_gate(
        working, guide_ids=candidate_ids, assets_root=assets_root
    )
    moves = claims_mod.evidence_moves(current_rows, candidate_rows)
    return {
        "adopted": gate["adopted"],
        "claims": gate["claims"],
        "guides": gate["guides"],
        "steps": gate["steps"],
        "by_level": gate["by_level"],
        "counts": gate["counts"],
        "findings": gate["findings"],
        "assets_checked": gate["assets_checked"],
        "downgrades": moves["downgrades"],
        "upgrades": moves["upgrades"],
        "losses": claims_mod.evidence_losses(current_rows, candidate_rows),
        "current": {
            "claims": len(current_rows),
            "digest": claims_mod.digest_of_claims(current_rows),
            "by_level": claims_mod.level_counts(current_rows),
            "by_guide": claims_mod.digests_by_guide(current_rows),
        },
        "candidate": {
            "claims": len(candidate_rows),
            "digest": claims_mod.digest_of_claims(candidate_rows),
            "by_level": claims_mod.level_counts(candidate_rows),
            "by_guide": claims_mod.digests_by_guide(candidate_rows),
        },
    }


def _evidence_changes(
    report: dict[str, Any],
    candidate: dict[int, dict[str, Any]],
    topic_of: dict[str, str],
) -> list[dict[str, Any]]:
    """证据退化进 change 列表，才能被三层 gate 当成 REVIEW REQUIRED 处理。"""
    changes: list[dict[str, Any]] = []
    for item in (report.get("evidence") or {}).get("downgrades") or []:
        guide_id = int(item["guide_id"])
        entry = candidate.get(guide_id) or {}
        target = str(entry.get("source_point_id") or "")
        changes.append({
            "type": "EVIDENCE_DOWNGRADED",
            "guide_id": guide_id,
            "step_id": item.get("step_id"),
            "target": target,
            "topic": topic_of.get(target),
            "from": item.get("from"),
            "to": item.get("to"),
            "direct_to_indirect": bool(item.get("direct_to_indirect")),
        })
    return changes


def _topic_of(working: GuideDatabase) -> dict[str, str]:
    """target/source point id -> topic key, from the materialized ledger."""
    out: dict[str, str] = {}
    for row in working.conn.execute(
        "SELECT topic_key, source_point_id FROM guide_target_status WHERE IFNULL(source_point_id, '') <> ''"
    ):
        out.setdefault(str(row["source_point_id"]), str(row["topic_key"]))
    return out


def _candidate_view(working: GuideDatabase) -> GuideDatabase:
    """An in-memory published DB holding exactly the candidate entries."""
    view = GuideDatabase(Path(":memory:"))
    for row in working.conn.execute(
        """
        SELECT id, point_stable_key, source_point_id, title, status
        FROM guide_entry WHERE IFNULL(status, '') = ? ORDER BY id
        """,
        (PUBLISHABLE_STATUS,),
    ):
        view.conn.execute(
            """
            INSERT INTO guide_entry(id, point_stable_key, source_point_id, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                row["point_stable_key"],
                row["source_point_id"],
                row["title"],
                row["status"],
                _now(),
                _now(),
            ),
        )
    view.conn.commit()
    return view


def _gate_topic_states(gates: dict[str, Any] | None) -> dict[str, str]:
    states: dict[str, str] = {}
    for section in ("A", "B", "C"):
        block = (gates or {}).get(section) or {}
        for item in block.get("topics") or []:
            states[str(item.get("topic"))] = str(item.get("engine") or "")
    return states


def _protected_regressions(gates: dict[str, Any], topics: set[str] | None = None) -> list[str]:
    """Gate topics that PASS today and would not PASS in the candidate.

    Gate C also lists scope rows (`POINT`, `MAP_LABEL`), which duplicate the
    topic they are proven by; `topics` keeps the result to real topic keys.
    """
    before = _gate_topic_states(gates.get("current"))
    after = _gate_topic_states(gates.get("candidate"))
    return sorted(
        topic
        for topic, state in before.items()
        if state == "PASS"
        and after.get(topic) != "PASS"
        and (topics is None or topic in topics)
    )


def snapshot_manifest(
    db: GuideDatabase,
    *,
    ctx: Any = None,
    official_points: dict[str, list[dict[str, Any]]] | None = None,
    topics: list[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """The manifest of one published snapshot (a1-6 §16, schema 3).

    Holds exactly what a diff or an audit needs to identify a snapshot: per-topic
    coverage, every guide with its content hash and step count, its binding
    target, and the asset hashes it references.
    """
    from hsrmap.guides import claims as claims_mod

    entries = entry_snapshot(db)
    coverage = topic_coverage(db, db, ctx=ctx, official_points=official_points, topics=topics)
    rows = claims_mod.claim_rows(db, [int(guide_id) for guide_id in entries])
    by_guide = claims_mod.digests_by_guide(rows)
    body = _manifest_body(entries, coverage, by_guide)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": created_at or _now(),
        "database": str(getattr(db, "path", "")),
        "topics": {
            key: {**value, "basis": coverage[key]["basis"]} for key, value in body["topics"].items()
        },
        "entries": {
            key: {**value, "title": entries[int(key)]["title"]} for key, value in body["entries"].items()
        },
        "assets": body["assets"],
        "bindings": body["bindings"],
        #: 证据摘要（a1-8 六）：步骤文字一个字没变、等级却退化的快照，digest 会不同。
        "evidence": {
            "digest": claims_mod.digest_of_claims(rows),
            "by_guide": by_guide,
            "summary": claims_mod.summary(db) if rows else {"total": 0},
        },
    }


def _manifest_body(
    entries: dict[int, dict[str, Any]],
    coverage: dict[str, dict[str, Any]],
    evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The identifying parts of a snapshot, without timestamps or paths."""
    body: dict[str, Any] = {}
    bindings: dict[str, str] = {}
    assets: set[str] = set()
    for guide_id, entry in sorted(entries.items()):
        body[str(guide_id)] = {
            "target": entry["source_point_id"],
            "content_sha256": entry["fingerprint"],
            "step_count": len(entry["steps"]),
            "assets": sorted(entry["assets"]),
        }
        bindings[str(guide_id)] = entry["source_point_id"]
        assets.update(entry["assets"])
    return {
        "topics": {
            key: {"covered": int(item["covered"]), "targets": int(item["targets"])}
            for key, item in sorted(coverage.items())
        },
        "entries": body,
        "assets": sorted(assets),
        "bindings": bindings,
        #: 逐条目的证据摘要。步骤文字没变但等级退化时，条目指纹不变、这一项会变。
        "evidence": {str(key): value for key, value in sorted((evidence or {}).items())},
    }


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Stable digest of the identifying parts of a manifest."""
    payload = json.dumps(
        {
            "topics": manifest.get("topics") or {},
            "entries": manifest.get("entries") or {},
            "assets": manifest.get("assets") or [],
            "bindings": manifest.get("bindings") or {},
            "evidence": manifest.get("evidence") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_problems(db: GuideDatabase) -> list[str]:
    """Structural problems that make a snapshot unpublishable."""
    problems: list[str] = []
    tables = {
        str(row["name"])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table in REQUIRED_TABLES:
        if table not in tables:
            problems.append(f"schema invalid: missing table {table}")
    if problems:
        return problems
    empty_bindings = int(
        db.conn.execute(
            "SELECT COUNT(*) AS c FROM guide_entry WHERE IFNULL(source_point_id, '') = ''"
        ).fetchone()["c"]
    )
    if empty_bindings:
        problems.append(f"schema invalid: {empty_bindings} entries without a binding target")
    return problems


def classify_changes(
    current: dict[int, dict[str, Any]],
    candidate: dict[int, dict[str, Any]],
    coverage: dict[str, Any] | None = None,
    *,
    topic_of: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Typed change list between two snapshots (a1-6 §17)."""
    topic_of = topic_of or {}
    changes: list[dict[str, Any]] = []
    current_targets = {entry["source_point_id"] for entry in current.values()}
    candidate_targets = {entry["source_point_id"] for entry in candidate.values()}

    for guide_id in sorted(set(candidate) - set(current)):
        entry = candidate[guide_id]
        changes.append({"type": "GUIDE_ADDED", "guide_id": guide_id, "target": entry["source_point_id"]})
    candidate_by_target: dict[str, list[int]] = {}
    for guide_id, entry in candidate.items():
        candidate_by_target.setdefault(str(entry["source_point_id"]), []).append(int(guide_id))
    for guide_id in sorted(set(current) - set(candidate)):
        entry = current[guide_id]
        # a duplicate guide for a target that still has one is a merge, not a loss:
        # the thicker guide took its place and no published content disappeared
        survivors = sorted(
            other
            for other in candidate_by_target.get(str(entry["source_point_id"]), [])
            if other != int(guide_id)
        )
        if survivors:
            changes.append({
                "type": "GUIDE_MERGED",
                "guide_id": guide_id,
                "target": entry["source_point_id"],
                "topic": topic_of.get(entry["source_point_id"]),
                "survivor": survivors[0],
            })
            continue
        changes.append({
            "type": "GUIDE_REMOVED",
            "guide_id": guide_id,
            "target": entry["source_point_id"],
            "topic": topic_of.get(entry["source_point_id"]),
        })
    for guide_id in sorted(set(current) & set(candidate)):
        before, after = current[guide_id], candidate[guide_id]
        if before["fingerprint"] == after["fingerprint"]:
            continue
        fields = [name for name in ("source_point_id", "title", "steps", "assets") if before[name] != after[name]]
        changes.append({
            "type": "GUIDE_CHANGED",
            "guide_id": guide_id,
            "target": after["source_point_id"],
            "fields": fields,
        })
        if "source_point_id" in fields:
            changes.append({
                "type": "BINDING_CHANGED",
                "guide_id": guide_id,
                "from": before["source_point_id"],
                "to": after["source_point_id"],
            })

    for target in sorted(candidate_targets - current_targets):
        changes.append({"type": "TARGET_ADDED", "target": target, "topic": topic_of.get(target)})
    for target in sorted(current_targets - candidate_targets):
        changes.append({"type": "TARGET_REMOVED", "target": target, "topic": topic_of.get(target)})

    current_assets = {sha for entry in current.values() for sha in entry["assets"]}
    candidate_assets = {sha for entry in candidate.values() for sha in entry["assets"]}
    for sha in sorted(candidate_assets - current_assets):
        changes.append({"type": "ASSET_ADDED", "assets": [sha]})
    for sha in sorted(current_assets - candidate_assets):
        changes.append({"type": "ASSET_REMOVED", "assets": [sha]})

    for topic, item in sorted(((coverage or {}).get("topics") or {}).items()):
        delta = int(item.get("delta") or 0)
        if delta > 0:
            changes.append({"type": "COVERAGE_INCREASED", "topic": topic, "delta": delta, "covered": item.get("candidate")})
        elif delta < 0:
            changes.append({"type": "COVERAGE_DECREASED", "topic": topic, "delta": delta, "covered": item.get("candidate")})
    return changes


def change_counts(changes: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in CHANGE_TYPES}
    for change in changes:
        name = str(change.get("type") or "")
        counts[name] = counts.get(name, 0) + 1
    return counts


def load_waivers(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the allowed_regressions list (a1-6 §19); an absent file waives nothing."""
    target = Path(path) if path is not None else ALLOWED_REGRESSIONS_PATH
    if not target.exists():
        return []
    try:
        import yaml

        body = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    entries = body.get("allowed_regressions") if isinstance(body, dict) else body
    out = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append({str(key): value for key, value in entry.items()})
    return out


def match_waiver(change: dict[str, Any], waivers: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The waiver that names this change, if any (topic and target must match)."""
    target = str(change.get("target") or "")
    topic = str(change.get("topic") or "")
    for waiver in waivers or []:
        want_target = str(waiver.get("target") or "")
        want_topic = str(waiver.get("topic") or "")
        if want_target and want_target != target:
            continue
        if want_topic and topic and want_topic != topic:
            continue
        if want_target or want_topic:
            return waiver
    return None


def evaluate_gate(
    report: dict[str, Any],
    *,
    waivers: list[dict[str, Any]] | None = None,
    allow_coverage_drop: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Three-tier gate (a1-6 §18): HARD / COVERAGE / REVIEW REQUIRED."""
    hard = list(report.get("hard_errors") or [])
    coverage: list[str] = []
    review: list[dict[str, Any]] = []
    waived: list[dict[str, Any]] = []
    protected = set((report.get("gates") or {}).get("protected_regressions") or [])
    topic_state = ((report.get("coverage") or {}).get("topics") or {})

    def _policy_allows(topic: str | None) -> bool:
        """force allows everything; allow_coverage_drop allows all but protected topics."""
        if force:
            return True
        if allow_coverage_drop and (not topic or topic not in protected):
            return True
        return False

    waived_by_topic: dict[str, int] = {}
    for change in report.get("changes") or []:
        if change.get("type") in {"TARGET_REMOVED", "GUIDE_REMOVED"}:
            topic_key = str(change.get("topic") or "")
            if topic_key and match_waiver(change, waivers) is not None:
                waived_by_topic[topic_key] = waived_by_topic.get(topic_key, 0) + 1

    for change in report.get("changes") or []:
        kind = str(change.get("type"))
        topic = str(change.get("topic") or "") or None
        if kind == "TARGET_REMOVED":
            waiver = match_waiver(change, waivers)
            if waiver is not None:
                waived.append({**change, "waiver": waiver})
                continue
            if _policy_allows(topic):
                continue
            coverage.append(f"published target removed: {change.get('target')}")
        elif kind == "GUIDE_REMOVED":
            waiver = match_waiver(change, waivers)
            if waiver is not None:
                waived.append({**change, "waiver": waiver})
                continue
            if _policy_allows(topic):
                continue
            coverage.append(f"published guide removed: {change.get('guide_id')}")
        elif kind == "COVERAGE_DECREASED":
            if _policy_allows(topic):
                continue
            waived_drop = waived_by_topic.get(str(topic or ""), 0)
            if waived_drop and waived_drop >= abs(int(change.get("delta") or 0)):
                waived.append({**change, "waiver": {"reason": "covered by waived target removals"}})
                continue
            item = topic_state.get(topic or "") or {}
            coverage.append(
                f"coverage regression: {topic} {item.get('current')} -> {item.get('candidate')}"
                + (" (gate protected)" if topic in protected else "")
            )
        elif kind in REVIEW_CHANGE_TYPES:
            #: 证据退化是可以豁免的：明确写进 allowed_regressions.yaml 才算数。
            waiver = match_waiver(change, waivers) if kind == "EVIDENCE_DOWNGRADED" else None
            if waiver is not None:
                waived.append({**change, "waiver": waiver})
                continue
            review.append(change)

    removed_unwaived = sum(
        1
        for change in report.get("changes") or []
        if change.get("type") == "GUIDE_REMOVED" and match_waiver(change, waivers) is None
    )
    if removed_unwaived and not _policy_allows(None):
        coverage.append(f"removed published entries: {removed_unwaived}")
    reasons = list(hard) + coverage
    if force:
        reasons = list(hard)
    return {
        "hard_fail": hard,
        "coverage_fail": coverage,
        "review_required": review,
        "waived": waived,
        "reasons": reasons,
        "result": "FAIL" if reasons else ("REVIEW" if review else "PASS"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable snapshot diff (a1-6 §17 wants json and md)."""
    counts = report.get("entries", {}).get("counts", {})
    gate = report.get("gate") or {}
    lines = [
        "# Snapshot diff",
        "",
        f"- generated: {report.get('generated_at')}",
        f"- published: {report.get('published_db')}",
        f"- candidate: {report.get('working_db')}",
        f"- result: **{gate.get('result', 'n/a')}**",
        "",
        "## Entries",
        "",
        f"- added: {counts.get('added', 0)}",
        f"- removed: {counts.get('removed', 0)}",
        f"- changed: {counts.get('changed', 0)}",
        f"- unchanged: {report.get('entries', {}).get('unchanged', 0)}",
        "",
        "## Changes",
        "",
        "| type | detail |",
        "| --- | --- |",
    ]
    for change in (report.get("changes") or [])[:200]:
        detail = change.get("target") or change.get("topic") or change.get("guide_id") or ""
        if change.get("type") == "GUIDE_CHANGED":
            detail = f"{change.get('guide_id')} ({', '.join(change.get('fields') or [])})"
        lines.append(f"| {change.get('type')} | {detail} |")
    lines += [
        "",
        "## Coverage",
        "",
        "| topic | current | candidate | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for topic, item in sorted(((report.get("coverage") or {}).get("topics") or {}).items()):
        lines.append(f"| {topic} | {item.get('current')} | {item.get('candidate')} | {item.get('delta')} |")
    evidence = report.get("evidence") or {}
    if evidence and not evidence.get("skipped"):
        lines += [
            "",
            "## Evidence",
            "",
            f"- claims: {evidence.get('claims', 0)} (published {evidence.get('current', {}).get('claims', 0)})",
            f"- digest: {evidence.get('current', {}).get('digest', '')[:16]} -> "
            f"{evidence.get('candidate', {}).get('digest', '')[:16]}",
        ]
        for level, count in sorted((evidence.get("candidate", {}).get("by_level") or {}).items()):
            lines.append(f"- {level}: {count}")
        for item in evidence.get("findings") or []:
            lines.append(f"- **HARD** {item.get('problem')} guide {item.get('guide_id')} {item.get('detail', '')}")
        for item in evidence.get("downgrades") or []:
            lines.append(
                f"- REVIEW REQUIRED EVIDENCE_DOWNGRADED guide {item.get('guide_id')} "
                f"step {item.get('step_id')}: {item.get('from')} -> {item.get('to')}"
            )
    lines += ["", "## Gate", ""]
    for tier in ("hard_fail", "coverage_fail"):
        for reason in gate.get(tier) or []:
            lines.append(f"- **{tier.upper()}** {reason}")
    for change in gate.get("review_required") or []:
        lines.append(f"- REVIEW REQUIRED {change.get('type')} {change.get('guide_id') or change.get('topic') or ''}")
    for entry in gate.get("waived") or []:
        waiver = entry.get("waiver") or {}
        lines.append(f"- WAIVED {entry.get('target')} ({waiver.get('reason', '')})")
    return "\n".join(lines) + "\n"


def write_diff_reports(
    report: dict[str, Any],
    *,
    json_path: Path | None = None,
    md_path: Path | None = None,
) -> dict[str, str]:
    """Write both the json and the markdown snapshot diff."""
    json_target = write_diff_report(report, json_path)
    md_target = Path(md_path) if md_path is not None else DIFF_MARKDOWN_PATH
    md_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_target), "markdown": str(md_target)}


def _hard_errors(report: dict[str, Any]) -> list[str]:
    """一律阻断的错误：绑定、磁盘、捏造/无据步骤，以及证据层的每一处缺口。

    a1-8 六 之前，捏造步骤只有「本次新引入的」才阻断；现在只要候选快照里有，
    就 HARD FAIL——发布出去的东西必须能对着它自己的来源查。
    """
    errors = [f"broken binding: {item['key']}" for item in report["bindings"]["broken"]]
    errors += [f"missing asset: {sha}" for sha in report["assets"]["missing_files"]]
    audit = report.get("audit") or {}
    for name, label in (("hallucinated", "hallucinated step"), ("ungrounded", "ungrounded step")):
        errors += [f"{label}: guide {item['guide_id']}" for item in audit.get(name) or []]
    evidence = report.get("evidence") or {}
    for item in evidence.get("findings") or []:
        where = f"guide {item['guide_id']}"
        if item.get("step_id") is not None:
            where += f" step {item['step_id']}"
        if item.get("detail"):
            where += f" ({item['detail']})"
        errors.append(f"{str(item['problem']).lower()}: {where}")
    errors += [
        f"evidence removed: guide {item['guide_id']}" for item in evidence.get("losses") or []
    ]
    return errors


def blocking_reasons(
    report: dict[str, Any],
    *,
    allow_coverage_drop: bool = False,
    force: bool = False,
) -> list[str]:
    """The publish gate: hard errors always block, coverage loss by policy."""
    if report.get("changes"):
        return evaluate_gate(
            report,
            waivers=report.get("waivers") or [],
            allow_coverage_drop=allow_coverage_drop,
            force=force,
        )["reasons"]
    reasons = list(report.get("hard_errors") or [])
    if force:
        return reasons
    coverage = report.get("coverage") or {}
    protected = set((report.get("gates") or {}).get("protected_regressions") or [])
    for topic in coverage.get("regressions") or []:
        if allow_coverage_drop and topic not in protected:
            continue
        item = (coverage.get("topics") or {}).get(topic) or {}
        reasons.append(
            f"coverage regression: {topic} {item.get('current')} -> {item.get('candidate')}"
            + (" (gate protected)" if topic in protected else "")
        )
    removed = (report.get("entries") or {}).get("counts", {}).get("removed", 0)
    if removed and not allow_coverage_drop:
        reasons.append(f"removed published entries: {removed}")
    return reasons


def assert_publishable(
    report: dict[str, Any],
    *,
    allow_coverage_drop: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    reasons = blocking_reasons(report, allow_coverage_drop=allow_coverage_drop, force=force)
    report["blocking_reasons"] = reasons
    report["ok"] = not reasons
    if reasons:
        raise PublishBlocked(reasons, report)
    return report


def write_diff_report(report: dict[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else DIFF_REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return target
