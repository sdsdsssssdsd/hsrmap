import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.scenes import score_official_scenes
from hsrmap.guides.matching.ticker import official_candidates_for_map, score_ticker_unit
from hsrmap.guides.regions.official import resolve_official_map
from hsrmap.guides.review.service import approve_item, create_item
from hsrmap.guides.topics.dream_ticker.bind import best_map_for_unit, bind_dream_ticker
from hsrmap.guides.topics.dream_ticker.canary import pick_ticker_canaries
from hsrmap.guides.topics.dream_ticker.freeze import freeze_ticker_review
from hsrmap.guides.topics.dream_ticker.report import ticker_bind_report
from hsrmap.guides.vision.generic_roles import is_bind_evidence, normalize_role
from hsrmap.guides.vision.sanitize import sanitize_region
from hsrmap.viewer_app import create_app


def test_generic_role_normalizes_and_excludes_waste():
    assert normalize_role("location_map") == "LOCATION_MAP"
    assert normalize_role("reward") == "RESULT"
    assert normalize_role("result") == "RESULT"
    assert not is_bind_evidence("COVER")
    assert not is_bind_evidence("ADVERTISEMENT")
    assert not is_bind_evidence("UNRELATED")
    assert is_bind_evidence("LOCATION_MAP")
    assert is_bind_evidence("PUZZLE_STEP")
    assert is_bind_evidence("RESULT")


def test_vision_region_drops_hallucinated_point_id():
    out = sanitize_region(
        {
            "map_name_raw": "黄金的时刻",
            "source_point_id": "9999",
            "point_id": "123",
            "map_id": "508",
            "visible_text": ["黄金的时刻"],
        }
    )
    assert "source_point_id" not in out
    assert "point_id" not in out
    assert out.get("map_id") is None
    assert out["map_name_raw"] == "黄金的时刻"


def test_parent_region_stays_ambiguous():
    maps = [{"map_id": "pin", "name": "匹诺康尼", "renderable": False, "path": "匹诺康尼"}]
    out = resolve_official_map("匹诺康尼", maps)
    assert out["status"] == "AMBIGUOUS_REGION"
    assert out["map_id"] is None


def test_digit_or_floor_token_is_ambiguous():
    maps = [
        {"map_id": "221", "name": "3层", "renderable": True, "path": "朝露公馆 / 3层"},
        {"map_id": "181", "name": "稚子的梦-小", "renderable": True, "path": "稚子的梦"},
    ]
    assert resolve_official_map("3", maps)["status"] == "AMBIGUOUS_REGION"
    assert resolve_official_map("3", maps)["map_id"] is None
    assert resolve_official_map("3层", maps).get("map_id") == "221"


def test_unit_text_beats_other_map_on_same_page():
    points = [
        {"source_point_id": "1", "map_id": "hot", "map_name": "「白日梦」酒店-梦境", "map_path": "匹诺康尼 / 「白日梦」酒店-梦境", "region": "「白日梦」酒店-梦境", "label": "梦境迷钟"},
        {"source_point_id": "2", "map_id": "181", "map_name": "稚子的梦-小", "map_path": "匹诺康尼 / 稚子的梦 / 稚子的梦-小", "region": "稚子的梦", "label": "梦境迷钟"},
    ]
    draft = {
        "map_name": "「白日梦」酒店-梦境",
        "resolved_map_name": "稚子的梦",
        "steps": [{"text": "第1个【梦境迷钟】在「白日梦」酒店-梦境入口"}],
        "source_block_ids": ["b1"],
    }
    observations = [
        {"block_id": "b9", "role": "LOCATION_MAP", "map_name_raw": "稚子的梦-小", "resolved_map": {"status": "MATCH", "map_id": "181", "map_name": "稚子的梦-小"}},
        {"block_id": "b1", "role": "PUZZLE_STEP", "map_name_raw": None, "resolved_map": {"status": "NO_MATCH"}},
    ]
    hit = best_map_for_unit(draft, observations, points)
    assert hit.get("map_id") == "hot"
    assert "酒店" in str(hit.get("map_name") or "")


