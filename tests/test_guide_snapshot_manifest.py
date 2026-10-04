"""P4: snapshot manifest, typed diff, three-tier gate, waivers."""

import json
from pathlib import Path

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publishing import diff as diff_mod
from hsrmap.guides.publishing.diff import (
    CHANGE_TYPES,
    MANIFEST_SCHEMA_VERSION,
    blocking_reasons,
    change_counts,
    classify_changes,
    evaluate_gate,
    load_waivers,
    manifest_digest,
    render_markdown,
    schema_problems,
    snapshot_diff,
    snapshot_manifest,
)

TOPIC = "test_topic"


def _ground(db, text):
    """合成条目也要有来源页：审计问的是「这一步在不在它自己的来源里」。

    a1-8 六 起 UNGROUNDED_STEP 是 HARD FAIL，所以夹具必须造出一个真实来源，
    否则测的就不是发布门，而是「没有语料时会怎样」。
    """
    url = "https://t.test/snapshot"
    row = db.conn.execute(
        "SELECT id, raw_text_path FROM guide_page WHERE canonical_url = ?", (url,)
    ).fetchone()
    if row is None:
        source = db.upsert_source({"name": "t", "domain": "t.test"})
        page = db.add_page(source["id"], {"canonical_url": url, "title": "来源", "author": "作者"})
        path = db.path.parent / "page.txt"
        path.write_text(text, encoding="utf-8")
        db.conn.execute("UPDATE guide_page SET raw_text_path = ? WHERE id = ?", (str(path), page["id"]))
        db.conn.commit()
        return url
    path = Path(str(row["raw_text_path"]))
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and text not in existing:
        path.write_text(existing + "\n" + text, encoding="utf-8")
    return url


def _entry(db, pid, *, title="攻略", status="published", images=(), text="第一步"):
    return db.create_entry(
        {
            "source_point_id": pid,
            "title": title,
            "status": status,
            "source_url": _ground(db, text),
            "steps": [{"text": text, "images": list(images)}],
        }
    )


def _points(count, start=1):
    return [{"source_point_id": str(i), "map_id": "10", "label": TOPIC} for i in range(start, start + count)]


def _diff(working, published, tmp_path, **kwargs):
    kwargs.setdefault("assets_root", tmp_path / "assets")
    kwargs.setdefault("official_points", {TOPIC: _points(3)})
    kwargs.setdefault("topics", [TOPIC])
    return snapshot_diff(working, published, **kwargs)


def test_manifest_shape_and_stability(tmp_path):
    db = GuideDatabase(tmp_path / "published.db")
    _entry(db, "1", images=["a" * 64])
    _entry(db, "2")
    manifest = snapshot_manifest(db, official_points={TOPIC: _points(3)}, topics=[TOPIC])
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION == 4
    assert set(manifest) >= {"schema_version", "created_at", "topics", "entries", "assets", "bindings"}
    entry = manifest["entries"]["1"]
    assert entry["target"] == "1"
    assert entry["content_sha256"] and entry["step_count"] == 1
    assert entry["assets"] == ["a" * 64]
    assert manifest["bindings"]["2"] == "2"
    assert manifest["topics"][TOPIC]["covered"] == 2
    assert manifest["assets"] == ["a" * 64]
    again = snapshot_manifest(db, official_points={TOPIC: _points(3)}, topics=[TOPIC])
    assert manifest_digest(manifest) == manifest_digest(again)
    db.close()


def test_manifest_digest_moves_with_content(tmp_path):
    db = GuideDatabase(tmp_path / "published.db")
    _entry(db, "1")
    first = snapshot_manifest(db, official_points={TOPIC: _points(3)}, topics=[TOPIC])
    db.conn.execute("UPDATE guide_entry SET title = '改了' WHERE id = 1")
    db.conn.commit()
    second = snapshot_manifest(db, official_points={TOPIC: _points(3)}, topics=[TOPIC])
    assert manifest_digest(first) != manifest_digest(second)
    db.close()


def _renumber(db, old, new):
    """Give a working entry the id it would keep after a publish."""
    db.conn.commit()
    db.conn.execute("PRAGMA foreign_keys = OFF")
    db.conn.execute("UPDATE guide_steps SET guide_id = ? WHERE guide_id = ?", (new, old))
    db.conn.execute("UPDATE guide_assets SET guide_id = ? WHERE guide_id = ?", (new, old))
    db.conn.execute("UPDATE guide_entry SET id = ? WHERE id = ?", (new, old))
    db.conn.commit()
    db.conn.execute("PRAGMA foreign_keys = ON")


