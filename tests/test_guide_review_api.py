import pytest
"""Review console edits point/steps/images; only APPROVED publishes."""

from pathlib import Path

from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.pipeline import import_page
from hsrmap.guides.review.service import create_item
from hsrmap.guides.store import RawGuideStore
from hsrmap.viewer_app import create_app

CANARY = Path(__file__).parent / "fixtures" / "guides" / "canary" / "17173_multipoint.html"

@pytest.mark.data

def test_review_edit_then_approve(tmp_path):
    app = create_app(guide_path=tmp_path / "guide.db", guide_assets=tmp_path / "ga")
    client = TestClient(app)
    created = client.post(
        "/api/v1/review/items",
        json={
            "page": {
                "id": 1,
                "title": "海原市",
                "author": "祈鸢ya",
                "canonical_url": "https://news.17173.com/a",
                "source_claim": "米游社",
            },
            "source_point_id": "5171",
            "status": "NEEDS_REVIEW",
            "draft": {
                "map_name": "海原市",
                "steps": [{"text": "对准旋转机关", "images": ["aaa"]}],
                "evidence": {"map": 1.0, "ordinal": 0.7},
            },
        },
    )
    assert created.status_code == 200
    item_id = created.json()["id"]
    listed = client.get("/api/v1/review/items").json()["items"]
    assert listed[0]["status"] == "NEEDS_REVIEW"
    assert listed[0]["page_title"] == "海原市"
    html = client.get("/review")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "攻略审核" in html.text
    assert 'src="/review.js"' in html.text
    assert "<script>" not in html.text
    script = client.get("/review.js")
    assert script.status_code == 200
    assert "api/v1/review/items" in script.text
    assert "script-src 'self'" in html.headers.get("content-security-policy", "")
    patched = client.patch(
        f"/api/v1/review/items/{item_id}",
        json={"source_point_id": "5170", "draft": {"steps": [{"text": "向右移动后交互", "images": []}]}},
    )
    assert patched.status_code == 200
    assert patched.json()["source_point_id"] == "5170"
    assert patched.json()["draft"]["steps"][0]["text"] == "向右移动后交互"
    assert client.get("/api/v1/guides/by-point/5170").json()["entries"] == []
    approved = client.post(f"/api/v1/review/items/{item_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    entries = client.get("/api/v1/guides/by-point/5170").json()["entries"]
    assert entries[0]["steps"][0]["text"] == "向右移动后交互"
    assert client.get("/review").status_code == 200

@pytest.mark.data

def test_review_list_includes_local_page_images(tmp_path):
    assets = tmp_path / "ga" / "sha256"
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", assets)
    html = CANARY.read_text(encoding="utf-8")
    imported = import_page(
        html,
        "https://news.17173.com/content/04222026/173231817.shtml",
        db,
        store,
        fetch_asset=lambda src: b"PNG-" + src.encode(),
    )
    create_item(db, {"page_id": imported["page"]["id"], "source_point_id": "pending", "status": "AUTO_SUGGEST", "draft": {"steps": [{"text": "转", "images": []}]}})
    app = create_app(guide_path=tmp_path / "guide.db", guide_assets=assets)
    client = TestClient(app)
    item = client.get("/api/v1/review/items").json()["items"][0]
    assert item["page_images"]
    sha = item["page_images"][0]["sha"]
    assert item["page_images"][0]["url"] == f"/guide-assets/{sha}"
    assert client.get(item["page_images"][0]["url"]).status_code == 200
    js = client.get("/review.js").text
    assert "body.maps" in js
    assert "按地图" in js
    assert "candidate_points" in js
    assert "openLightbox" in js
    assert "废图已排除" in js
    assert "addEventListener" in js
    html_review = client.get("/review").text
    assert 'id="lightbox"' in html_review
    assert "onclick=" not in html_review

@pytest.mark.data

def test_review_images_use_derived_regions(tmp_path):
    assets = tmp_path / "ga" / "sha256"
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", assets)
    html = CANARY.read_text(encoding="utf-8")
    imported = import_page(
        html,
        "https://news.17173.com/content/04222026/173231817.shtml",
        db,
        store,
        fetch_asset=lambda src: b"PNG-" + src.encode(),
    )
    page_id = imported["page"]["id"]
    sha = next(block["asset"] for block in imported["blocks"] if block.get("type") == "image" and block.get("asset"))
    derived = tmp_path / "guides" / "derived" / str(page_id)
    derived.mkdir(parents=True)
    (derived / "image-roles.json").write_text(
        '{"observations":[{"block_id":"b3","sha256":"%s","role":"puzzle_step","map_name_raw":"海原市","resolved_map":{"map_name":"海原市"}}]}'
        % sha,
        encoding="utf-8",
    )
    create_item(db, {"page_id": page_id, "source_point_id": "", "status": "AUTO_SUGGEST", "draft": {}})
    app = create_app(guide_path=tmp_path / "guide.db", guide_assets=assets)
    item = TestClient(app).get("/api/v1/review/items").json()["items"][0]
    labeled = [img for img in item["page_images"] if img["sha"] == sha]
    assert labeled[0]["map_name"] == "海原市"
    assert labeled[0]["role"] == "puzzle_step"
    assert "海原市" in item["image_groups"]
    assert item["regions"] == ["海原市"]

@pytest.mark.data

def test_review_one_guide_per_map(tmp_path):
    assets = tmp_path / "ga" / "sha256"
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "guides", assets)
    html = CANARY.read_text(encoding="utf-8")

    def _page(url, label):
        imported = import_page(html, url, db, store, fetch_asset=lambda src: (label + src).encode())
        page_id = imported["page"]["id"]
        sha = next(block["asset"] for block in imported["blocks"] if block.get("type") == "image" and block.get("asset"))
        derived = tmp_path / "guides" / "derived" / str(page_id)
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "image-roles.json").write_text(
            '{"observations":[{"block_id":"b3","sha256":"%s","role":"puzzle_step","map_name_raw":"海原市","resolved_map":{"map_name":"海原市"}}]}'
            % sha,
            encoding="utf-8",
        )
        create_item(db, {"page_id": page_id, "source_point_id": "", "status": "AUTO_SUGGEST", "draft": {"map_name": "海原市"}})
        return page_id

    keep = _page("https://news.17173.com/a", "keep-")
    _page("https://www.3dmgame.com/a", "dup-")
    app = create_app(guide_path=tmp_path / "guide.db", guide_assets=assets)
    body = TestClient(app).get("/api/v1/review/items").json()
    maps = body["maps"]
    haiyuan = [row for row in maps if row["map_name"] == "海原市"]
    assert len(haiyuan) == 1
    assert haiyuan[0]["page_id"] == keep
    assert haiyuan[0]["hidden"] >= 1
    assert len(haiyuan[0]["images"]) >= 1
    js = TestClient(app).get("/review.js").text
    assert "body.maps" in js or "按地图" in js


