"""闭环自检（hsrmap doctor）与启动入口的测试。

用户 2026-10：「启动入口貌似打不开」——所以这里把「入口能不能用」变成会失败的测试：
入口文件、端口、起飞前检查、首屏请求、载荷预算，以及审核队列「列表是索引、详情按行取」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hsrmap.dod import _schema_version
from hsrmap.doctor import (
    ENTRY_POINTS,
    PAYLOAD_BUDGET,
    check_entry_points,
    check_first_load,
    check_ports,
    check_preflight,
    render_doctor,
    run_checks,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_every_entry_point_exists_and_its_script_does():
    report = check_entry_points(_root())
    assert report["status"] == "PASS", report
    for name, _note in ENTRY_POINTS:
        assert (_root() / name).is_file()


def test_ports_do_not_collide_and_match_the_service():
    report = check_ports(_root())
    assert report["status"] == "PASS", report
    ports = report["ports"]
    #: 地图 / 审核台 / 监控台三个端口必须互不相同。
    assert len(set(ports)) == len(ports)


@pytest.mark.data  # 需要真实 data/（预检与首屏闭环都是「这台机器上」的检查）
def test_preflight_has_no_fatal_problem():
    report = check_preflight(_root())
    assert report["status"] == "PASS", report


def test_entry_batch_files_never_vanish_on_error():
    """双击 .bat 的人要能看到报错：每个入口都必须有 pause，且先检查 python。"""
    for name in ("start.bat", "start_review.bat", "monitor/start_monitor.bat", "framework/start.bat"):
        text = (_root() / name).read_text(encoding="utf-8", errors="replace").lower()
        assert "pause" in text, f"{name} 出错时会一闪而过"
        assert "where python" in text, f"{name} 没有检查 python"


@pytest.mark.data  # 需要真实 data/（预检与首屏闭环都是「这台机器上」的检查）
def test_first_load_closes_the_loop():
    """首屏每个请求都 200，且载荷在预算内（审核队列曾经一次 142 MB）。"""
    report = check_first_load(_root())
    assert report["status"] == "PASS", report
    for path, size in report["payload_bytes"].items():
        if path in PAYLOAD_BUDGET:
            assert size <= PAYLOAD_BUDGET[path], f"{path} 载荷超标：{size} B"


@pytest.mark.data  # 需要真实 data/（预检与首屏闭环都是「这台机器上」的检查）
def test_doctor_report_shape_and_rendering():
    report = run_checks(_root())
    assert report["result"] in {"PASS", "PASS (partial)"}
    assert [item["id"] for item in report["items"]] == [
        "entry-points",
        "ports",
        "preflight",
        "first-load",
    ]
    text = render_doctor(report)
    assert "DOCTOR RESULT" in text


@pytest.mark.data  # 需要真实 data/（预检与首屏闭环都是「这台机器上」的检查）
def test_cli_doctor_json_and_exit_code(capsys):
    from hsrmap.cli import main

    code = main(["doctor", "--json-out"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["ok"] is True


def test_review_queue_is_an_index_and_the_row_detail_fills_it(tmp_path):
    """审核队列默认是索引：行里没有 draft/布局/图片数组；按行取详情才补齐。"""
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.guides.review.service import list_review_payload, map_row_for_item

    db = GuideDatabase.create(tmp_path / "guide.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title) VALUES (1, 1, 'https://t.test/a', '标题')"
    )
    db.create_entry({"source_point_id": "1", "title": "g", "status": "published",
                     "steps": [{"text": "第一步"}]})
    db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)"
        " VALUES (1, 'ingest', 'NEEDS_REVIEW', datetime('now'), '1', ?, '{}')",
        (json.dumps({"map_name": "测试地图", "target_key": "point:1", "steps": [{"text": "第一步"}]}, ensure_ascii=False),),
    )
    db.conn.commit()

    slim = list_review_payload(db, slim=True)
    assert slim["slim"] is True
    assert slim["items"] == [] and slim["items_omitted"] == 1
    row = slim["maps"][0]
    for heavy in ("draft", "layout", "layout_html", "images", "waste"):
        assert heavy not in row, f"slim 行不该带 {heavy}"
    assert row["draft_brief"]["target_key"] == "point:1"
    assert row["image_count"] == 0

    full = map_row_for_item(db, row["item_id"])
    assert full is not None and full["item_id"] == row["item_id"]
    assert full["draft"]["target_key"] == "point:1"
    assert "layout" in full

    #: full=1 时（老前端/CLI）仍然是完整行
    whole = list_review_payload(db, slim=False)
    assert "draft" in whole["maps"][0]
    db.close()


def test_review_endpoints_serve_index_then_detail(tmp_path):
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.viewer_app import create_review_app

    db = GuideDatabase.create(tmp_path / "guide.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title) VALUES (1, 1, 'https://t.test/a', '标题')"
    )
    db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)"
        " VALUES (1, 'ingest', 'NEEDS_REVIEW', datetime('now'), '1', ?, '{}')",
        (json.dumps({"map_name": "测试地图", "target_key": "point:1", "steps": []}, ensure_ascii=False),),
    )
    db.conn.commit()
    db.close()

    app = create_review_app(
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "guide.db",
    )
    client = TestClient(app)
    body = client.get("/api/v1/review/items").json()
    assert body["slim"] is True and body["maps"]
    item_id = body["maps"][0]["item_id"]
    full = client.get(f"/api/v1/review/maps/{item_id}")
    assert full.status_code == 200
    assert full.json()["draft"]["target_key"] == "point:1"
    assert client.get("/api/v1/review/maps/999999").status_code == 404


def test_page_image_cache_is_versioned_and_used(tmp_path):
    """逐页图片清单进缓存表（迁移 7）：第二次取不再重新解析文件。"""
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.guides.review.service import page_images

    db = GuideDatabase.create(tmp_path / "guide.db")
    assert db.schema_version() >= 7
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    raw = tmp_path / "raw" / "guide" / "p1" / "page.html"
    raw.parent.mkdir(parents=True)
    raw.write_text("<html></html>", encoding="utf-8")
    extracted = tmp_path / "raw" / "guide" / "extracted" / "page.json"
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_text(json.dumps([
        {"type": "image", "asset": "a" * 64, "id": "b1", "alt": "", "src": ""},
        {"type": "text", "text": "x"},
    ]), encoding="utf-8")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title, raw_html_path) VALUES (1, 1, ?, 't', ?)",
        ("https://t.test/a", str(raw)),
    )
    db.conn.commit()
    images = page_images(db, 1)
    assert [img["sha"] for img in images] == ["a" * 64]
    cached = db.conn.execute("SELECT COUNT(*) AS c FROM page_image_cache WHERE page_id = 1").fetchone()
    assert int(cached["c"]) == 1
    #: 命中缓存也要给拷贝：调用方会往 img 里写 role
    hit = page_images(db, 1)
    hit[0]["role"] = "废图"
    assert page_images(db, 1)[0]["role"] != "废图"
    _schema_version(db.path)  # 版本位读得出来
    db.close()
