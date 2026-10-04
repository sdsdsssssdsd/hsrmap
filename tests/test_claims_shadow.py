"""S5 的 shadow 约束：证据等级是**注解**，六状态必须与 S0 基线逐点一致。

需要真实语料，默认跳过；用 `pytest --run-data-e2e tests/test_claims_shadow.py` 跑。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.claims import summary
from hsrmap.guides.stages import completeness_report
from hsrmap.paths import GUIDE_DB

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "runbooks" / "baseline-hygiene.json"

pytestmark = pytest.mark.data


def _require_data() -> None:
    if not Path(GUIDE_DB).is_file() or not BASELINE.is_file():
        pytest.skip("SKIPPED: snapshot fixture unavailable")


@pytest.mark.parametrize("lookup", ["string", "relation", "auto"])
def test_status_matrix_matches_the_s0_baseline(lookup: str) -> None:
    """两条查法（字符串键 / 正规关系表）都必须逐点等于 S0 基线（a1-8 十二）。"""
    _require_data()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    db = GuideDatabase.open_readonly(GUIDE_DB)
    report = completeness_report(db, lookup=lookup)
    matrix = sorted(f'{row["topic"]}:{row["point"]}:{row["status"]}' for row in report["rows"])
    digest = hashlib.sha256("\n".join(matrix).encode("utf-8")).hexdigest()
    db.close()
    assert len(matrix) == baseline["status_matrix_size"]
    assert digest == baseline["status_matrix_sha256"], "六状态变了：证据等级只能是注解，不能改判定"


def test_the_two_lookups_agree_point_by_point() -> None:
    """切换前必须证明两条路完全一致，而不是「看起来差不多」。"""
    _require_data()
    from hsrmap.guides.targets import relations_in_sync

    db = GuideDatabase.open_readonly(GUIDE_DB)
    state = relations_in_sync(db)
    by_string = {row["point"]: row for row in completeness_report(db, lookup="string")["rows"]}
    by_relation = {row["point"]: row for row in completeness_report(db, lookup="relation")["rows"]}
    db.close()
    assert state["in_sync"] is True, f"关系表没补齐就不许切：{state}"
    assert by_string.keys() == by_relation.keys()
    difference = [
        point for point in by_string if by_string[point]["guide_id"] != by_relation[point]["guide_id"]
    ]
    assert difference == [], f"两条路挑出了不同的攻略：{difference[:5]}"


def test_evidence_layers_cover_every_point() -> None:
    _require_data()
    db = GuideDatabase.open_readonly(GUIDE_DB)
    report = completeness_report(db)
    db.close()
    layers = report["evidence_layers"]
    assert sum(layers.values()) == report["points"]
    assert layers["missing"] == 0
    #: 1006/1006 不该掩盖转录：至少要有转录辅助的点位被单列出来。
    assert layers["transcription"] > 0


def test_claims_table_is_populated_for_published_entries() -> None:
    _require_data()
    db = GuideDatabase.open_readonly(GUIDE_DB)
    claims = summary(db)
    published = db.conn.execute("SELECT COUNT(*) AS c FROM guide_entry WHERE status = 'published'").fetchone()
    db.close()
    assert int(published["c"]) > 0
    assert claims["total"] > 0
    assert set(claims["by_level"]) <= {"OFFICIAL", "COMMUNITY_TEXT", "TRANSCRIPTION", "CROSS_INFERENCE"}
