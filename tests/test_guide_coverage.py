"""Point Match Coverage is not Real Guide Coverage."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.coverage import build_coverage
from hsrmap.guides.dedup import classify_duplicate


def test_coverage_splits_match_from_approved_guides(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    official = [str(i) for i in range(48)]
    matches = [{"source_point_id": str(i), "confidence": 0.9} for i in range(48)]
    db.create_entry({"source_point_id": "0", "title": "真实", "status": "published", "source_kind": "Community", "steps": [{"text": "转", "images": []}]})
    report = build_coverage(db, official, matches)
    assert report["official_points"] == 48
    assert report["point_match_coverage"]["matched"] == 48
    assert report["point_match_coverage"]["unresolved"] == 0
    assert report["real_guide_coverage"]["with_approved_guide"] == 1
    assert report["real_guide_coverage"]["without_guide"] == 47


def test_exact_and_probable_dedup():
    page_a = {"content_sha256": "aaa", "author": "祈鸢ya", "title": "星铁4.2版本，6个浮脂溯源解密攻略", "text": "海原市开局使用Q技能"}
    page_b = {"content_sha256": "aaa", "author": "祈鸢ya", "title": "星铁4.2版本，6个浮脂溯源解密攻略", "text": "海原市开局使用Q技能"}
    page_c = {"content_sha256": "bbb", "author": "祈鸢ya", "title": "星铁4.2版本浮脂溯源解密", "text": "海原市 开局使用Q技能对准旋转机关"}
    reprint = {
        "content_sha256": "ccc",
        "author": "祈鸢ya",
        "title": "崩坏星穹铁道V4.2浮脂溯源解密玩法_3DM手游",
        "text": "大家好呀，这里是祈鸢，本期给大家带来的是，二相乐园—4.2版本海原市。海原市开局使用Q技能",
    }
    assert classify_duplicate(page_a, page_b) == "DUPLICATE_EXACT"
    assert classify_duplicate(page_a, page_c) == "DUPLICATE_PROBABLE"
    assert classify_duplicate(page_a, reprint) == "DUPLICATE_PROBABLE"


def test_coverage_reports_matcher_accuracy_slots(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    report = build_coverage(db, [], [])
    assert set(report["matcher_accuracy"]) >= {"reviewed", "correct", "incorrect", "uncertain"}
    assert report["matcher_accuracy"]["reviewed"] == 0
    assert report["matcher_accuracy"]["correct"] == 0
    assert report["matcher_accuracy"]["incorrect"] == 0