def test_collapse_attaches_official_slots():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 1,
            "page_id": 1,
            "status": "AUTO_SUGGEST",
            "page_title": "t",
            "source_point_id": "",
            "draft": {},
            "image_groups": {"海原市": [{"sha": "a", "url": "/x"}]},
        }
    ]
    official = [{"source_point_id": "5171", "map_name": "海原市", "label": "浮脂溯源"}]
    maps = collapse_by_map(items, official)
    assert maps[0]["candidate_points"][0]["source_point_id"] == "5171"
    assert maps[0]["slots"][0]["canary"] is True


def test_collapse_keeps_items_without_recognized_map_images():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 76,
            "page_id": 10,
            "status": "NEEDS_REVIEW",
            "page_title": "黄金的时刻迷钟",
            "source_point_id": "",
            "draft": {
                "topic_key": "dream_ticker",
                "map_name": "1、黄金的时刻（4个）",
                "resolved_map_name": "黄金的时刻",
                "candidate_points": [{"source_point_id": "1609"}],
                "steps": [{"text": "第1个【梦境迷钟】"}],
            },
            "image_groups": {"未识别地区": [{"sha": "a", "url": "/x"}], "废图": []},
            "regions": [],
        }
    ]
    maps = collapse_by_map(items, [])
    assert len(maps) == 1
    assert maps[0]["map_name"] == "黄金的时刻"
    assert maps[0]["item_id"] == 76
    assert maps[0]["candidate_points"][0]["source_point_id"] == "1609"


def test_collapse_filters_candidates_by_item_topic():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 76,
            "page_id": 10,
            "status": "NEEDS_REVIEW",
            "page_title": "黄金的时刻迷钟",
            "source_point_id": "",
            "draft": {
                "topic_key": "dream_ticker",
                "map_name": "黄金的时刻",
                "resolved_map_name": "黄金的时刻",
                "candidate_points": [{"source_point_id": "1609"}],
            },
            "image_groups": {"未识别地区": [{"sha": "a", "url": "/x"}]},
        }
    ]
    official = [
        {"source_point_id": "1609", "map_name": "黄金的时刻", "label": "梦境迷钟"},
        {"source_point_id": "5171", "map_name": "黄金的时刻", "label": "浮脂溯源"},
    ]
    maps = collapse_by_map(items, official)
    ids = {str(row.get("source_point_id")) for row in maps[0]["candidate_points"]}
    assert ids == {"1609"}


