"""a1-8 十三：Web 要能回答「凭什么算完成」（证据 API + 地图进程的只读攻略面）。

注意：这里**不用** `with TestClient(app)`。一个进程里连续创建多个 TestClient 时，
上一个 app 的 lifespan 退出会打到下一个 app 上（Starlette 测试客户端的既有行为），
把刚打开的发布库关掉；真实部署是一进程一 app，所以这只是测试夹具的写法问题。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.claims import backfill
from hsrmap.viewer_app import create_map_app, create_review_app

SHA = "b" * 64
SOLVE_TEXT = "黄金替罪羊解法：右右左右右右，然后左右，再一直向左"
TRANS_TEXT = f"[图解法转录 {SHA}] 特殊区域2：面板第一排是右左左左右左。"


def _published(tmp_path: Path, *, inference: bool = False) -> Path:
    """一个小的发布快照：一条社区攻略（正文 + 图解转录）。"""
    db = GuideDatabase.create(tmp_path / "published.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    path = tmp_path / "page.txt"
    path.write_text(f"{SOLVE_TEXT} {SOLVE_TEXT}", encoding="utf-8")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title, raw_text_path)"
        " VALUES (1, 1, 'https://t.test/a', '来源', ?)",
        (str(path),),
    )
    db.create_entry({
        "source_point_id": "408",
        "title": "攻略",
        "status": "published",
        "source_url": "https://t.test/a",
        "source_kind": "Community",
        "steps": [
            {"text": SOLVE_TEXT},
            {"text": TRANS_TEXT, "images": [SHA]},
        ],
    })
    db.conn.commit()
    backfill(db, apply=True)
    if inference:
        db.conn.execute(
            "UPDATE guide_claim_evidence SET evidence_level = 'CROSS_INFERENCE', basis_json = ?"
            " WHERE step_id = (SELECT MIN(id) FROM guide_steps)",
            ('{"refs": ["official:408"]}',),
        )
        db.conn.commit()
    db.close()
    return tmp_path / "published.db"


def _map_client(tmp_path: Path, **kwargs) -> TestClient:
    published = _published(tmp_path, inference=bool(kwargs.pop("inference", False)))
    app = create_map_app(published_path=published, guide_path=published, **kwargs)
    return TestClient(app)


def test_evidence_overview_reports_layers_and_levels(tmp_path):
    client = _map_client(tmp_path)
    body = client.get("/api/v1/guides/evidence").json()
    assert body["available"] is True
    assert body["claims"]["total"] == 2
    assert body["claims"]["by_level"] == {"COMMUNITY_TEXT": 1, "TRANSCRIPTION": 1}
    assert set(body["layers"]) == {"direct", "transcription", "inference", "missing"}
    assert body["claims"]["digest"]


def test_point_evidence_exposes_each_step_claim(tmp_path):
    client = _map_client(tmp_path)
    body = client.get("/api/v1/guides/evidence/408").json()
    assert body["available"] is True
    assert body["status"] is None, "没有官方点位数据时如实说「判不了」，而不是编一个状态"
    entry = body["entries"][0]
    assert entry["guide_id"] == 1 and entry["title"] == "攻略"
    levels = [step["evidence_level"] for step in entry["steps"]]
    assert levels == ["COMMUNITY_TEXT", "TRANSCRIPTION"]
    transcribed = entry["steps"][1]
    assert transcribed["transcribed"] is True and transcribed["inferred"] is False
    assert transcribed["asset_sha256"] == SHA and transcribed["grounding_tier"] == "IMAGE_REF"
    assert transcribed["claim_kind"] == "SOLVE"
    community = entry["steps"][0]
    assert community["grounding_tier"] == "EXACT" and community["source_page_id"] == 1


def test_cross_inference_is_marked_as_inference(tmp_path):
    """推断必须在界面上和正文引证长得不一样——不能只靠用户自己看等级字符串。"""
    client = _map_client(tmp_path, inference=True)
    body = client.get("/api/v1/guides/evidence/408").json()
    step = body["entries"][0]["steps"][0]
    assert step["evidence_level"] == "CROSS_INFERENCE"
    assert step["inferred"] is True and step["transcribed"] is False
    assert step["basis"] == ["official:408"]


def test_map_app_serves_the_read_only_guide_surface(tmp_path):
    """地图页要用攻略数据画「有攻略 / 为什么算完成」，但绝不能拿到写权限。"""
    client = _map_client(tmp_path)
    #: 路由存在（没有快照时按约定 503，而不是 404）；写端点一个都没有。
    assert client.get("/api/v1/guides/by-point/408").status_code != 404
    assert client.get("/api/v1/guides/index").status_code != 404
    assert client.get("/api/v1/guides/evidence").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/api/v1/guides", json={"source_point_id": "1"}).status_code == 404
    assert client.get("/api/v1/review/items").status_code == 404


def test_map_app_without_a_snapshot_says_so_instead_of_failing(tmp_path):
    """没有发布快照是合法状态：地图要起得来，接口如实说 available: false。"""
    app = create_map_app(
        published_path=tmp_path / "missing.db", guide_path=tmp_path / "missing.db"
    )
    client = TestClient(app)
    assert client.get("/api/v1/guides/evidence").json() == {"available": False}
    detail = client.get("/api/v1/guides/evidence/408").json()
    assert detail["available"] is False and detail["entries"] == []
    assert client.get("/").status_code == 200


def test_review_app_keeps_the_write_surface(tmp_path):
    published = _published(tmp_path)
    app = create_review_app(published_path=published, guide_path=published)
    client = TestClient(app)
    assert client.get("/api/v1/guides/evidence").status_code == 200
    assert client.get("/api/v1/review/items").status_code in {200, 503}


@pytest.mark.data  # 需要真实官方点位与发布快照
def test_real_point_explains_why_it_counts_as_complete():
    from hsrmap.paths import GUIDE_PUBLISHED_DB

    client = TestClient(create_map_app(published_path=GUIDE_PUBLISHED_DB))
    body = client.get("/api/v1/guides/evidence/3556").json()
    assert body["available"] is True
    status = body["status"]
    assert status is not None and status["done"] is True
    assert status["locate_evidence"] == "OFFICIAL"
    #: 3556 的解法是靠图解转录成立的：详情页必须把这件事说出来。
    assert status["solve_evidence"] == "TRANSCRIPTION"
    levels = {step["evidence_level"] for entry in body["entries"] for step in entry["steps"]}
    assert "TRANSCRIPTION" in levels
    overview = client.get("/api/v1/guides/evidence").json()
    assert overview["completion"]["done"] == overview["completion"]["points"] > 0
    assert overview["layers"]["transcription"] > 0