def test_mixed_region_cannot_unique_bind_even_with_scene():
    unit = {
        "map_name": "苏乐达-1号-右 / 匹诺康尼大剧院",
        "map_id": None,
        "vision_scene_scores": {"2373": 0.95},
        "guide_image_bytes": b"AAA-GUIDE" * 40,
    }
    cands = [
        {"source_point_id": "2253", "map_id": "soda", "label": "梦境迷钟", "official_image_bytes": b"ZZZ"},
        {"source_point_id": "2373", "map_id": "255", "label": "梦境迷钟", "official_image_bytes": b"AAA-GUIDE" * 40},
    ]
    bind = score_ticker_unit(unit, cands)
    assert bind["source_point_id"] == ""
    assert bind["status"] == "review"


def test_article_ordinal_cannot_unique_bind():
    unit = {"article_ordinal": 2, "map_name": "黄金的时刻", "map_id": "508"}
    cands = [
        {"source_point_id": "101", "map_id": "508", "label": "梦境迷钟"},
        {"source_point_id": "102", "map_id": "508", "label": "梦境迷钟"},
        {"source_point_id": "103", "map_id": "508", "label": "梦境迷钟"},
        {"source_point_id": "104", "map_id": "508", "label": "梦境迷钟"},
    ]
    bind = score_ticker_unit(unit, cands)
    assert bind["source_point_id"] == ""
    assert bind["status"] == "review"
    assert {row["source_point_id"] for row in bind["candidates"]} == {"101", "102", "103", "104"}


def test_scene_compare_drops_hallucinated_point_id():
    class Provider:
        def compare_scenes(self, guide_bytes, candidates):
            return {"candidate": "9999", "confidence": 0.99}

    scores = score_official_scenes(
        b"guide",
        [{"source_point_id": "101", "official_image_bytes": b"off-101"}, {"source_point_id": "102", "official_image_bytes": b"off-102"}],
        Provider(),
    )
    assert scores == {}


def test_scene_payload_drops_candidates_without_official_bytes():
    from hsrmap.guides.matching.scenes import build_scene_payload

    payload = build_scene_payload(
        b"guide-bytes",
        [
            {"source_point_id": "101"},
            {"source_point_id": "102", "official_image_bytes": b"official-102"},
            {"source_point_id": "", "official_image_bytes": b"orphan"},
        ],
    )
    assert payload["allowed"] == ["102"]
    official_ids = [part["source_point_id"] for part in payload["parts"] if part.get("role") == "official"]
    assert official_ids == ["102"]
    assert payload["parts"][0]["role"] == "guide"


def test_score_official_scenes_skips_without_official_bytes():
    called = []

    class Provider:
        def compare_scenes(self, guide_bytes, candidates):
            called.append(1)
            return {"candidate": "101", "confidence": 0.99}

    scores = score_official_scenes(b"guide", [{"source_point_id": "101"}], Provider())
    assert scores == {}
    assert called == []


def test_pairwise_scene_picks_unique_official():
    class Provider:
        def compare_scenes(self, guide_bytes, candidates):
            if len(candidates) != 1:
                return {"candidate": None, "confidence": 0.0}
            pid = str(candidates[0]["source_point_id"])
            if pid == "102":
                return {"candidate": "102", "confidence": 0.91}
            return {"candidate": None, "confidence": 0.2}

    scores = score_official_scenes(
        b"guide",
        [
            {"source_point_id": "101", "official_image_bytes": b"off-101"},
            {"source_point_id": "102", "official_image_bytes": b"off-102"},
        ],
        Provider(),
    )
    assert scores == {"102": 0.91}


