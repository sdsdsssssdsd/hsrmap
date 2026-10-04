"""Snapshot diff and coverage regression gate for publish-snapshot."""

import sqlite3
from pathlib import Path

import pytest

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publishing.diff import (
    PublishBlocked,
    assert_publishable,
    blocking_reasons,
    snapshot_diff,
    write_diff_report,
)

TOPIC = "test_topic"


def _points(count, start=1):
    return [
        {"source_point_id": str(index), "map_id": "10", "label": TOPIC}
        for index in range(start, start + count)
    ]


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


def _ctx(points=(), maps=("10",)):
    core = sqlite3.connect(":memory:")
    core.row_factory = sqlite3.Row
    core.execute("CREATE TABLE points (source_id TEXT)")
    core.execute("CREATE TABLE maps (source_id TEXT)")
    for mid in maps:
        core.execute("INSERT INTO maps(source_id) VALUES (?)", (mid,))
    for pid in points:
        core.execute("INSERT INTO points(source_id) VALUES (?)", (pid,))

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.core = type("Core", (), {"conn": core})()
    return ctx


def _diff(working, published, tmp_path, **kwargs):
    kwargs.setdefault("assets_root", tmp_path / "assets")
    kwargs.setdefault("official_points", {TOPIC: _points(3)})
    kwargs.setdefault("topics", [TOPIC])
    return snapshot_diff(working, published, **kwargs)


def test_normal_growth_is_allowed(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    _entry(working, "2")
    report = _diff(working, published, tmp_path)
    assert report["entries"]["counts"] == {"added": 2, "removed": 0, "changed": 0}
    assert report["coverage"]["topics"][TOPIC]["current"] == 0
    assert report["coverage"]["topics"][TOPIC]["candidate"] == 2
    assert report["coverage"]["regressions"] == []
    assert blocking_reasons(report) == []
    working.close()
    published.close()


def test_coverage_drop_blocks_publish(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    for pid in ("1", "2", "3"):
        _entry(published, pid, title="已发布")
    _entry(working, "1", title="已发布")
    _entry(working, "2", title="已发布")
    _entry(working, "3", title="已发布", status="draft")
    report = _diff(working, published, tmp_path)
    assert report["coverage"]["topics"][TOPIC] == {
        "current": 3,
        "candidate": 2,
        "delta": -1,
        "targets": 3,
        "basis": "official",
    }
    assert report["coverage"]["regressions"] == [TOPIC]
    reasons = blocking_reasons(report)
    assert any("coverage regression" in item for item in reasons)
    # 非 Gate 保护 Topic 允许显式放行
    assert blocking_reasons(report, allow_coverage_drop=True) == []
    assert blocking_reasons(report, force=True) == []
    working.close()
    published.close()


def test_removed_published_entry_is_reported(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(published, "1", title="保留")
    _entry(published, "2", title="将被删除")
    _entry(working, "1", title="保留")
    report = _diff(working, published, tmp_path)
    assert report["entries"]["counts"]["removed"] == 1
    assert report["entries"]["removed"][0]["title"] == "将被删除"
    reasons = blocking_reasons(report)
    assert any("removed published entries" in item for item in reasons)
    working.close()
    published.close()


def test_changed_entry_is_reported_but_not_blocking(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1", title="旧标题")
    _entry(published, "1", title="旧标题")
    assert working.conn.execute("SELECT id FROM guide_entry").fetchone()["id"] == 1
    working.conn.execute("UPDATE guide_entry SET title = ? WHERE id = 1", ("新标题",))
    working.conn.commit()
    report = _diff(working, published, tmp_path)
    assert report["entries"]["counts"] == {"added": 0, "removed": 0, "changed": 1}
    assert report["entries"]["changed"][0]["fields"] == ["title"]
    assert blocking_reasons(report) == []
    working.close()
    published.close()


def test_missing_asset_is_a_hard_error(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1", images=["a" * 64])
    _entry(working, "2")
    assets = tmp_path / "assets"
    (assets / "aa").mkdir(parents=True)
    report = _diff(working, published, tmp_path, assets_root=assets)
    assert report["assets"]["missing_files"] == ["a" * 64]
    assert report["hard_errors"] == [f"missing asset: {'a' * 64}"]
    # 硬错误连 --force 都不能放行
    assert blocking_reasons(report, force=True) == [f"missing asset: {'a' * 64}"]
    (assets / "aa" / ("a" * 64 + ".png")).write_bytes(b"png")
    fixed = _diff(working, published, tmp_path, assets_root=assets)
    assert fixed["assets"]["missing_files"] == []
    working.close()
    published.close()


def test_broken_binding_is_a_hard_error(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    _entry(working, "999", title="悬空绑点")
    ctx = _ctx(points=["1"])
    report = _diff(working, published, tmp_path, ctx=ctx)
    assert report["bindings"]["core_checked"] is True
    assert report["bindings"]["broken"] == [{"key": "999", "kind": "point", "missing": "999"}]
    assert blocking_reasons(report, force=True) == ["broken binding: 999"]
    working.close()
    published.close()


def test_synthetic_scope_keys_are_checked_against_maps(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "map:10:topic:origami_bird")
    _entry(working, "map:77:topic:origami_bird")
    ctx = _ctx(maps=("10",))
    report = _diff(working, published, tmp_path, ctx=ctx)
    assert report["bindings"]["broken"] == [
        {"key": "map:77:topic:origami_bird", "kind": "map", "missing": "77"}
    ]
    working.close()
    published.close()


def test_gate_protected_topic_blocks_even_with_allow_coverage_drop(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    for index in range(1, 23):
        _entry(published, str(index))
    for index in range(1, 23):
        status = "published" if index <= 19 else "draft"
        _entry(working, str(index), status=status)
    report = snapshot_diff(
        working,
        published,
        assets_root=tmp_path / "assets",
        official_points={"floating_grease": _points(22)},
        topics=["floating_grease"],
    )
    assert report["gates"]["current"]["A"]["topics"][0]["engine"] == "PASS"
    assert report["gates"]["protected_regressions"] == ["floating_grease"]
    assert blocking_reasons(report, allow_coverage_drop=True) != []
    assert blocking_reasons(report, force=True) == []
    try:
        assert_publishable(report, allow_coverage_drop=True)
        raise AssertionError("expected PublishBlocked")
    except PublishBlocked as exc:
        assert "gate protected" in str(exc)
    working.close()
    published.close()


def test_write_diff_report(tmp_path):
    report = {"ok": True, "entries": {"counts": {"added": 0}}}
    path = write_diff_report(report, tmp_path / "reports" / "publish-diff.json")
    assert path.exists()
    assert path.name == "publish-diff.json"
    assert '"ok": true' in path.read_text(encoding="utf-8")


@pytest.mark.data  # 这条走完整 CLI 路径，会碰离线 viewer（需要 data/current.json）
def test_cli_publish_snapshot_blocks_and_keeps_published_db(tmp_path):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(published, "1", title="已发布")
    _entry(working, "1", title="已发布")
    _entry(working, "2", title="草稿", status="draft")
    working.close()
    published.close()
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--report", str(tmp_path / "reports" / "diff.json"),
    ])
    assert code == 0
    published = GuideDatabase(published_path)
    assert len(published.list_for_point("1")) == 1
    assert published.list_for_point("2") == []
    published.close()


def test_cli_publish_snapshot_exits_2_on_coverage_regression(tmp_path, capsys):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(published, "1", title="已发布")
    _entry(published, "2", title="已发布")
    _entry(working, "1", title="已发布")
    working.close()
    published.close()
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--report", str(tmp_path / "reports" / "diff.json"),
    ])
    assert code == 2
    assert "blocked" in capsys.readouterr().out
    published = GuideDatabase(published_path)
    assert {row["source_point_id"] for row in published.conn.execute("SELECT source_point_id FROM guide_entry")} == {"1", "2"}
    published.close()


def test_cli_force_publishes_despite_coverage_drop(tmp_path):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(published, "1", title="已发布")
    _entry(published, "2", title="已发布")
    _entry(working, "1", title="已发布")
    working.close()
    published.close()
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--force",
        "--dry-run",
        "--report", str(tmp_path / "reports" / "diff.json"),
    ])
    assert code == 0
    published = GuideDatabase(published_path)
    assert len(published.conn.execute("SELECT id FROM guide_entry").fetchall()) == 2
    published.close()


def _renumber(db, old, new):
    """Line the two snapshots up by id, the way a real publish history does."""
    db.conn.commit()
    db.conn.execute("PRAGMA foreign_keys = OFF")
    for table, column in (("guide_entry", "id"), ("guide_steps", "guide_id"), ("guide_assets", "guide_id")):
        db.conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old))
    db.conn.commit()
    db.conn.execute("PRAGMA foreign_keys = ON")


