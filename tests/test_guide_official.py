"""官方点位详情（文字 + 官方截图）作为一种来源，走与社区攻略相同的发布通道。"""

import io
import sqlite3

import pytest
from PIL import Image

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.assets.fetcher import AssetFetcher
from hsrmap.guides.official import official_details, seed_official_guides

POINTS = [
    {"source_point_id": "3481", "map_id": "398", "map_name": "特殊房间", "region": "特殊房间", "label": "若虫"},
    {"source_point_id": "3545", "map_id": "408", "map_name": "特殊房间", "region": "特殊房间", "label": "若虫"},
    {"source_point_id": "9999", "map_id": "409", "map_name": "特殊房间", "region": "特殊房间", "label": "若虫"},
]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (30, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _detail_db(tmp_path, rows):
    path = tmp_path / "detail.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE point_details (id TEXT PRIMARY KEY, request_id TEXT, source_point_id TEXT, title TEXT,"
        " subtitle TEXT, plain_text TEXT, content_format TEXT, content_raw TEXT, is_empty INTEGER,"
        " detail_state TEXT, raw_sha256 TEXT)"
    )
    con.execute(
        "CREATE TABLE point_detail_assets (id INTEGER PRIMARY KEY, detail_id TEXT, asset_sha256 TEXT,"
        " role TEXT, sort_order INTEGER, remote_url TEXT, alt_text TEXT, metadata_json TEXT, state TEXT)"
    )
    for index, (point_id, text, state, url) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO point_details VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(index), str(index), point_id, None, None, text, "plain", text, 0, state, "sha"),
        )
        if url:
            con.execute(
                "INSERT INTO point_detail_assets VALUES (?,?,?,?,?,?,?,?,?)",
                (index, str(index), "sha%d" % index, "image", 0, url, None, "{}", "COMPLETE"),
            )
    con.commit()
    con.close()
    return path