def test_change_vocabulary_is_complete(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(published, "1", title="保留")
    _entry(published, "2", title="将被删")
    _entry(working, "1", title="保留")
    _entry(working, "3", title="新增", images=["b" * 64])
    _renumber(working, 2, 5)
    report = _diff(working, published, tmp_path)
    kinds = {change["type"] for change in report["changes"]}
    assert {"GUIDE_ADDED", "GUIDE_REMOVED", "TARGET_ADDED", "TARGET_REMOVED", "ASSET_ADDED"} <= kinds
    assert set(report["change_counts"]) == set(CHANGE_TYPES)
    assert report["change_counts"]["GUIDE_ADDED"] == 1
    working.close()
    published.close()


def test_changed_entry_yields_guide_and_binding_changes(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1", title="旧")
    _entry(published, "1", title="旧")
    working.conn.execute("UPDATE guide_entry SET source_point_id = '2' WHERE id = 1")
    working.conn.commit()
    report = _diff(working, published, tmp_path)
    kinds = [change["type"] for change in report["changes"]]
    assert "GUIDE_CHANGED" in kinds and "BINDING_CHANGED" in kinds
    changed = [c for c in report["changes"] if c["type"] == "GUIDE_CHANGED"][0]
    assert changed["fields"] == ["source_point_id"]
    working.close()
    published.close()


def test_three_tier_gate(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(published, "1", title="旧")
    _entry(working, "1", title="新")
    report = _diff(working, published, tmp_path)
    gate = evaluate_gate(report)
    assert gate["result"] == "REVIEW"
    assert gate["hard_fail"] == [] and gate["coverage_fail"] == []
    assert [c["type"] for c in gate["review_required"]] == ["GUIDE_CHANGED"]
    assert blocking_reasons(report) == []

    missing = _diff(working, published, tmp_path, assets_root=tmp_path / "nothing")
    working.conn.execute("DELETE FROM guide_steps WHERE guide_id = 1")
    working.conn.execute(
        "INSERT INTO guide_assets(guide_id, step_index, asset_sha256) VALUES (1, 0, ?)", ("c" * 64,)
    )
    working.conn.commit()
    missing = _diff(working, published, tmp_path, assets_root=tmp_path / "nothing")
    assert missing["gate"]["hard_fail"], missing["gate"]
    assert missing["gate"]["result"] == "FAIL"
    assert evaluate_gate(missing, force=True)["hard_fail"] == missing["gate"]["hard_fail"]
    working.close()
    published.close()


def _waiver_file(tmp_path, body):
    path = tmp_path / "allowed_regressions.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _ledger_row(db, topic, target):
    """Materialized target ledger: target -> topic attribution."""
    db.conn.execute(
        """
        INSERT OR REPLACE INTO guide_target_status(topic_key, target_key, source_point_id, status, updated_at)
        VALUES (?, ?, ?, 'PUBLISHED', datetime('now'))
        """,
        (topic, f"point:{target}", target),
    )
    db.conn.commit()


def test_waiver_allows_a_named_target_removal(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(published, "1", title="保留")
    _entry(published, "2", title="错误绑定")
    _entry(working, "1", title="保留")
    _ledger_row(working, TOPIC, "2")
    path = _waiver_file(
        tmp_path,
        "allowed_regressions:\n  - topic: test_topic\n    target: '2'\n    reason: incorrect binding\n    issue: ATLAS-1\n",
    )
    waivers = load_waivers(path)
    assert waivers and waivers[0]["reason"] == "incorrect binding"
    report = _diff(working, published, tmp_path, waivers=waivers)
    assert report["gate"]["waived"], report["gate"]
    assert report["gate"]["coverage_fail"] == []
    assert blocking_reasons(report) == []
    # without the waiver the same removal blocks
    plain = _diff(working, published, tmp_path)
    assert any("published target removed" in reason for reason in blocking_reasons(plain))
    working.close()
    published.close()


def test_missing_core_point_is_a_hard_failure(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    _entry(working, "999", title="悬空")
    import sqlite3

    core = sqlite3.connect(":memory:")
    core.row_factory = sqlite3.Row
    core.execute("CREATE TABLE points (source_id TEXT)")
    core.execute("CREATE TABLE maps (source_id TEXT)")
    core.execute("INSERT INTO points(source_id) VALUES ('1')")
    ctx = type("Ctx", (), {"core": type("Core", (), {"conn": core})()})()
    report = _diff(working, published, tmp_path, ctx=ctx)
    assert report["gate"]["hard_fail"] == ["broken binding: 999"]
    assert report["gate"]["result"] == "FAIL"
    working.close()
    published.close()


def test_render_markdown_has_the_expected_sections(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    report = _diff(working, published, tmp_path)
    text = render_markdown(report)
    assert "# Snapshot diff" in text
    assert "## Entries" in text and "## Changes" in text and "## Coverage" in text and "## Gate" in text
    assert "GUIDE_ADDED" in text
    working.close()
    published.close()


def test_schema_problems_flags_missing_tables(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    assert schema_problems(db) == []
    db.conn.execute("DROP TABLE guide_assets")
    db.conn.commit()
    assert any("schema invalid" in problem for problem in schema_problems(db))
    db.close()


def test_classify_changes_ignores_noise():
    current = {1: {"source_point_id": "1", "fingerprint": "x", "title": "t", "steps": [], "assets": []}}
    candidate = {1: {"source_point_id": "1", "fingerprint": "x", "title": "t", "steps": [], "assets": []}}
    assert classify_changes(current, candidate, {}) == []
    assert change_counts([{"type": "GUIDE_ADDED"}])["GUIDE_ADDED"] == 1


def test_cli_publish_snapshot_writes_json_md_and_manifest(tmp_path, monkeypatch):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(published, "1", title="已发布")
    _entry(working, "1", title="已发布")
    working.close()
    published.close()
    manifest_path = tmp_path / "reports" / "snapshot-manifest.json"
    monkeypatch.setattr(diff_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(diff_mod, "DIFF_MARKDOWN_PATH", tmp_path / "reports" / "snapshot-diff.md")
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--dry-run",
        "--report", str(tmp_path / "reports" / "diff.json"),
        "--waivers", str(tmp_path / "none.yaml"),
    ])
    assert code == 0
    report = json.loads((tmp_path / "reports" / "diff.json").read_text(encoding="utf-8"))
    assert report["dry_run"] is True
    assert report["gate"]["result"] in {"PASS", "REVIEW"}
    assert (tmp_path / "reports" / "snapshot-diff.md").exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 4
    assert manifest["bindings"]["1"] == "1"
