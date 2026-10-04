"""证据一等化（a1-8 五）：分类规则、声明表回填、分层统计。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.claims import (
    METHOD_VERSION,
    backfill,
    claim_digest,
    classify_step,
    evidence_layers,
    point_evidence,
    summary,
    transcription_asset,
)


def test_transcription_asset_only_matches_the_marker() -> None:
    sha = "a" * 40
    assert transcription_asset(f"[图解法转录 {sha}] 第1步：右右左右") == sha
    assert transcription_asset("第1步：右右左右") == ""
    assert transcription_asset("[图解法转录 zz] 不是 sha") == ""


def test_official_steps_are_official_evidence() -> None:
    claim = classify_step("完成此处「黄金替罪羊」解谜获得。", official=True)
    assert claim.evidence_level == "OFFICIAL"
    assert claim.grounding_tier == "DERIVED"
    assert claim.claim_kind in {"SOLVE", "LOCATE", "SCOPE"}
    assert claim.method_version == METHOD_VERSION if hasattr(claim, "method_version") else True


def test_transcribed_steps_are_transcription_with_asset() -> None:
    sha = "b" * 40
    claim = classify_step(f"[图解法转录 {sha}] 特殊区域2：面板第一排是右左左左右左。")
    assert (claim.evidence_level, claim.grounding_tier, claim.claim_kind) == ("TRANSCRIPTION", "IMAGE_REF", "SOLVE")
    assert claim.asset_sha256 == sha


def test_community_steps_take_their_grounding_tier() -> None:
    from hsrmap.guides.signature import normalize_text

    sentence = "黄金替罪羊解法：右右左右右右，然后左右，再一直向左"
    #: corpus 是「normalize_text(来源页正文)」——grounding 两边都按同一套规范化比较。
    exact = classify_step(sentence, corpus=normalize_text(sentence))
    assert (exact.evidence_level, exact.grounding_tier, exact.claim_kind) == ("COMMUNITY_TEXT", "EXACT", "SOLVE")

    #: 逐字对不上的正文仍然是社区证据，但落地方式只能记 DERIVED（等审计去判 UNGROUNDED）。
    invented = classify_step("先往南走三步再折返", corpus=sentence)
    assert invented.evidence_level == "COMMUNITY_TEXT" and invented.grounding_tier == "DERIVED"

    located = classify_step("位于此处黑板上。", corpus=sentence)
    assert located.claim_kind == "LOCATE" and located.evidence_level == "COMMUNITY_TEXT"


def test_point_evidence_layers() -> None:
    assert point_evidence(official=True, solve_steps=[]) == {"locate": "OFFICIAL", "solve": None}
    assert point_evidence(official=True, solve_steps=["第1步：点击按钮"])["solve"] == "COMMUNITY_TEXT"
    assert point_evidence(official=True, solve_steps=["[图解法转录 " + "c" * 40 + "] 第1步：拖动"])["solve"] == "TRANSCRIPTION"
    rows = [
        {"done": True, "solve_evidence": None},
        {"done": True, "solve_evidence": "COMMUNITY_TEXT"},
        {"done": True, "solve_evidence": "TRANSCRIPTION"},
        {"done": True, "solve_evidence": "CROSS_INFERENCE"},
        {"done": False, "solve_evidence": None},
    ]
    assert evidence_layers(rows) == {"direct": 2, "transcription": 1, "inference": 1, "missing": 1}


def _seed(tmp_path: Path) -> GuideDatabase:
    db = GuideDatabase.create(tmp_path / "guide.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 'test', 'example.test')")
    (tmp_path / "page.txt").write_text("右右左右右右，然后左右，再一直向左", encoding="utf-8")
    db.conn.execute(
        "INSERT INTO guide_page(source_id, canonical_url, title, raw_text_path) VALUES (1, ?, '来源', ?)",
        ("https://example.test/g", str(tmp_path / "page.txt")),
    )
    entry = db.create_entry({
        "source_point_id": "set:3556-3622:topic:golden_scapegoat",
        "title": "测试条目", "source_url": "https://example.test/g", "source_kind": "Community", "status": "published",
    })
    guide_id = int(entry["id"])
    db.conn.execute("INSERT INTO guide_steps(guide_id, step_index, text) VALUES (?, 0, ?)",
                    (guide_id, "黄金替罪羊解法：右右左右右右，然后左右，再一直向左"))
    db.conn.execute("INSERT INTO guide_steps(guide_id, step_index, text) VALUES (?, 1, ?)",
                    (guide_id, "[图解法转录 " + "d" * 40 + "] 第2步：面板第二排是左右左左"))
    db.conn.commit()
    return db


def test_backfill_dry_run_then_apply_is_idempotent(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    dry = backfill(db, apply=False)
    assert dry["claims"] == 2 and dry["dry_run"] is True
    assert summary(db)["total"] == 0

    applied = backfill(db, apply=True)
    assert applied["claims"] == 2 and applied["written"] == 2
    first_digest = claim_digest(db)
    again = backfill(db, apply=True)
    assert again["claims"] == 2
    assert summary(db)["total"] == 2, "重建必须幂等：不能翻倍"
    assert claim_digest(db) == first_digest
    levels = summary(db)["by_level"]
    assert levels.get("TRANSCRIPTION") == 1 and levels.get("COMMUNITY_TEXT") == 1
    db.close()
