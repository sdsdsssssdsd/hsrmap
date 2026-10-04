"""Staged publish: build → validate → offline E2E → regression → atomic switch.

The published DB is what the offline Viewer serves, so a publish must never be
able to leave it half-written. This module builds the candidate in a separate
staging file, proves it (structure, audit, gate, offline API), and only then
swaps it in with `os.replace`, keeping the previous file as a backup.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publishing.diff import (
    PublishBlocked,
    assert_publishable,
    entry_snapshot,
    schema_problems,
    snapshot_diff,
)
from hsrmap.guides.publishing.sync import sync_published
from hsrmap.paths import GUIDE_ASSETS, GUIDE_PUBLISHED_DB


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def staging_path_for(target: Path) -> Path:
    target = Path(target)
    return target.with_name(f"{target.stem}.staging{target.suffix}")


def build_staging(working: GuideDatabase, staging: Path) -> dict[str, Any]:
    """Copy the publishable state into a fresh staging database."""
    staging = Path(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(str(staging) + suffix)
        if candidate.exists():
            candidate.unlink()
    db = GuideDatabase(staging)
    try:
        copied = sync_published(working, db)
        entries = entry_snapshot(db)
    finally:
        db.close()
    return {"path": str(staging), "copied": copied, "entries": len(entries)}


def validate_staging(staging: Path, working: GuideDatabase, *, assets_root: Path | None = None) -> dict[str, Any]:
    """Structural validation of the staged snapshot."""
    db = GuideDatabase(Path(staging))
    try:
        entries = entry_snapshot(db)
        problems = schema_problems(db)
    finally:
        db.close()
    manifest_entries = {guide_id: dict(entry) for guide_id, entry in entries.items()}
    for entry in manifest_entries.values():
        entry.setdefault("asset_rows", [])
    from hsrmap.guides.audit import audit_entries

    audit = audit_entries(manifest_entries, working, assets_root=assets_root)
    empty_bindings = [guide_id for guide_id, entry in entries.items() if not entry["source_point_id"]]
    if empty_bindings:
        problems.append(f"schema invalid: {len(empty_bindings)} staged entries without a binding target")
    return {
        "ok": not problems,
        "problems": problems,
        "entries": len(entries),
        "empty_bindings": len(empty_bindings),
        "audit": {"counts": audit["counts"], "guides_with_problems": audit["guides_with_problems"]},
    }


def offline_e2e(staging: Path, *, assets_root: Path | None = None, sample: int = 3) -> dict[str, Any]:
    """Serve the staged snapshot through the real offline Viewer app."""
    from fastapi.testclient import TestClient

    from hsrmap.viewer_app import create_app

    root = Path(assets_root or GUIDE_ASSETS)
    app = create_app(guide_path=Path(staging), published_path=Path(staging), guide_assets=root)
    checks: dict[str, int] = {}
    points: list[str] = []
    # The context manager runs the app's shutdown handlers, which close the
    # database handles — on Windows the staging file cannot be renamed while
    # the Viewer still holds it.
    with TestClient(app) as client:
        index = client.get("/api/v1/guides/index")
        checks["guides/index"] = index.status_code
        if index.status_code == 200:
            points = list((index.json().get("points") or {}))[:sample]
        for point_id in points:
            checks[f"guides/by-point/{point_id}"] = client.get(f"/api/v1/guides/by-point/{point_id}").status_code
        checks["guides/atlas"] = client.get("/api/v1/guides/atlas").status_code
    failures = [name for name, status in checks.items() if status != 200]
    return {"ok": not failures, "checks": checks, "failures": failures, "sample_points": points}


def atomic_switch(staging: Path, target: Path, *, keep_backup: bool = True, attempts: int = 5) -> dict[str, Any]:
    """Swap the staged file in, keeping the previous snapshot as a backup.

    Windows refuses to rename a file another handle still holds, so a reader that
    is a moment late to close gets a short retry window instead of a failed
    publish. The swap itself stays a single `os.replace` — never a partial copy.
    """
    staging, target = Path(staging), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target.exists() and keep_backup:
        backup = target.with_name(f"{target.name}.bak-{_stamp()}")
        shutil.copyfile(target, backup)
    last: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            os.replace(staging, target)
            return {"switched": True, "target": str(target), "backup": str(backup) if backup else None, "attempts": attempt + 1}
        except OSError as exc:  # WinError 32: still in use
            last = exc
            time.sleep(0.2 * (attempt + 1))
    raise last if last else RuntimeError("atomic switch failed")


def publish_atomic(
    working: GuideDatabase,
    *,
    target: Path | None = None,
    ctx: Any = None,
    assets_root: Path | None = None,
    waivers: list[dict[str, Any]] | None = None,
    official_points: dict[str, list[dict[str, Any]]] | None = None,
    topics: list[str] | None = None,
    core_check: bool = True,
    allow_coverage_drop: bool = False,
    force: bool = False,
    e2e: bool = True,
    keep_backup: bool = True,
) -> dict[str, Any]:
    """Build, prove and switch a published snapshot; never leave it half-written."""
    target = Path(target or GUIDE_PUBLISHED_DB)
    staging = staging_path_for(target)
    report: dict[str, Any] = {"target": str(target), "staging": str(staging)}
    built = build_staging(working, staging)
    report["build"] = built
    current = GuideDatabase(target)
    try:
        diff = snapshot_diff(
            working,
            current,
            ctx=ctx,
            assets_root=assets_root,
            waivers=waivers,
            official_points=official_points,
            topics=topics,
            core_check=core_check,
        )
    finally:
        current.close()
    report["diff"] = {
        "gate": diff.get("gate"),
        "change_counts": diff.get("change_counts"),
        "entries": diff.get("entries", {}).get("counts"),
        "audit": {
            "counts": (diff.get("audit") or {}).get("counts"),
            "new_hallucinations": len((diff.get("audit") or {}).get("new_hallucinations") or []),
            "pre_existing_hallucinations": (diff.get("audit") or {}).get("pre_existing_hallucinations", 0),
        },
        "evidence": {
            "claims": (diff.get("evidence") or {}).get("claims", 0),
            "by_level": (diff.get("evidence") or {}).get("by_level"),
            "gaps": len((diff.get("evidence") or {}).get("findings") or []),
            "downgrades": len((diff.get("evidence") or {}).get("downgrades") or []),
        },
        "coverage": (diff.get("coverage") or {}).get("regressions"),
    }
    try:
        assert_publishable(diff, allow_coverage_drop=allow_coverage_drop, force=force)
    except PublishBlocked as exc:
        report.update({"ok": False, "blocked": True, "reasons": exc.reasons})
        report["validation"] = validate_staging(staging, working, assets_root=assets_root)
        _cleanup(staging)
        report["staging_removed"] = True
        return report

    report["validation"] = validate_staging(staging, working, assets_root=assets_root)
    if not report["validation"]["ok"]:
        report.update({"ok": False, "blocked": True, "reasons": report["validation"]["problems"]})
        _cleanup(staging)
        report["staging_removed"] = True
        return report

    report["e2e"] = offline_e2e(staging, assets_root=assets_root) if e2e else {"ok": True, "skipped": True}
    if not report["e2e"]["ok"]:
        report.update({"ok": False, "blocked": True, "reasons": [f"offline e2e failed: {report['e2e']['failures']}"]})
        _cleanup(staging)
        report["staging_removed"] = True
        return report

    report["switch"] = atomic_switch(staging, target, keep_backup=keep_backup)
    report["manifest"] = write_snapshot_manifest(
        target, ctx=ctx, official_points=official_points, topics=topics
    )
    report.update({"ok": True, "blocked": False, "reasons": [], "copied": built["copied"]})
    return report


def write_snapshot_manifest(
    target: Path,
    *,
    ctx: Any = None,
    official_points: dict[str, list[dict[str, Any]]] | None = None,
    topics: list[str] | None = None,
) -> str:
    """Write the manifest **of the snapshot that is now published** (a1-6 §16)。

    发布路径以前只在 dry-run 里写 manifest，真发布之后那份就过期了：读者拿到的
    evidence digest 还是切换前的。切换成功后必须按新快照重写一次。
    """
    from hsrmap.guides.publishing.diff import snapshot_manifest

    target = Path(target)
    db = GuideDatabase(target)
    try:
        manifest = snapshot_manifest(
            db, ctx=ctx, official_points=official_points, topics=topics
        )
    finally:
        db.close()
    path = target.parent / "reports" / "snapshot-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return str(path)


def _cleanup(staging: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(str(staging) + suffix)
        if candidate.exists():
            candidate.unlink()