def test_collapse_keeps_waste_images_off_the_map_row():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 1,
            "page_id": 1,
            "status": "NEEDS_REVIEW",
            "page_title": "酒店迷钟",
            "source_point_id": "",
            "draft": {"topic_key": "dream_ticker", "map_name": "「白日梦」酒店-梦境"},
            "image_groups": {
                "「白日梦」酒店-梦境": [{"sha": "ok", "url": "/ok", "role": "puzzle_step"}],
                "废图": [{"sha": "ad", "url": "/ad", "role": "advertisement", "src": "https://imgs.gamersky.com/upimg/new_preview/x.jpg"}],
            },
        }
    ]
    maps = collapse_by_map(items, [])
    assert {img["sha"] for img in maps[0]["images"]} == {"ok"}
    assert {img["sha"] for img in maps[0]["waste"]} == {"ad"}


def test_collapse_skips_site_chrome_titles():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 1,
            "page_id": 1,
            "status": "NEEDS_REVIEW",
            "page_title": "导语",
            "draft": {"topic_key": "dream_ticker", "map_name": "17173 新闻导语"},
            "image_groups": {"未识别地区": [{"sha": "a"}]},
            "regions": [],
        }
    ]
    assert collapse_by_map(items, []) == []


def test_collapse_skips_article_title_as_map_name():
    from hsrmap.guides.review.service import collapse_by_map

    items = [
        {
            "id": 1,
            "page_id": 1,
            "status": "NEEDS_REVIEW",
            "page_title": "酒店宝箱",
            "draft": {
                "topic_key": "dream_ticker",
                "map_name": "《崩坏星穹铁道》2.0匹诺康尼白日梦酒店梦境宝箱全收集",
            },
            "image_groups": {"未识别地区": [{"sha": "a"}]},
        }
    ]
    assert collapse_by_map(items, []) == []

def test_approve_anchored_takes_the_richest_draft_per_map(tmp_path):
    """One guide per map, from the draft that actually carries the instructions."""
    import json

    from hsrmap.guides.review.service import approve_anchored

    db = GuideDatabase(tmp_path / 'guide.db')
    source = db.upsert_source({'name': 't', 'domain': 't.test'})
    page = db.add_page(source['id'], {'canonical_url': 'https://t.test/map', 'title': '筑梦边境地图折纸小鸟全收集'})

    def item(index, steps, images, target='map:150:topic:origami_bird'):
        draft = {
            'schema_version': 2,
            'topic_key': 'origami_bird',
            'target_type': 'MAP_LABEL',
            'target_key': target,
            'map_name': f'{index}号小鸟',
            'steps': steps,
            'images': images,
        }
        cur = db.conn.execute(
            "INSERT INTO review_item(page_id, reason, status, created_at, draft_json) VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), ?)",
            (page['id'], json.dumps(draft, ensure_ascii=False)),
        )
        db.conn.commit()
        return int(cur.lastrowid)

    anchor = {'status': 'PAGE_ANCHOR', 'map_id': '150', 'map_name': '筑梦边境', 'confidence': 0.6}
    thin = item(1, [], [{'sha256': 'a' * 64, 'resolved_map': anchor}])
    rich = item(5, [{'text': '先到如图所示位置', 'images': []}, {'text': '按图操作', 'images': []}], [{'sha256': 'b' * 64, 'resolved_map': anchor}])
    unanchored = item(9, [{'text': '没有锚点', 'images': []}], [{'sha256': 'c' * 64, 'resolved_map': {'status': 'NO_MATCH'}}])

    dry = approve_anchored(db, topic='origami_bird')
    assert dry['applied'] is False and dry['targets'] == 1
    assert dry['approved'][0]['item_id'] == rich and dry['approved'][0]['steps'] == 2
    assert dry['approved'][0]['images'] == 1

    applied = approve_anchored(db, topic='origami_bird', apply=True)
    assert applied['approved'][0]['entry_id']
    rows = {int(row['id']): row['status'] for row in db.conn.execute('SELECT id, status FROM review_item')}
    assert rows[rich] == 'APPROVED' and rows[thin] == 'NEEDS_REVIEW' and rows[unanchored] == 'NEEDS_REVIEW'
    entry = dict(db.conn.execute("SELECT * FROM guide_entry WHERE id = ?", (applied['approved'][0]['entry_id'],)).fetchone())
    assert entry['source_point_id'] == 'map:150:topic:origami_bird'
    steps = [
        dict(row)
        for row in db.conn.execute('SELECT step_index, text FROM guide_steps WHERE guide_id = ? ORDER BY step_index', (entry['id'],))
    ]
    assert [step['text'] for step in steps] == ['先到如图所示位置', '按图操作']
    assets = [
        dict(row)
        for row in db.conn.execute('SELECT step_index, asset_sha256 FROM guide_assets WHERE guide_id = ?', (entry['id'],))
    ]
    assert [(asset['step_index'], asset['asset_sha256']) for asset in assets] == [(1, 'b' * 64)]
    db.close()

