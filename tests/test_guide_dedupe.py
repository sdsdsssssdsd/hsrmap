"""One guide per target: duplicates are superseded, coverage does not move."""

import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.dedupe import dedupe_entries, duplicate_targets
from hsrmap.guides.ledger import published_point_ids


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _entry(db, target, steps, title="攻略", assets=0):
    entry = db.create_entry({
        "source_point_id": target,
        "title": title,
        "status": "published",
        "source_url": "https://t.test/page",
        "steps": [{"text": text, "images": []} for text in steps],
    })
    for index in range(assets):
        db.conn.execute(
            "INSERT INTO guide_assets(guide_id, step_index, asset_sha256) VALUES (?, 0, ?)",
            (int(entry["id"]), f"sha{index}"),
        )
    db.conn.commit()
    return int(entry["id"])


def test_the_thickest_guide_per_target_stays(tmp_path):
    db = _db(tmp_path)
    thin = _entry(db, "4139", ["第一步", "第二步"], title="薄的")
    thick = _entry(db, "4139", ["第一步", "第二步", "第三步", "第四步"], title="厚的")
    other = _entry(db, "4142", ["只有一个目标"], title="别的目标")

    report = dedupe_entries(db, apply=True)
    assert report["targets_with_duplicates"] == 1
    assert report["superseded"] == 1
    statuses = {
        int(row["id"]): row["status"]
        for row in db.conn.execute("SELECT id, status FROM guide_entry")
    }
    assert statuses[thick] == "published"
    assert statuses[thin] == "superseded"
    assert statuses[other] == "published"
    db.close()


def test_a_dry_run_reports_without_touching_anything(tmp_path):
    db = _db(tmp_path)
    _entry(db, "4139", ["第一步"])
    _entry(db, "4139", ["第一步", "第二步"])

    dry = dedupe_entries(db)
    assert dry["applied"] is False and dry["superseded"] == 1
    assert db.conn.execute(
        "SELECT COUNT(*) FROM guide_entry WHERE status = 'published'"
    ).fetchone()[0] == 2
    db.close()


def test_coverage_is_per_target_and_does_not_move(tmp_path):
    db = _db(tmp_path)
    _entry(db, "set:4139-4142:topic:x", ["第一步", "第二步"])
    _entry(db, "set:4139-4142:topic:x", ["第一步"])
    before = published_point_ids(db)
    dedupe_entries(db, apply=True)
    assert published_point_ids(db) == before == {"4139", "4142"}
    db.close()


def test_targets_with_one_guide_are_left_alone(tmp_path):
    db = _db(tmp_path)
    _entry(db, "4139", ["第一步"])
    _entry(db, "4142", ["第一步"])
    assert duplicate_targets(db) == []
    assert dedupe_entries(db, apply=True)["superseded"] == 0
    db.close()

def test_a_guide_with_a_solution_beats_a_thicker_timestamp_shell(tmp_path):
    """44 步全是视频时间戳的空壳，不该压过 4 步写着「影子出现前：右、左…」的攻略。"""
    db = _db(tmp_path)
    shell = _entry(
        db,
        "set:4139-4142:topic:x",
        ["t%d-t%d:%d.0" % (index, index - 1, index) for index in range(1, 7)],
        title="时间戳空壳",
    )
    real = _entry(
        db,
        "set:4139-4142:topic:x",
        ["影子出现前：右、左、左、左。", "影子出现后：上、右、左、右。"],
        title="真攻略",
    )
    report = dedupe_entries(db, apply=True)
    statuses = {
        int(row["id"]): row["status"]
        for row in db.conn.execute("SELECT id, status FROM guide_entry")
    }
    assert statuses[real] == "published"
    assert statuses[shell] == "superseded"
    #: 空壳被时间戳那一关撤掉（它自己也有竞争者），重复判定因此只剩真攻略
    assert report["timestamp_entries"]["retired"] == 1
    db.close()


def test_a_published_entry_whose_steps_are_all_timestamps_is_retired(tmp_path):
    db = _db(tmp_path)
    shell = _entry(
        db,
        "set:2842-2843:topic:origami_bird",
        ["t%d-t%d:%d.0" % (index, index - 1, index) for index in range(1, 6)],
        title="章节时间戳",
    )
    real = _entry(db, "set:2842-2843:topic:origami_bird", ["位于此处窗户上，走进调查。"], title="官方点位")
    sole = _entry(
        db,
        "set:9999:topic:origami_bird",
        ["t%d-t%d:%d.0" % (index, index - 1, index) for index in range(1, 6)],
        title="只有它一条时不撤",
    )

    report = dedupe_entries(db, apply=True)
    assert report["timestamp_entries"]["retired"] == 1
    assert report["timestamp_entries"]["entries"][0]["entry_id"] == shell
    statuses = {
        int(row["id"]): row["status"]
        for row in db.conn.execute("SELECT id, status FROM guide_entry")
    }
    assert statuses[shell] == "superseded"
    assert statuses[real] == "published"
    #: 唯一一条的目标即使全是时间戳也先留着（撤了就是覆盖率回退）
    assert statuses[sole] == "published"
    db.close()