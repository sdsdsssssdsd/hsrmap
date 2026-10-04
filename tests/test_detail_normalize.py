"""Empty official detail is COMPLETE_EMPTY; known images and unknown URLs stay separate."""

import json
from pathlib import Path

from hsrmap.detail_normalize import classify_retcode, normalize_point_info

ROOT = Path(__file__).resolve().parents[1]
EMPTY = json.loads((ROOT / "phase1" / "samples" / "point_info" / "5260.json").read_text(encoding="utf-8"))
BOTH = json.loads((ROOT / "phase1" / "samples" / "point_info" / "5171.json").read_text(encoding="utf-8"))
NOTES = json.loads((ROOT / "phase1" / "samples" / "point_info" / "5146.json").read_text(encoding="utf-8"))


def test_5260_is_complete_empty_not_failed():
    parsed = normalize_point_info(EMPTY)
    assert parsed["job_state"] == "COMPLETE_EMPTY"
    assert parsed["detail_state"] == "EMPTY"
    assert parsed["is_empty"] is True
    assert parsed["source_point_id"] == "5260"
    assert parsed["plain_text"] == ""
    assert parsed["images"] == []


def test_5171_has_description_and_official_image():
    parsed = normalize_point_info(BOTH)
    assert parsed["job_state"] == "COMPLETE"
    assert parsed["detail_state"] == "NONEMPTY"
    assert "浮脂溯源" in parsed["plain_text"]
    assert parsed["content_raw"] == BOTH["data"]["info"]["content"]
    assert parsed["content_format"] in {"plain", "html"}
    assert len(parsed["images"]) == 1
    assert parsed["images"][0]["role"] == "image"
    assert parsed["images"][0]["remote_url"].startswith("https://")


def test_5146_notes_has_description_and_image():
    parsed = normalize_point_info(NOTES)
    assert parsed["is_empty"] is False
    assert "浮脂记事" in parsed["plain_text"]
    assert parsed["images"]


def test_unknown_image_url_is_unclassified_candidate():
    payload = json.loads(json.dumps(BOTH))
    payload["data"]["info"]["mystery_banner"] = "https://example.test/extra.webp"
    parsed = normalize_point_info(payload)
    urls = {item["remote_url"] for item in parsed["unclassified_asset_candidates"]}
    assert "https://example.test/extra.webp" in urls
    known = {item["remote_url"] for item in parsed["images"]}
    assert "https://example.test/extra.webp" not in known


def test_source_not_found_is_not_empty():
    parsed = normalize_point_info({"retcode": -1, "message": "point not exist", "data": None})
    assert parsed["job_state"] == "SOURCE_NOT_FOUND"
    assert parsed["detail_state"] == "SOURCE_NOT_FOUND"
    assert parsed["is_empty"] is False


def test_not_found_retcode_classifier():
    assert classify_retcode(-1, "point not exist") == "SOURCE_NOT_FOUND"
    assert classify_retcode(0, "OK") == "OK"
