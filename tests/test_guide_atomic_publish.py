"""P5: staged publish switches atomically and never half-writes published.db."""

import json
from pathlib import Path

import pytest

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publishing.atomic import (
    build_staging,
    offline_e2e,
    publish_atomic,
    staging_path_for,
    validate_staging,
)


def _ground(db, text):
    """合成条目也要有来源页：a1-8 六 起 UNGROUNDED_STEP 是 HARD FAIL。

    发布门问的是「这一步在不在它自己的来源里」，所以夹具必须先造出那个来源。
    """
    url = "https://t.test/atomic"
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


def _points(count=3):
    return {"test_topic": [{"source_point_id": str(i), "map_id": "10"} for i in range(1, count + 1)]}


def test_build_staging_copies_the_publishable_state(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    _entry(working, "1")
    _entry(working, "2", status="draft")
    staging = staging_path_for(tmp_path / "published.db")
    built = build_staging(working, staging)
    assert built["copied"] == 1 and built["entries"] == 1
    assert staging.exists()
    db = GuideDatabase(staging)
    assert [row["source_point_id"] for row in db.conn.execute("SELECT source_point_id FROM guide_entry")] == ["1"]
    db.close()
    working.close()


def test_validate_staging_reports_structure(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    _entry(working, "1")
    staging = staging_path_for(tmp_path / "published.db")
    build_staging(working, staging)
    report = validate_staging(staging, working, assets_root=tmp_path / "assets")
    assert report["ok"] is True and report["entries"] == 1 and report["problems"] == []
    working.close()


@pytest.mark.data  # 离线 viewer 要 data/current.json：submit 副本没有 data/，按约定跳过
def test_offline_e2e_serves_the_staged_snapshot(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    _entry(working, "1")
    staging = staging_path_for(tmp_path / "published.db")
    build_staging(working, staging)
    result = offline_e2e(staging, assets_root=tmp_path / "assets")
    assert result["ok"] is True, result
    assert result["checks"]["guides/index"] == 200
    assert result["checks"]["guides/atlas"] == 200
    working.close()


def test_publish_atomic_switches_and_keeps_a_backup(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    target = tmp_path / "published.db"
    _entry(working, "1", title="第一版")
    first = publish_atomic(working, target=target, assets_root=tmp_path / "assets", official_points=_points(), topics=["test_topic"], e2e=False)
    assert first["ok"] is True and first["switch"]["switched"] is True
    db = GuideDatabase(target)
    assert db.list_for_point("1")[0]["title"] == "第一版"
    db.close()
    working.conn.execute("UPDATE guide_entry SET title = '第二版' WHERE id = 1")
    working.conn.commit()
    second = publish_atomic(working, target=target, assets_root=tmp_path / "assets", official_points=_points(), topics=["test_topic"], e2e=False)
    assert second["ok"] is True
    assert second["switch"]["backup"] and (tmp_path / "published.db.bak").exists() is False
    assert list(tmp_path.glob("published.db.bak-*"))
    db = GuideDatabase(target)
    assert db.list_for_point("1")[0]["title"] == "第二版"
    assert not staging_path_for(target).exists()
    db.close()
    working.close()


def test_publish_atomic_aborts_on_regression_and_removes_staging(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    target = tmp_path / "published.db"
    published = GuideDatabase(target)
    _entry(published, "1", title="已发布")
    _entry(published, "2", title="将被移除")
    published.close()
    _entry(working, "1", title="已发布")
    result = publish_atomic(working, target=target, assets_root=tmp_path / "assets", official_points=_points(), topics=["test_topic"], e2e=False)
    assert result["ok"] is False and result["blocked"] is True
    assert any("removed" in reason or "coverage" in reason for reason in result["reasons"])
    assert result["staging_removed"] is True
    assert not staging_path_for(target).exists()
    db = GuideDatabase(target)
    assert {row["source_point_id"] for row in db.conn.execute("SELECT source_point_id FROM guide_entry")} == {"1", "2"}
    db.close()
    working.close()


@pytest.mark.data  # 原子发布会跑离线 viewer 自检，同样要 data/current.json
def test_cli_publish_snapshot_is_atomic_by_default(tmp_path, capsys):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    _entry(working, "1", title="第一版")
    _entry(published, "1", title="第一版")
    working.close()
    published.close()
    args = [
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--raw", str(tmp_path / "raw"),
        "--no-core-check",
        "--report", str(tmp_path / "reports" / "diff.json"),
    ]
    assert main(args) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True and body["switch"]["switched"] is True
    published = GuideDatabase(published_path)
    assert published.list_for_point("1")[0]["title"] == "第一版"
    published.close()
    # a second publish keeps the previous snapshot as a backup
    working = GuideDatabase(working_path)
    working.conn.execute("UPDATE guide_entry SET title = '第二版' WHERE id = 1")
    working.conn.commit()
    working.close()
    assert main(args) == 0
    capsys.readouterr()
    assert list(tmp_path.glob("published.db.bak-*"))
