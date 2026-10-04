"""Golden A/B/C/D are test semantics, not document words (a1-6 §31)."""

from pathlib import Path

from hsrmap.guides.closure import golden_status
from hsrmap.guides.golden import GOLDEN_LEVELS, golden_level_ids, level_status, levels_ok

ROOT = Path(__file__).resolve().parents[1]


def test_the_four_levels_name_their_pipeline_stage():
    assert golden_level_ids() == ("A", "B", "C", "D")
    pipeline = {level.id: level.pipeline for level in GOLDEN_LEVELS}
    assert pipeline == {
        "A": "HTML -> blocks",
        "B": "blocks -> target",
        "C": "approved -> published",
        "D": "published -> viewer",
    }
    assert {level.name for level in GOLDEN_LEVELS} == {"extraction", "matching", "publish", "lookup"}
    assert all(level.meaning for level in GOLDEN_LEVELS)


def test_every_level_is_backed_by_real_tests_and_fixtures():
    assert levels_ok() is True
    for level in level_status():
        assert level["ok"] is True, level["level"]
        assert level["test_count"] > 0
        assert all(item["exists"] and item["tests"] > 0 for item in level["tests"])
        assert all(item["exists"] for item in level["fixtures"])
    extraction = next(item for item in level_status() if item["level"] == "A")
    assert len(extraction["fixtures"]) >= 4  # the four real parsed pages


def test_a_missing_test_module_makes_a_level_fail(tmp_path):
    rows = level_status(root=tmp_path)
    assert rows and all(row["ok"] is False for row in rows)
    assert levels_ok(root=tmp_path) is False


def test_closure_reports_the_levels_alongside_topic_goldens():
    status = golden_status()
    assert status["levels_ok"] is True
    assert status["levels_label"] == "A/B/C/D"
    assert status["ok"] is True
    assert [row["level"] for row in status["levels"]] == ["A", "B", "C", "D"]
    assert status["registered"] == status["enabled_topics"]
