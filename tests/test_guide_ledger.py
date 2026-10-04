import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ledger import WaveLocked, assert_wave_unlocked, build_gates, topic_ledger
from hsrmap.guides.review.service import create_item
from hsrmap.viewer_app import create_app


def _page(db, url="https://t.test/a"):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    return db.add_page(source["id"], {"canonical_url": url, "title": "t"})


def test_ledger_splits_published_matched_and_needs_source(tmp_path):
    working = GuideDatabase(tmp_path / "w.db")
    published = GuideDatabase(tmp_path / "p.db")
    page = _page(working)
    pub_page = _page(published, "https://t.test/pub")
    published.create_entry(
        {
            "source_point_id": "5171",
            "title": "浮脂1",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    create_item(
        working,
        {
            "page_id": page["id"],
            "source_point_id": "5172",
            "status": "AUTO_SUGGEST",
            "draft": {"topic_key": "floating_grease", "candidate_points": [{"source_point_id": "5172"}]},
        },
    )
    official = [
        {"source_point_id": "5171", "map_id": "a"},
        {"source_point_id": "5172", "map_id": "a"},
        {"source_point_id": "5173", "map_id": "b"},
    ]
    report = topic_ledger(working, "floating_grease", official_points=official, published_db=published)
    by = {row["source_point_id"]: row["status"] for row in report["targets"]}
    assert report["official_targets"] == 3
    assert by["5171"] == "PUBLISHED"
    assert by["5172"] == "MATCHED"
    assert by["5173"] == "NEEDS_SOURCE"
    assert report["counts"]["PUBLISHED"] == 1
    assert report["counts"]["NEEDS_SOURCE"] == 1
    assert report["engine"] != report["corpus_label"]
    working.close()
    published.close()


def test_ledger_zero_official_is_registered_not_fake_points(tmp_path):
    working = GuideDatabase(tmp_path / "w.db")
    report = topic_ledger(working, "magnetic_puzzle", official_points=[])
    assert report["official_targets"] == 0
    assert report["topic_status"] == "NO_OFFICIAL_TARGET"
    assert report["targets"] == []
    working.close()


def test_cli_process_wave_all_errors_without_force(tmp_path, capsys):
    from hsrmap.cli import main

    code = main(
        [
            "guides",
            "process",
            "--wave",
            "all",
            "--db",
            str(tmp_path / "w.db"),
            "--published",
            str(tmp_path / "p.db"),
        ]
    )
    assert code == 2
    assert "Atlas engine gates are not complete" in capsys.readouterr().out


def test_ledger_marks_no_public_source_found(tmp_path):
    working = GuideDatabase(tmp_path / "w.db")
    page = _page(working)
    create_item(
        working,
        {
            "page_id": page["id"],
            "source_point_id": "4630",
            "status": "NO_PUBLIC_SOURCE_FOUND",
            "draft": {"topic_key": "pioneer_fairy"},
        },
    )
    report = topic_ledger(
        working,
        "pioneer_fairy",
        official_points=[{"source_point_id": "4630", "map_id": "518"}],
    )
    assert report["targets"][0]["status"] == "NO_PUBLIC_SOURCE_FOUND"
    assert report["counts"]["NO_PUBLIC_SOURCE_FOUND"] == 1
    assert report["no_source_yet"] == 0
    working.close()


def test_wave_process_locked_until_gates_pass():
    gates = {"result": "BLOCKED", "A": {"result": "BLOCKED"}}
    try:
        assert_wave_unlocked(gates)
    except WaveLocked as exc:
        assert "Atlas engine gates are not complete" in str(exc)
    else:
        raise AssertionError("expected WaveLocked")
    assert_wave_unlocked(gates, force=True)


def test_ledger_counts_scope_keys_as_engine_published(tmp_path):
    working = GuideDatabase(tmp_path / "w.db")
    published = GuideDatabase(tmp_path / "p.db")
    pub_page = _page(published, "https://t.test/scope")
    published.create_entry(
        {
            "source_point_id": "map:149:topic:origami_bird",
            "title": "黄金的时刻小鸟",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    published.create_entry(
        {
            "source_point_id": "map:150:topic:origami_bird",
            "title": "筑梦边境小鸟",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    bird = topic_ledger(
        working,
        "origami_bird",
        official_points=[
            {"source_point_id": "1926", "map_id": "149"},
            {"source_point_id": "1943", "map_id": "150"},
            {"source_point_id": "1955", "map_id": "154"},
        ],
        published_db=published,
    )
    assert bird["published"] == 2
    assert bird["official_targets"] == 3
    by_map = {row["map_id"]: row["status"] for row in bird["targets"]}
    assert by_map["149"] == "PUBLISHED"
    assert by_map["150"] == "PUBLISHED"
    assert by_map["154"] == "NEEDS_SOURCE"

    published.create_entry(
        {
            "source_point_id": "set:346:topic:zagreus_hand",
            "title": "扎格列斯之手",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    zag = topic_ledger(
        working,
        "zagreus_hand",
        official_points=[
            {"source_point_id": "4135", "map_id": "346"},
            {"source_point_id": "4136", "map_id": "345"},
        ],
        published_db=published,
    )
    assert zag["published"] == 1

    published.create_entry(
        {
            "source_point_id": "global:topic:jump",
            "title": "JUMP成就",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    jump = topic_ledger(
        working,
        "jump",
        official_points=[{"source_point_id": "5261", "map_id": "770"}],
        published_db=published,
    )
    assert jump["published"] == 1
    working.close()
    published.close()

def test_a_map_is_published_when_every_one_of_its_points_is(tmp_path):
    """A page that covers an area yields one set per region, never a map-key entry."""
    working = GuideDatabase(tmp_path / "w.db")
    published = GuideDatabase(tmp_path / "p.db")
    pub_page = _page(published, "https://t.test/points")
    published.create_entry(
        {
            "source_point_id": "set:1926-1943:topic:origami_bird",
            "title": "黄金的时刻小鸟（两点）",
            "status": "published",
            "page_id": pub_page["id"],
        }
    )
    report = topic_ledger(
        working,
        "origami_bird",
        official_points=[
            {"source_point_id": "1926", "map_id": "149"},
            {"source_point_id": "1943", "map_id": "149"},
            {"source_point_id": "1955", "map_id": "154"},
        ],
        published_db=published,
    )
    by_map = {row["map_id"]: row["status"] for row in report["targets"]}
    # both points of map 149 are published, so the map itself is covered
    assert by_map["149"] == "PUBLISHED"
    assert by_map["154"] == "NEEDS_SOURCE"
    working.close()
    published.close()


@pytest.mark.data

def test_gates_payload_blocked_and_exposed_on_review(tmp_path):
    app = create_app(guide_path=tmp_path / "w.db", published_path=tmp_path / "p.db", guide_assets=tmp_path / "ga")
    client = TestClient(app)
    body = client.get("/api/v1/atlas/gates").json()
    assert body["result"] == "BLOCKED"
    assert body["A"]["result"] in {"BLOCKED", "CANARY"}
    assert "Dream Ticker" in body["A"]["topics"][1]["display"] or body["A"]["topics"][1]["topic"] == "dream_ticker"
    html = client.get("/review").text
    assert "Atlas Gates" in html
    assert "Engine Gate" in html
