from hsrmap.guides.inventory.classifier import classify_label


def test_grease_is_puzzle_topic():
    row = classify_label({"source_id": "686", "name": "浮脂溯源·二次元ROTATE！", "category": "解密战利品", "point_count": 48})
    assert row["status"] == "GUIDE_TOPIC"
    assert row["suggested_kind"] == "PUZZLE"
    assert row["guide_needed"] is True


def test_anchor_needs_no_guide():
    row = classify_label({"source_id": "1", "name": "界域定锚", "category": "传送", "point_count": 200})
    assert row["status"] == "NO_GUIDE_REQUIRED"
    assert row["guide_needed"] is False


def test_ordinary_chest_is_location_only():
    row = classify_label({"source_id": "2", "name": "战利品", "category": "战利品", "point_count": 80})
    assert row["status"] == "LOCATION_ONLY"


def test_generic_chests_are_not_puzzle_topics():
    for name in ("普通战利品", "丰厚战利品", "贵重战利品"):
        row = classify_label({"source_id": "x", "name": name, "category": "解密战利品", "point_count": 80})
        assert row["status"] == "LOCATION_ONLY"
        assert row["guide_needed"] is False


def test_dream_ticker_is_puzzle_topic():
    row = classify_label({"source_id": "3", "name": "梦境迷钟", "category": "解密战利品", "point_count": 31})
    assert row["status"] == "GUIDE_TOPIC"
    assert row["suggested_kind"] == "PUZZLE"


def test_origami_bird_is_collectible_topic():
    row = classify_label({"source_id": "4", "name": "折纸小鸟", "category": "收集品", "point_count": 120})
    assert row["status"] == "GUIDE_TOPIC"
    assert row["suggested_kind"] == "COLLECTIBLE"


def test_unknown_label_needs_review():
    row = classify_label({"source_id": "9", "name": "奇怪的装置甲", "category": "其他", "point_count": 3})
    assert row["status"] == "NEEDS_REVIEW"


def test_golden_scapegoat_and_specials_are_guide_topics():
    goat = classify_label({"source_id": "679", "name": "黄金替罪羊", "category": "解密战利品", "point_count": 72})
    assert goat["status"] == "GUIDE_TOPIC"
    assert goat["suggested_kind"] == "PUZZLE"
    bucket = classify_label({"source_id": "461", "name": "王下一桶", "category": "战利品", "point_count": 1})
    assert bucket["status"] == "GUIDE_TOPIC"
    assert bucket["suggested_kind"] == "CHALLENGE"
    orb = classify_label({"source_id": "681", "name": "奇迹宝珠", "category": "地标", "point_count": 9})
    assert orb["status"] == "GUIDE_TOPIC"
    assert orb["suggested_kind"] == "COLLECTIBLE"