def test_pairwise_scene_ties_stay_unbound():
    class Provider:
        def compare_scenes(self, guide_bytes, candidates):
            if len(candidates) != 1:
                return {"candidate": None, "confidence": 0.0}
            pid = str(candidates[0]["source_point_id"])
            return {"candidate": pid, "confidence": 0.9}

    scores = score_official_scenes(
        b"guide",
        [
            {"source_point_id": "101", "official_image_bytes": b"off-101"},
            {"source_point_id": "102", "official_image_bytes": b"off-102"},
        ],
        Provider(),
    )
    assert scores == {}


def test_pick_guide_observation_prefers_puzzle_step():
    from hsrmap.guides.matching.scenes import pick_guide_observation

    hit = pick_guide_observation(
        [
            {"role": "LOCATION_MAP", "sha256": "map-sha"},
            {"role": "COVER", "sha256": "cover-sha"},
            {"role": "PUZZLE_STEP", "sha256": "clock-sha"},
            {"role": "RESULT", "sha256": "result-sha"},
        ]
    )
    assert hit["sha256"] == "clock-sha"


def test_official_image_similarity_outranks_ordinal():
    unit = {
        "article_ordinal": 1,
        "map_name": "黄金的时刻",
        "map_id": "508",
        "guide_image_bytes": b"AAA-GUIDE" * 40,
    }
    cands = [
        {"source_point_id": "101", "map_id": "508", "label": "梦境迷钟", "official_image_bytes": b"ZZZ-OTHER" * 40},
        {"source_point_id": "102", "map_id": "508", "label": "梦境迷钟", "official_image_bytes": b"AAA-GUIDE" * 40},
    ]
    bind = score_ticker_unit(unit, cands)
    assert bind["candidates"][0]["source_point_id"] == "102"
    assert bind["source_point_id"] == "102"
    assert bind["status"] == "auto"


def test_region_parent_keeps_official_candidates_without_leaf_map():
    points = [
        {
            "source_point_id": "201",
            "map_id": "reef-3",
            "map_name": "3层",
            "map_path": "匹诺康尼 / 流梦礁 / 3层",
            "region": "流梦礁",
            "label": "梦境迷钟",
        },
        {
            "source_point_id": "202",
            "map_id": "reef-1",
            "map_name": "1层",
            "map_path": "匹诺康尼 / 流梦礁 / 1层",
            "region": "流梦礁",
            "label": "梦境迷钟",
        },
    ]
    maps = [
        {"map_id": None, "name": "流梦礁", "path": "流梦礁", "renderable": False},
        {"map_id": "reef-3", "name": "3层", "path": "匹诺康尼 / 流梦礁 / 3层", "renderable": True},
    ]
    assert resolve_official_map("流梦礁", maps)["status"] == "AMBIGUOUS_REGION"
    hit = best_map_for_unit({"map_name": "流梦礁", "steps": [{"text": "流梦礁天台迷钟"}]}, [], points)
    assert hit.get("map_id") in {None, ""}
    from hsrmap.guides.matching.candidates import query_candidates

    cands = query_candidates("流梦礁", points, semantic=None, label_names=["梦境迷钟"])
    assert {row["source_point_id"] for row in cands} == {"201", "202"}


def test_candidates_only_official_ticker_label():
    points = [
        {"source_point_id": "1", "map_id": "508", "map_name": "黄金的时刻", "label": "梦境迷钟"},
        {"source_point_id": "2", "map_id": "508", "map_name": "黄金的时刻", "label": "宝箱"},
        {"source_point_id": "9", "map_id": "999", "map_name": "其他", "label": "梦境迷钟"},
    ]
    hits = official_candidates_for_map("508", points, label_names=["梦境迷钟"])
    assert [row["source_point_id"] for row in hits] == ["1"]


def _ticker_item(db, url, **draft):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": draft.get("map_name") or "t"})
    return create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": draft.pop("source_point_id", ""),
            "status": "NEEDS_REVIEW",
            "draft": {"topic_key": "dream_ticker", "label": "梦境迷钟", **draft},
        },
    )


