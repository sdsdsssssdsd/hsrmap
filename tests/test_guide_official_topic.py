import pytest
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.discover import load_seeds_for_topic
from hsrmap.guides.review.canary import flatten_topic_points
from hsrmap.guides.topics.official import official_detail_images, official_points_for_topic
from hsrmap.guides.topics.seed import seed_official_targets


def test_flatten_uses_topic_label_not_grease():
    payload = {
        "maps": [
            {
                "map_id": "1",
                "name": "黄金的时刻",
                "path": "匹诺康尼 / 黄金的时刻",
                "points": [{"source_id": "9", "x": 1.0, "y": 2.0}],
            }
        ]
    }
    rows = flatten_topic_points(payload, label="梦境迷钟")
    assert rows[0]["label"] == "梦境迷钟"
    assert rows[0]["source_point_id"] == "9"
    assert rows[0]["region"] == "黄金的时刻"

@pytest.mark.data

def test_live_topics_load_official_points():
    grease = official_points_for_topic("floating_grease")
    ticker = official_points_for_topic("dream_ticker")
    nymph = official_points_for_topic("nymph")
    dust = official_points_for_topic("nameless_dust_spirit")
    assert len(grease) == 48
    assert len(ticker) >= 40
    assert len(nymph) >= 200
    assert len(dust) >= 150
    assert all("浮脂" in row["label"] for row in grease)
    assert all("迷钟" in row["label"] for row in ticker)
    assert all("若虫" in row["label"] for row in nymph)
    assert all("尘灵" in row["label"] for row in dust)


def test_discover_challenge_topics_have_map_guides():
    jump = load_seeds_for_topic("jump")
    hanu = load_seeds_for_topic("hanu")
    assert any("619120" in url or "111240224" in url for url in jump["urls"])
    assert any("1728961" in url or "1708241" in url for url in hanu["urls"])
    assert "1708081" not in " ".join(hanu["urls"])


def test_discover_collectible_topics_have_map_guides():
    bird = load_seeds_for_topic("origami_bird")
    nymph = load_seeds_for_topic("nymph")
    assert any("1708244" in url for url in bird["urls"])
    assert any("1909725" in url for url in nymph["urls"])
    assert "786205107434815910" not in " ".join(bird["urls"])


def test_discover_king_bucket_has_public_guides():
    seeds = load_seeds_for_topic("king_bucket")
    blob = " ".join(seeds["urls"])
    assert any("王下一桶" in query for query in seeds["queries"])
    assert "786205107434815910" not in blob
    assert any("1706816" in url for url in seeds["urls"])


def test_discover_dimensional_trotter_has_public_guides():
    seeds = load_seeds_for_topic("dimensional_trotter")
    blob = " ".join(seeds["urls"])
    assert any("扑满" in query for query in seeds["queries"])
    assert "786205107434815910" not in blob
    assert any("1592527" in url for url in seeds["urls"])
    assert any("1690364" in url for url in seeds["urls"])
    assert any("1592050" in url for url in seeds["urls"])
    assert any("133558123" in url or "1592658" in url for url in seeds["urls"])


def test_discover_zagreus_hand_has_public_guides():
    seeds = load_seeds_for_topic("zagreus_hand")
    blob = " ".join(seeds["urls"])
    assert any("扎格列斯" in query for query in seeds["queries"])
    assert "786205107434815910" not in blob
    assert any("554586" in url or "1911076" in url for url in seeds["urls"])


def test_discover_golden_scapegoat_has_public_guides():
    seeds = load_seeds_for_topic("golden_scapegoat")
    blob = " ".join(seeds["urls"])
    assert any("替罪羊" in query for query in seeds["queries"])
    assert "786205107434815910" not in blob
    assert any("1873199" in url or "1872129" in url for url in seeds["urls"])
    assert any("gamersky.com" in url or "17173.com" in url for url in seeds["urls"])


def test_discover_dream_ticker_does_not_reuse_grease_urls():
    seeds = load_seeds_for_topic("dream_ticker")
    blob = " ".join(seeds["urls"])
    assert any("迷钟" in query for query in seeds["queries"])
    assert "786205107434815910" not in blob
    assert "2092445" not in blob
    assert any("1708650" in url for url in seeds["urls"])

@pytest.mark.data

def test_seed_dream_ticker_official_targets(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    n = seed_official_targets(db, "dream_ticker")
    assert n >= 40
    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM guide_target t JOIN guide_topic tp ON tp.id = t.topic_id WHERE tp.topic_key = ?",
        ("dream_ticker",),
    ).fetchone()
    assert int(row["n"]) == n
    db.close()


def test_official_detail_images_reads_nested_viewer_payload():
    nested = {
        "core": {"source_id": "1609"},
        "detail": {
            "state": "NONEMPTY",
            "images": [{"url": "/assets/abc", "role": "image"}],
        },
    }
    assert official_detail_images(nested)[0]["url"].endswith("abc")
    assert official_detail_images({"state": "UNAVAILABLE", "images": [{"url": "/assets/x"}]}) == []
    assert official_detail_images({"images": [{"url": "/assets/flat"}]})[0]["url"].endswith("flat")