def test_a_duplicate_that_merges_into_a_thicker_guide_is_not_a_regression(tmp_path):
    """The target keeps a guide, so nothing was lost — the removal is a merge."""
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    thin = _entry(published, "1", title="薄的")           # published id 1
    _entry(published, "2", title="另一个目标")             # published id 2
    other = _entry(working, "2", title="另一个目标")       # working id 1 -> 2
    _renumber(working, int(other["id"]), 2)
    thick = _entry(working, "1", title="厚的")            # working id 3
    _ground(working, "第二步")
    working.conn.execute(
        "INSERT INTO guide_steps(guide_id, step_index, text) VALUES (?, 1, '第二步')",
        (int(thick["id"]),),
    )
    working.conn.commit()

    report = _diff(working, published, tmp_path)
    kinds = {change["type"] for change in report["changes"]}
    assert "GUIDE_MERGED" in kinds and "GUIDE_REMOVED" not in kinds
    merged = [c for c in report["changes"] if c["type"] == "GUIDE_MERGED"][0]
    assert merged["guide_id"] == int(thin["id"]) and merged["survivor"] == int(thick["id"])
    assert report["gate"]["result"] == "REVIEW"
    assert report["gate"]["coverage_fail"] == []
    assert_publishable(report, allow_coverage_drop=False, force=False)

    # losing the target's last guide is still a hard stop
    working.conn.execute("DELETE FROM guide_entry WHERE id = ?", (int(thick["id"]),))
    working.conn.commit()
    report = _diff(working, published, tmp_path)
    kinds = {change["type"] for change in report["changes"]}
    assert {"GUIDE_REMOVED", "TARGET_REMOVED", "COVERAGE_DECREASED"} <= kinds
    assert report["gate"]["result"] == "FAIL"

def test_publish_snapshot_reports_land_next_to_the_published_db(tmp_path):
    """报告跟着这次发布的库走，而不是模块导入时算出来的常量。

    以前 CLI 里 `from ...diff import MANIFEST_PATH` 是**取值**导入，测试里的 monkeypatch
    拦不住它，pytest 于是往真实的 `data/guides/reports/` 写了一份内容属于临时库的报告。
    """
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(published, "1", title="已发布")
    _entry(working, "1", title="已发布")
    working.close()
    published.close()
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--dry-run",
        "--report", str(tmp_path / "reports" / "diff.json"),
    ])
    assert code == 0
    assert (tmp_path / "reports" / "diff.json").exists()
    # 这两份是「没给路径」的报告：它们必须落在发布库旁边
    assert (tmp_path / "reports" / "snapshot-diff.md").exists()
    assert (tmp_path / "reports" / "snapshot-manifest.json").exists()