def _db(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    for index, point in enumerate(POINTS):
        db.conn.execute(
            "INSERT INTO guide_target_status(topic_key, target_key, source_point_id, map_id, status, updated_at)"
            " VALUES ('nymph', ?, ?, ?, 'NEEDS_SOURCE', datetime('now'))",
            ("point:" + point["source_point_id"], point["source_point_id"], point["map_id"]),
        )
    db.conn.commit()
    return db


def test_official_details_are_read_by_point_id(tmp_path):
    detail_db = _detail_db(tmp_path, [("3481", "位于门旁的凳子上。", "NONEMPTY", "https://o.test/a.png")])
    found = official_details(detail_db, ["3481", "0000"])
    assert found["3481"]["text"] == "位于门旁的凳子上。"
    assert found["3481"]["assets"][0]["url"] == "https://o.test/a.png"
    assert "0000" not in found


def test_a_point_with_an_official_line_but_no_picture_is_still_located(tmp_path):
    """官方说明本身就是 LOCATE 证据：有图更好，没图也发（不是「无官方图就跳过」）。"""
    db = _db(tmp_path)
    detail_db = _detail_db(
        tmp_path,
        [
            ("3481", "位于门旁的凳子上。", "NONEMPTY", "https://o.test/a.png"),
            ("3545", "", "EMPTY", ""),
            ("9999", "位于此处角落。", "NONEMPTY", ""),
        ],
    )
    report = seed_official_guides(db, "nymph", detail_db=detail_db, points=POINTS)
    assert report["needy"] == 3
    assert report["published"] == 2
    assert report["text_only"] == 1
    # 只有「连说明都没有」的点位才跳过
    assert report["skipped_reasons"] == {"OFFICIAL_DETAIL_EMPTY": 1}
    text_only = next(item for item in report["entries"] if item["point"] == "9999")
    assert text_only["sha"] == "" and text_only["text_only"] is True
    db.close()


def test_publishing_a_text_only_official_point_points_at_the_official_map(tmp_path, monkeypatch):
    import hsrmap.guides.official as official

    monkeypatch.setattr(official, "GUIDE_RAW", tmp_path / "raw")
    monkeypatch.setattr(official, "GUIDE_ASSETS", tmp_path / "guide-assets" / "sha256")
    monkeypatch.setattr(official, "GUIDE_CACHE", tmp_path / "guide-cache")
    db = _db(tmp_path)
    detail_db = _detail_db(tmp_path, [("9999", "位于此处角落。", "NONEMPTY", "")])
    report = seed_official_guides(db, "nymph", apply=True, detail_db=detail_db, points=POINTS)
    assert report["published"] == 1 and report["text_only"] == 1
    row = db.conn.execute("SELECT * FROM guide_entry WHERE source_point_id = '9999'").fetchone()
    assert row["source_kind"] == "Official"
    assert row["source_url"] == official.OFFICIAL_MAP_URL
    steps = db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ?", (row["id"],)).fetchall()
    assert [item["text"] for item in steps] == ["位于此处角落。"]
    assets = db.conn.execute("SELECT COUNT(*) AS c FROM guide_assets WHERE guide_id = ?", (row["id"],)).fetchone()
    assert assets["c"] == 0
    db.close()


def test_an_official_action_line_without_a_picture_is_published_too(tmp_path):
    """说明没写「在哪儿」但写了「怎么做」（「击落空中气球获得。」）时也发文字条目。"""
    db = _db(tmp_path)
    detail_db = _detail_db(tmp_path, [("9999", "击落空中气球获得。", "NONEMPTY", "")])
    report = seed_official_guides(db, "nymph", detail_db=detail_db, points=POINTS)
    assert report["text_only"] == 1
    assert report["skipped_reasons"] == {"OFFICIAL_DETAIL_EMPTY": 2}
    entry = next(item for item in report["entries"] if item["point"] == "9999")
    assert entry["text"] == "击落空中气球获得。"
    db.close()


def test_an_official_line_that_does_not_say_where_is_not_published_as_text(tmp_path):
    """范围说明/设定文案不是定位证据：「此处地图区域存在1个王下一桶」发出来会是假证据。"""
    db = _db(tmp_path)
    detail_db = _detail_db(
        tmp_path,
        [
            ("3481", "此处地图区域存在1个王下一桶", "NONEMPTY", ""),
            ("3545", "位于此处窗户上。", "NONEMPTY", ""),
            ("9999", "翁法罗斯各处可见的巨型万能工具。", "NONEMPTY", ""),
        ],
    )
    report = seed_official_guides(db, "nymph", detail_db=detail_db, points=POINTS)
    assert report["skipped_reasons"] == {"OFFICIAL_TEXT_NOT_LOCATING": 2}
    assert report["published"] == 1 and report["text_only"] == 1
    db.close()


def test_all_points_follows_the_completion_model(tmp_path):
    """needy = 完成模型还没做完的点位。

    第 1 阶段以官方为准（用户 2026-10 指示）：若虫的点位在官方地图上就有定位，
    所以它们不再是补发对象；还缺 SOLVE 的主题（黄金替罪羊）才是。
    """
    db = _db(tmp_path)
    detail_db = _detail_db(tmp_path, [("3481", "位于门旁的凳子上。", "NONEMPTY", "https://o.test/a.png")])
    report = seed_official_guides(db, "nymph", detail_db=detail_db, points=POINTS, all_points=True)
    assert report["needy"] == 0
    assert report["published"] == 0
    db.close()


@pytest.mark.data  # 用真实官方点位（data/snapshots）；submit 副本里跳过
def test_all_points_still_seeds_a_topic_that_is_missing_solve_evidence(tmp_path):
    """第 2 阶段的缺口仍然要补：官方说明里带动作的点位（「击落空中气球获得。」）就是解法。"""
    from hsrmap.guides.topics.official import official_points_for_topic

    db = _db(tmp_path)
    detail_db = _detail_db(tmp_path, [("3481", "位于门旁的凳子上。", "NONEMPTY", "https://o.test/a.png")])
    real = official_points_for_topic("golden_scapegoat") or []
    assert real, "official golden_scapegoat points should be available"
    report = seed_official_guides(db, "golden_scapegoat", detail_db=detail_db, points=real, all_points=True)
    assert report["needy"] == len(real)
    db.close()


def test_publishing_an_official_point_writes_the_entry_and_the_image(tmp_path, monkeypatch):
    import hsrmap.guides.official as official

    monkeypatch.setattr(official, "GUIDE_RAW", tmp_path / "raw")
    monkeypatch.setattr(official, "GUIDE_ASSETS", tmp_path / "guide-assets" / "sha256")
    monkeypatch.setattr(official, "GUIDE_CACHE", tmp_path / "guide-cache")
    db = _db(tmp_path)
    detail_db = _detail_db(tmp_path, [("3481", "位于门旁的凳子上。", "NONEMPTY", "https://o.test/a.png")])
    fetcher = AssetFetcher(transport=lambda url, headers, timeout: (200, {"Content-Type": "image/png"}, _png()))

    report = seed_official_guides(db, "nymph", apply=True, detail_db=detail_db, fetcher=fetcher, points=POINTS)
    assert report["published"] == 1
    row = db.conn.execute("SELECT * FROM guide_entry WHERE source_point_id = '3481'").fetchone()
    assert row["source_kind"] == "Official"
    assert "官方" in row["title"]
    steps = db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ?", (row["id"],)).fetchall()
    assert steps and steps[0]["text"] == "位于门旁的凳子上。"
    assets = db.conn.execute("SELECT asset_sha256 FROM guide_assets WHERE guide_id = ?", (row["id"],)).fetchall()
    assert assets and assets[0]["asset_sha256"]
    sha = str(assets[0]["asset_sha256"])
    stored = list((tmp_path / "guide-assets" / "sha256" / sha[:2]).glob(sha + "*"))
    assert stored, "the official image has to be readable where published guides look"
    db.close()
