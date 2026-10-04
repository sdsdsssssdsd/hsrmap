"""发布快照的写权限**按进程分工**（a1-8 四.2 + S9 之后的修正）：

* 只跑地图的进程 → 只读打开 published：地图进程永远不该动发布快照；
* 审核台进程 → 可写打开 published：它要「创建 / 通过」条目并同步进快照
  （S9 把两边都改成只读之后，审核台直接 \`attempt to write a readonly database\`）。

这条不变量以前只写在注释里，所以坏过一次；现在用测试钉住。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.viewer_app import create_app

pytestmark = pytest.mark.data


def _dbs(tmp_path):
    working = GuideDatabase.create(tmp_path / "guide.db")
    working.close()
    published = GuideDatabase.create(tmp_path / "published.db")
    published.close()
    return tmp_path / "guide.db", tmp_path / "published.db"


def test_map_only_process_opens_published_readonly(tmp_path) -> None:
    guide, published = _dbs(tmp_path)
    app = create_app(user_path=tmp_path / "user.db", guide_path=guide, published_path=published,
                     sections=("map",))
    assert app.state.guide is None, "地图进程不该拿到工作库的写句柄"
    with pytest.raises(sqlite3.OperationalError):
        app.state.published.conn.execute("DELETE FROM guide_steps")


def test_review_console_can_publish_into_the_snapshot(tmp_path) -> None:
    """审核台的「创建条目 + 状态 published」要真的写进发布快照，而不是撞只读错误。"""
    guide, published = _dbs(tmp_path)
    app = create_app(user_path=tmp_path / "user.db", guide_path=guide, published_path=published)
    client = TestClient(app)
    created = client.post("/api/v1/guides", json={
        "source_point_id": "5171",
        "title": "海原市浮脂",
        "summary": "先转再点",
        "status": "published",
    })
    assert created.status_code == 200, created.text
    assert client.get("/api/v1/guides/by-point/5171").json()["entries"], "发布快照里应该能看到这条"


def test_review_console_keeps_the_snapshot_readable_for_the_map(tmp_path) -> None:
    """审核台可写，不代表它把快照改坏：另一个只读进程仍能正常打开并查询。"""
    guide, published = _dbs(tmp_path)
    review = create_app(user_path=tmp_path / "user.db", guide_path=guide, published_path=published)
    TestClient(review).post("/api/v1/guides", json={
        "source_point_id": "5171", "title": "x", "status": "published",
    })
    maponly = create_app(user_path=tmp_path / "user2.db", guide_path=guide, published_path=published,
                         sections=("map",))
    rows = maponly.state.published.conn.execute("SELECT COUNT(*) FROM guide_entry").fetchone()
    assert int(rows[0]) == 1