def test_freeze_does_not_create_or_approve(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    first = _ticker_item(db, "https://t.test/a", map_name="黄金的时刻", map_id="508")
    _ticker_item(db, "https://t.test/b", map_name="流梦礁", map_id="reef")
    snap = freeze_ticker_review(db, dest=tmp_path / "freeze.json", official_targets=43)
    assert snap["review_items"] == 2
    assert snap["official_targets"] == 43
    assert snap["review_items"] != snap["official_targets"]
    assert db.conn.execute("SELECT COUNT(*) n FROM review_item").fetchone()["n"] == 2
    assert db.conn.execute("SELECT status FROM review_item WHERE id=?", (first["id"],)).fetchone()["status"] != "APPROVED"


def test_canary_skips_chrome_and_empty_candidates():
    items = [
        {"id": 1, "map_name": "17173 新闻导语", "candidate_count": 0, "confidence": 0.0, "map_id": None},
        {"id": 2, "map_name": "黄金的时刻", "candidate_count": 4, "confidence": 0.2, "map_id": "508"},
        {"id": 3, "map_name": "流梦礁", "candidate_count": 3, "confidence": 0.8, "map_id": "reef"},
        {"id": 4, "map_name": "晖长石号", "candidate_count": 4, "confidence": 0.7, "map_id": "tv"},
        {"id": 5, "map_name": "筑梦边境", "candidate_count": 3, "confidence": 0.6, "map_id": "border"},
        {"id": 6, "map_name": "朝露公馆-3", "candidate_count": 3, "confidence": 0.5, "map_id": "dawn"},
        {"id": 7, "map_name": "克劳克影视乐园", "candidate_count": 4, "confidence": 0.15, "map_id": "film"},
        {"id": 8, "map_name": "《崩坏星穹铁道》2.2新地图", "candidate_count": 0, "confidence": 0.0, "map_id": None},
    ]
    picked = pick_ticker_canaries(items, limit=6)
    names = {row["map_name"] for row in picked}
    assert "17173 新闻导语" not in names
    assert "《崩坏星穹铁道》2.2新地图" not in names
    assert "黄金的时刻" in names
    assert any(row["candidate_count"] >= 4 for row in picked)


def test_canary_covers_maps_multi_and_low_confidence():
    items = [
        {"id": 1, "map_name": "黄金的时刻", "candidate_count": 4, "confidence": 0.91, "map_id": "508"},
        {"id": 2, "map_name": "黄金的时刻", "candidate_count": 4, "confidence": 0.2, "map_id": "508"},
        {"id": 3, "map_name": "流梦礁", "candidate_count": 3, "confidence": 0.8, "map_id": "reef"},
        {"id": 4, "map_name": "晖长石号", "candidate_count": 4, "confidence": 0.7, "map_id": "tv"},
        {"id": 5, "map_name": "筑梦边境", "candidate_count": 3, "confidence": 0.6, "map_id": "border"},
        {"id": 6, "map_name": "朝露公馆-3", "candidate_count": 3, "confidence": 0.5, "map_id": "dawn"},
        {"id": 7, "map_name": "克劳克影视乐园", "candidate_count": 4, "confidence": 0.4, "map_id": "film"},
        {"id": 8, "map_name": "苏乐达-1号-左", "candidate_count": 7, "confidence": 0.15, "map_id": "soda"},
    ]
    picked = pick_ticker_canaries(items, limit=6)
    assert 5 <= len(picked) <= 8
    names = {row["map_name"] for row in picked}
    assert len(names) >= 4
    assert any(row["candidate_count"] >= 4 for row in picked)
    assert any(row["confidence"] < 0.3 for row in picked)


def test_bind_report_never_counts_auto_as_approved():
    report = ticker_bind_report(
        review_items=29,
        official_targets=43,
        vision_resolved=8,
        candidate_generated=11,
        point_match=4,
        human_correct=0,
        approved=0,
        wrong_published=0,
        hallucinated=0,
    )
    assert report["Approved Real Guide Coverage"] == "0 / 43"
    assert report["Point Match Coverage"] == "4 / 29"
    assert report["Dream Ticker review items"] == 29
    assert report["Wrong published bindings"] == 0


def test_bind_canary_never_approves_or_creates(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    official = [
        {"source_point_id": "101", "map_id": "508", "map_name": "黄金的时刻", "label": "梦境迷钟"},
        {"source_point_id": "102", "map_id": "508", "map_name": "黄金的时刻", "label": "梦境迷钟"},
        {"source_point_id": "201", "map_id": "reef", "map_name": "流梦礁", "label": "梦境迷钟"},
        {"source_point_id": "202", "map_id": "reef", "map_name": "流梦礁", "label": "梦境迷钟"},
        {"source_point_id": "203", "map_id": "reef", "map_name": "流梦礁", "label": "梦境迷钟"},
        {"source_point_id": "301", "map_id": "tv", "map_name": "晖长石号", "label": "梦境迷钟"},
        {"source_point_id": "401", "map_id": "hot", "map_name": "酒店", "label": "梦境迷钟"},
        {"source_point_id": "501", "map_id": "film", "map_name": "克劳克影视乐园", "label": "梦境迷钟"},
    ]
    for name, mid, url in [
        ("黄金的时刻", "508", "https://t.test/gold"),
        ("流梦礁", "reef", "https://t.test/reef"),
        ("晖长石号", "tv", "https://t.test/tv"),
        ("酒店", "hot", "https://t.test/hot"),
        ("克劳克影视乐园", "film", "https://t.test/film"),
        ("筑梦边境", "border", "https://t.test/border"),
    ]:
        _ticker_item(db, url, map_name=name, map_id=mid)
    before = db.conn.execute("SELECT COUNT(*) n FROM review_item").fetchone()["n"]
    report = bind_dream_ticker(
        db,
        official_points=official,
        provider=None,
        canary_only=True,
        dest=tmp_path / "freeze.json",
    )
    after = db.conn.execute("SELECT COUNT(*) n FROM review_item").fetchone()["n"]
    statuses = [row["status"] for row in db.conn.execute("SELECT status FROM review_item")]
    assert after == before
    assert "APPROVED" not in statuses
    assert report["Approved Real Guide Coverage"] == "0 / 8"

@pytest.mark.data

def test_approve_ticker_requires_candidate_and_returns_409(tmp_path, make_guide_db):
    db = GuideDatabase(tmp_path / "g.db")
    catalog = [
        {"source_point_id": "101", "map_id": "508", "label": "梦境迷钟"},
        {"source_point_id": "102", "map_id": "508", "label": "梦境迷钟"},
    ]
    missing = _ticker_item(
        db,
        "https://t.test/miss",
        map_id="508",
        semantic_key="梦境迷钟",
        source_point_id="101",
        candidate_points=[],
    )
    try:
        approve_item(db, missing["id"], official_points=catalog)
        raise AssertionError("expected reject")
    except ValueError as exc:
        assert "candidate" in str(exc)

    #: viewer 只读写、不建库（a1-8 四.2）：工作库先显式建出来。
    app = create_app(guide_path=make_guide_db("app.db"), guide_assets=tmp_path / "ga")
    client = TestClient(app)
    created = client.post(
        "/api/v1/review/items",
        json={
            "page": {"canonical_url": "https://t.test/app", "title": "黄金的时刻"},
            "source_point_id": "101",
            "draft": {"topic_key": "dream_ticker", "map_id": "508", "semantic_key": "梦境迷钟", "candidate_points": []},
        },
    )
    resp = client.post(f"/api/v1/review/items/{created.json()['id']}/approve")
    assert resp.status_code == 409
