"""P6.3 观察存储 + P6.4 进度差异的边界测试（a1-9 §13/§14/§18）。

这些用例是硬门的机器版本，尤其是：

* `unknown` / 未验证语义**永远**不能推导 `completed`；
* 合并在没确认时**一个字节都不写**，写的时候也只写 `True`；
* 观察存储里**没有凭据**，也没有完整 UID。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from hsrmap.progress import diff as diff_mod
from hsrmap.progress import store
from hsrmap.progress.models import (
    SEMANTIC_GAME_OBTAINED,
    SEMANTIC_MANUAL,
    SEMANTIC_MAP_MARK,
    SEMANTIC_UNKNOWN,
    SOURCE_HOYOLAB_MAP,
    VERIFIED_SEMANTICS,
    ProgressObservation,
    profile_key,
)
from hsrmap.progress.resolver import allowed_semantics, resolve
from hsrmap.user_db import SCHEMA, SCHEMA_VERSION, UserDatabase

PROFILE = "p1"


@pytest.fixture()
def db(tmp_path):
    database = UserDatabase(tmp_path / "user.db")
    yield database
    database.close()


def _obs(point_id: str, semantic: str, *, completed: bool = True, source: str = "") -> ProgressObservation:
    return ProgressObservation(
        source_point_id=point_id, profile_id=PROFILE, semantic=semantic,
        completed=completed, source=source,
    )


# --------------------------------------------------------------------------- #
# 存储
# --------------------------------------------------------------------------- #

def test_schema_v2_creates_progress_tables(db) -> None:
    tables = {
        str(row["name"])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"progress_profile", "progress_observation"} <= tables
    assert db.get_meta("schema") == str(SCHEMA_VERSION) == "2"
    assert int(db.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION


def test_v1_database_is_upgraded_without_losing_progress(tmp_path) -> None:
    """老库（只有 v1 两张表 + 勾选）打开后应长出 v2 的表，且勾选原样保留。"""
    path = tmp_path / "user.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE point_progress (
            source_point_id TEXT PRIMARY KEY, stable_key TEXT,
            completed INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
            note TEXT, updated_at TEXT NOT NULL);
        INSERT INTO metadata VALUES ('schema', '1');
    """)
    old.execute(
        "INSERT INTO point_progress(source_point_id, completed, updated_at) VALUES ('p-old', 1, '2026-01-01')"
    )
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()

    database = UserDatabase(path)
    try:
        assert database.get_point("p-old")["completed"] is True
        assert database.get_meta("schema") == "2"
        tables = {
            str(row["name"])
            for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "progress_observation" in tables
    finally:
        database.close()


def test_observation_upsert_is_idempotent(db) -> None:
    store.upsert_observation(db, _obs("p1", SEMANTIC_MAP_MARK))
    store.upsert_observation(db, _obs("p1", SEMANTIC_MAP_MARK))
    assert len(store.observations(db)) == 1
    #: 状态变化只更新同一行，不新增。
    store.upsert_observation(db, _obs("p1", SEMANTIC_MAP_MARK, completed=False))
    rows = store.observations(db)
    assert len(rows) == 1 and rows[0].completed is False


def test_observation_store_has_no_secrets(db, tmp_path) -> None:
    store.upsert_profile(db, store.profile_from_role(realm="cn", role={"game_uid": "123456789", "region": "prod_gf_cn"}))
    store.upsert_observation(db, _obs("p1", SEMANTIC_MAP_MARK))
    profile = store.profiles(db)[0]
    assert profile.uid_masked != "123456789"
    assert profile.uid_masked.endswith("6789")
    blob = (tmp_path / "user.db").read_bytes()
    for needle in (b"ltoken", b"ltuid", b"cookie", b"123456789"):
        assert needle not in blob


def test_profile_key_is_deterministic_and_opaque() -> None:
    first = profile_key(realm="cn", region="prod_gf_cn", uid_masked="12***89")
    assert first == profile_key(realm="cn", region="prod_gf_cn", uid_masked="12***89")
    assert len(first) == 16 and "12***89" not in first


def test_summary_counts_and_no_cookie_flag(db) -> None:
    store.upsert_observation(db, _obs("p1", SEMANTIC_MAP_MARK))
    store.upsert_observation(db, _obs("p2", SEMANTIC_MANUAL))
    report = store.summary(db)
    assert report["observations"][SEMANTIC_MAP_MARK] == 1
    assert report["completed_by_semantic"][SEMANTIC_MANUAL] == 1
    assert report["stores_cookie"] is False


# --------------------------------------------------------------------------- #
# 语义裁决：Gate 0 之前，只有手动算完成
# --------------------------------------------------------------------------- #

def test_gate0_default_is_empty() -> None:
    assert VERIFIED_SEMANTICS == frozenset(), "Gate 0 没做实验前，这里必须是空集"
    assert allowed_semantics() == frozenset()


def test_manual_always_completes() -> None:
    result = resolve([_obs("p1", SEMANTIC_MANUAL)])
    assert result.completed == {"p1"}
    assert result.points["p1"].label == "本地已完成"


@pytest.mark.parametrize("semantic", [SEMANTIC_MAP_MARK, SEMANTIC_GAME_OBTAINED])
def test_unverified_remote_never_derives_completed(semantic: str) -> None:
    result = resolve([_obs("p1", semantic)])
    assert result.completed == set()
    assert result.points["p1"].unclear == (semantic,)


def test_unknown_semantic_never_derives_completed() -> None:
    result = resolve([_obs("p1", SEMANTIC_UNKNOWN)])
    assert result.completed == set()
    assert result.points["p1"].completed is False


def test_map_mark_counts_only_when_user_opts_in_and_is_labelled() -> None:
    observations = [_obs("p1", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP)]
    assert resolve(observations).completed == set()
    opted = resolve(observations, import_map_mark=True)
    assert opted.completed == {"p1"}
    assert opted.points["p1"].label == "官方地图标记", "界面文案不能写成游戏内已完成"


def test_verified_semantic_counts_after_gate0(monkeypatch) -> None:
    """Gate 0 通过后的路径也要能走通（用显式 verified 参数，不改模块常量）。"""
    result = resolve([_obs("p1", SEMANTIC_GAME_OBTAINED)], verified={SEMANTIC_GAME_OBTAINED})
    assert result.completed == {"p1"}
    assert result.points["p1"].label == "游戏内已获得"


# --------------------------------------------------------------------------- #
# 差异
# --------------------------------------------------------------------------- #

def _seed(db) -> diff_mod.ProgressDiff:
    """一份「两边都有话说」的样本：both / local-only / remote-only / mystery。"""
    db.upsert_point("both", {"completed": True})
    db.upsert_point("local-only", {"completed": True})
    store.upsert_observation(db, _obs("both", SEMANTIC_MANUAL))
    store.upsert_observation(db, _obs("both", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP))
    store.upsert_observation(db, _obs("remote-only", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP))
    store.upsert_observation(db, _obs("mystery", SEMANTIC_UNKNOWN, source=SOURCE_HOYOLAB_MAP))
    return diff_mod.build_diff(db)


def test_diff_buckets(db) -> None:
    diff = _seed(db)
    assert diff.local_only == frozenset({"local-only"})
    assert diff.local_total == 2
    #: 默认没有可推导语义 → 远端状态进不了 remote_only，只能进 unknown。
    assert diff.remote_only == frozenset()
    assert {"remote-only", "mystery"} <= set(diff.unknown)
    assert diff.remote_by_semantic[SEMANTIC_MAP_MARK] == frozenset({"both", "remote-only"})
    assert diff.as_dict()["dry_run"] is True


def test_diff_with_opt_in_puts_remote_points_in_remote_only(db) -> None:
    _seed(db)
    diff = diff_mod.build_diff(db, import_map_mark=True)
    assert diff.remote_only == frozenset({"remote-only"})
    assert "mystery" in diff.unknown, "unknown 语义永远不因为它而变成可合并"
    assert diff.allowed_remote_semantics == (SEMANTIC_MAP_MARK,)


def test_local_completed_union_of_table_and_observations(db) -> None:
    db.upsert_point("table-only", {"completed": True})
    store.upsert_observation(db, _obs("observation-only", SEMANTIC_MANUAL))
    assert diff_mod.local_completed(db) == {"table-only", "observation-only"}


def test_diff_does_not_write_anything(db, tmp_path) -> None:
    db.upsert_point("local-only", {"completed": True})
    before = (tmp_path / "user.db").read_bytes()
    diff_mod.build_diff(db, import_map_mark=True)
    assert (tmp_path / "user.db").read_bytes() == before


# --------------------------------------------------------------------------- #
# 合并：默认 dry-run，写的时候只写 True
# --------------------------------------------------------------------------- #

def test_merge_is_dry_run_by_default(db) -> None:
    store.upsert_observation(db, _obs("remote-only", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP))
    diff = diff_mod.build_diff(db, import_map_mark=True)
    before = store.observations(db)
    report = diff_mod.merge(db, diff, semantics=[SEMANTIC_MAP_MARK])
    assert report["planned"] == 1 and report["applied"] == 0 and report["dry_run"] is True
    assert db.get_point("remote-only")["completed"] is False
    assert len(store.observations(db)) == len(before)
    assert db.get_meta(diff_mod.MERGE_META_KEY) is None


def test_merge_refuses_semantics_without_permission(db) -> None:
    store.upsert_observation(db, _obs("remote-only", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP))
    diff = diff_mod.build_diff(db)  # 没有 import_map_mark → 没有可推导语义
    report = diff_mod.merge(db, diff, semantics=[SEMANTIC_MAP_MARK], confirm=True)
    assert report["applied"] == 0
    assert db.get_point("remote-only")["completed"] is False


def test_merge_applies_only_true_and_is_idempotent(db) -> None:
    store.upsert_observation(db, _obs("remote-only", SEMANTIC_MAP_MARK, source=SOURCE_HOYOLAB_MAP))
    diff = diff_mod.build_diff(db, import_map_mark=True)
    report = diff_mod.merge(db, diff, semantics=[SEMANTIC_MAP_MARK], confirm=True)
    assert report["applied"] == 1 and report["wrote_completed_false"] == 0
    assert db.get_point("remote-only")["completed"] is True
    assert json.loads(db.get_meta(diff_mod.MERGE_META_KEY))["applied"] == 1
    #: 第二次：点位已经在 both 里，不该再动。
    again = diff_mod.merge(db, diff_mod.build_diff(db, import_map_mark=True),
                           semantics=[SEMANTIC_MAP_MARK], confirm=True)
    assert again["planned"] == 0 and again["applied"] == 0


def test_merge_never_flips_completed_back_to_false(db) -> None:
    db.upsert_point("done", {"completed": True, "note": "手动过的"})
    store.upsert_observation(db, _obs("done", SEMANTIC_MAP_MARK, completed=False))
    diff = diff_mod.build_diff(db, import_map_mark=True)
    diff_mod.merge(db, diff, semantics=[SEMANTIC_MAP_MARK], confirm=True)
    point = db.get_point("done")
    assert point["completed"] is True and point["note"] == "手动过的"


def test_remote_failure_leaves_local_progress_untouched(db) -> None:
    """断网/远端报错时本地库一个字节都不该变（这里模拟「远端什么都没写」）。"""
    db.upsert_point("keep", {"completed": True, "favorite": True, "note": "n"})
    snapshot = db.list_points()
    diff = diff_mod.build_diff(db, import_map_mark=True)
    assert diff.remote_total == 0 and diff.remote_only == frozenset()
    assert db.list_points() == snapshot
    assert db.get_point("keep")["favorite"] is True


def test_unknown_observations_are_never_mergeable(db) -> None:
    store.upsert_observation(db, _obs("mystery", SEMANTIC_UNKNOWN, source=SOURCE_HOYOLAB_MAP))
    for import_map_mark in (False, True):
        diff = diff_mod.build_diff(db, import_map_mark=import_map_mark)
        report = diff_mod.merge(
            db, diff, semantics=[SEMANTIC_UNKNOWN, SEMANTIC_MAP_MARK], confirm=True
        )
        assert report["applied"] == 0
        assert db.get_point("mystery")["completed"] is False


def test_observation_rejects_unknown_semantic_and_missing_ids() -> None:
    with pytest.raises(ValueError):
        ProgressObservation(source_point_id="p", profile_id=PROFILE, semantic="whatever", completed=True)
    with pytest.raises(ValueError):
        ProgressObservation(source_point_id="", profile_id=PROFILE, semantic=SEMANTIC_MANUAL, completed=True)


def test_schema_sql_still_creates_both_tables_on_raw_connection(tmp_path) -> None:
    """`ensure_schema` 对「先拿到连接」的调用方也要管用。"""
    path = tmp_path / "raw.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    names = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert {"point_progress", "progress_profile", "progress_observation"} <= names

# --------------------------------------------------------------------------- #
# CLI：status 只读、merge 默认 dry-run
# --------------------------------------------------------------------------- #

def test_cli_status_does_not_create_the_database(tmp_path, capsys) -> None:
    from hsrmap.cli import main

    target = tmp_path / "user.db"
    assert main(["progress", "status", "--db", str(target), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["exists"] is False and payload["diff"]["dry_run"] is True
    assert not target.exists(), "只看一眼不该建库"


def test_cli_merge_dry_run_then_confirm(tmp_path, capsys) -> None:
    from hsrmap.cli import main

    target = tmp_path / "user.db"
    database = UserDatabase(target)
    store.upsert_observation(database, ProgressObservation(
        source_point_id="p9", profile_id=PROFILE, semantic=SEMANTIC_MAP_MARK,
        completed=True, source=SOURCE_HOYOLAB_MAP,
    ))
    database.close()

    #: 没确认 → 计划 0（没有可推导语义）、实际 0、库没变。
    assert main(["progress", "merge", "--db", str(target), "--semantics", "map_mark", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 0
    check = UserDatabase(target)
    assert check.get_point("p9")["completed"] is False
    check.close()

    #: 确认 + 点名语义 + 显式接受 map_mark → 写 1 条，永不写 False。
    code = main(["progress", "merge", "--db", str(target), "--semantics", "map_mark",
                 "--accept-map-mark", "--confirm", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["applied"] == 1 and report["wrote_completed_false"] == 0
    check = UserDatabase(target)
    assert check.get_point("p9")["completed"] is True
    check.close()


def test_cli_merge_confirm_without_semantics_is_a_usage_error(tmp_path, capsys) -> None:
    from hsrmap.cli import main

    target = tmp_path / "user.db"
    assert main(["progress", "merge", "--db", str(target), "--confirm"]) == 2
    assert not target.exists()
    assert "不许改本地进度" in capsys.readouterr().err

