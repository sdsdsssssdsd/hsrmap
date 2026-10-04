"""a1-8 十六：Definition of Done 的 12 项验收本身也要有测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hsrmap.database import SCHEMA_VERSION as CORE_SCHEMA_VERSION
from hsrmap.dod import (
    DATA_ITEMS,
    DOD_ITEMS,
    _schema_version,
    render_dod,
    run_checks,
    stamp_versions,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_the_item_list_is_the_spec_list():
    assert len(DOD_ITEMS) == 12
    ids = [item[0] for item in DOD_ITEMS]
    assert len(set(ids)) == 12
    assert {"cli-not-silent", "web-answers-why", "no-per-point-sql"} <= set(ids)
    assert DATA_ITEMS <= set(ids)


def test_repo_checks_pass_without_any_data():
    """源码树本身的那几项不依赖运行数据，永远要能过。"""
    report = run_checks(_repo_root())
    by_id = {item["id"]: item for item in report["items"]}
    assert len(by_id) == 12
    for item_id, title in DOD_ITEMS:
        assert item_id in by_id and by_id[item_id]["title"] == title
    repo_items = [item for item in report["items"] if item["id"] not in DATA_ITEMS]
    assert repo_items, "至少要有一项与数据无关的检查"
    assert [item for item in repo_items if item["status"] == "FAIL"] == []
    assert report["result"] in {"PASS", "PASS (partial)", "FAIL"}


def test_cli_prints_json_and_follows_the_exit_code_contract(capsys):
    from hsrmap.cli import main

    code = main(["dod", "--json-out"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] in {"PASS", "PASS (partial)"}
    assert code == 0
    #: 有失败时必须是 2（gate 拦截），0 只代表全部成立。
    assert (code == 0) == bool(payload["ok"])


def test_render_marks_every_item():
    report = run_checks(_repo_root())
    text = render_dod(report)
    assert "DOD RESULT" in text
    for _item_id, title in DOD_ITEMS:
        assert title[:12] in text


def test_schema_version_detection_reads_every_marker(tmp_path):
    nothing = tmp_path / "bare.db"
    sqlite3.connect(nothing).close()
    assert _schema_version(nothing) is None

    pragma = tmp_path / "pragma.db"
    conn = sqlite3.connect(pragma)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    assert _schema_version(pragma) == "user_version=3"

    migrated = tmp_path / "migrated.db"
    conn = sqlite3.connect(migrated)
    conn.execute("CREATE TABLE schema_migration(version INTEGER, name TEXT)")
    conn.executemany("INSERT INTO schema_migration VALUES (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    conn.close()
    assert _schema_version(migrated) == "v2(2 条迁移)"

    legacy = tmp_path / "legacy-detail.db"
    conn = sqlite3.connect(legacy)
    #: 旧明细库的 schema_versions 是「指纹表」，没有 version 列——不能因此判定「无版本」。
    conn.execute("CREATE TABLE schema_versions(name TEXT, fingerprint TEXT, observed_at TEXT)")
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO metadata VALUES ('schema', '1')")
    conn.commit()
    conn.close()
    assert _schema_version(legacy) == "metadata.schema=1"


def test_stamp_versions_fills_only_recognised_families(tmp_path):
    from hsrmap import runtime

    runtime.set_runtime(runtime.resolve_runtime(tmp_path))
    try:
        core = tmp_path / "snapshots" / "s" / "core.db"
        core.parent.mkdir(parents=True)
        conn = sqlite3.connect(core)
        conn.execute("CREATE TABLE maps(id INTEGER)")
        conn.execute("CREATE TABLE points(id INTEGER)")
        conn.commit()
        conn.close()

        unknown = tmp_path / "unknown.db"
        conn = sqlite3.connect(unknown)
        conn.execute("CREATE TABLE whatever(id INTEGER)")
        conn.commit()
        conn.close()

        report = stamp_versions()
        assert [item["family"] for item in report["stamped"]] == ["core"]
        assert report["legacy"] == ["unknown.db"]
        #: 补的是**当前** core schema 版本（M7.1 起是 2），不是写死的历史数字。
        assert CORE_SCHEMA_VERSION >= 2
        assert _schema_version(core) == f"user_version={CORE_SCHEMA_VERSION}"
        #: 认不出家族的库不许乱写版本号
        assert _schema_version(unknown) is None
        #: 幂等
        assert stamp_versions()["stamped"] == []
    finally:
        runtime.reset_runtime()
