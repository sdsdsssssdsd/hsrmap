import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.review.service import approve_item, create_item


def _item(db, **kwargs):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/" + kwargs.get("url", "a"), "title": "t"})
    body = {
        "page_id": page["id"],
        "source_point_id": kwargs.get("source_point_id", ""),
        "status": "AUTO_SUGGEST",
        "draft": kwargs.get("draft") or {},
    }
    return create_item(db, body)


def test_approve_empty_id_rejected(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    item = _item(db, source_point_id="")
    with pytest.raises(ValueError, match="source_point_id"):
        approve_item(db, item["id"])


def test_approve_unknown_and_wrong_map_rejected(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    catalog = [
        {"source_point_id": "5171", "map_id": "842", "label": "浮脂溯源"},
        {"source_point_id": "6000", "map_id": "tv", "label": "浮脂溯源"},
    ]
    unknown = _item(db, source_point_id="9999", url="u", draft={"map_id": "842", "semantic_key": "浮脂溯源"})
    with pytest.raises(ValueError, match="unknown"):
        approve_item(db, unknown["id"], official_points=catalog)
    other_map = _item(db, source_point_id="6000", url="v", draft={"map_id": "842", "semantic_key": "浮脂溯源"})
    with pytest.raises(ValueError, match="map"):
        approve_item(db, other_map["id"], official_points=catalog)
    wrong_label = _item(db, source_point_id="5171", url="w", draft={"map_id": "842", "semantic_key": "宝箱"})
    with pytest.raises(ValueError, match="label"):
        approve_item(db, wrong_label["id"], official_points=catalog)
    ok = _item(db, source_point_id="5171", url="ok", draft={"map_id": "842", "semantic_key": "浮脂溯源", "steps": [{"text": "x"}]})
    approved = approve_item(db, ok["id"], official_points=catalog)
    assert approved["status"] == "APPROVED"
