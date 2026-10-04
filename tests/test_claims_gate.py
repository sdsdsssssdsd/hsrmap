"""Evidence 进入 publish gate（a1-8 六）。

规格里的六条规则各有一个测试：转录要能指到图、交叉推断要留下依据、必需声明要有来源、
声明必须和当前语料一致、等级退化要 REVIEW/WAIVER、evidence digest 要进 snapshot-manifest。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides import claims as claims_mod
from hsrmap.guides.claims import (
    CLAIM_PROBLEMS,
    METHOD_VERSION,
    backfill,
    evidence_gate,
    summary,
)
from hsrmap.guides.publishing.diff import (
    evaluate_gate,
    manifest_digest,
    snapshot_diff,
    snapshot_manifest,
)
from hsrmap.guides.publishing.sync import sync_published

TOPIC = "test_topic"
SOLVE_TEXT = "黄金替罪羊解法：右右左右右右，然后左右，再一直向左"
LOCATE_TEXT = "位于此处黑板上，就在房间角落。"
SHA = "a" * 64
TRANS_TEXT = f"[图解法转录 {SHA}] 特殊区域2：面板第一排是右左左左右左。"


def _points(count=3):
    return [{"source_point_id": str(i), "map_id": "10", "label": TOPIC} for i in range(1, count + 1)]


def _db(tmp_path: Path, name: str = "guide.db", corpus: str = "") -> GuideDatabase:
    db = GuideDatabase.create(tmp_path / name)
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 'test', 'example.test')")
    path = tmp_path / f"page-{name}.txt"
    path.write_text(corpus or f"{SOLVE_TEXT} {LOCATE_TEXT}", encoding="utf-8")
    db.conn.execute(
        "INSERT INTO guide_page(source_id, canonical_url, title, raw_text_path) VALUES (1, ?, '来源', ?)",
        ("https://example.test/g", str(path)),
    )
    db.conn.commit()
    return db


def _entry(db: GuideDatabase, steps, *, pid: str = "1", images=(), status: str = "published"):
    return db.create_entry({
        "source_point_id": pid,
        "title": "攻略",
        "status": status,
        "source_url": "https://example.test/g",
        "steps": [
            {"text": text, "images": list(images) if index == 0 else []}
            for index, text in enumerate(steps)
        ],
    })


def _asset_file(root: Path, sha: str = SHA) -> Path:
    folder = root / sha[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.png"
    path.write_bytes(b"png")
    return path


def _claim(db: GuideDatabase, guide_id: int, step_id: int, **fields):
    row = {
        "guide_id": guide_id,
        "step_id": step_id,
        "target_id": None,
        "claim_kind": "SOLVE",
        "evidence_level": "COMMUNITY_TEXT",
        "grounding_tier": "EXACT",
        "source_page_id": 1,
        "official_point_id": "1",
        "asset_sha256": None,
        "basis_json": None,
        "method_version": METHOD_VERSION,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(fields)
    db.conn.execute(
        "INSERT INTO guide_claim_evidence(guide_id, step_id, target_id, claim_kind, evidence_level,"
        " grounding_tier, source_page_id, official_point_id, asset_sha256, basis_json,"
        " method_version, created_at)"
        " VALUES (:guide_id, :step_id, :target_id, :claim_kind, :evidence_level, :grounding_tier,"
        " :source_page_id, :official_point_id, :asset_sha256, :basis_json, :method_version, :created_at)",
        row,
    )
    db.conn.commit()


def _problems(report) -> list[str]:
    return [item["problem"] for item in report["findings"]]


def _diff(working, published, tmp_path, **kwargs):
    kwargs.setdefault("assets_root", tmp_path / "assets")
    kwargs.setdefault("official_points", {TOPIC: _points()})
    kwargs.setdefault("topics", [TOPIC])
    return snapshot_diff(working, published, **kwargs)


def test_vocabulary_is_the_spec_vocabulary() -> None:
    assert set(CLAIM_PROBLEMS) >= {
        "TRANSCRIPTION_ASSET_MISSING",
        "TRANSCRIPTION_ASSET_NOT_ATTACHED",
        "INFERENCE_WITHOUT_BASIS",
        "CLAIM_WITHOUT_PROVENANCE",
        "STEP_WITHOUT_CLAIM",
    }


def test_transcription_with_its_own_picture_passes(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [TRANS_TEXT], images=[SHA])
    applied = backfill(db, apply=True)
    assert applied["by_level"] == {"TRANSCRIPTION": 1}
    _asset_file(tmp_path / "assets")
    report = evidence_gate(db, guide_ids=[int(entry["id"])], assets_root=tmp_path / "assets")
    assert _problems(report) == []
    assert report["adopted"] is True and report["claims"] == 1
    db.close()


def test_transcription_whose_picture_is_not_attached_is_a_hard_fail(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [TRANS_TEXT])
    backfill(db, apply=True)
    report = evidence_gate(db, guide_ids=[int(entry["id"])], assets_root=tmp_path / "assets")
    assert "TRANSCRIPTION_ASSET_NOT_ATTACHED" in _problems(report)
    db.close()


def test_transcription_picture_must_exist_on_disk(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [TRANS_TEXT], images=[SHA])
    backfill(db, apply=True)
    _asset_file(tmp_path / "assets")
    clean = evidence_gate(db, guide_ids=[int(entry["id"])], assets_root=tmp_path / "assets")
    assert _problems(clean) == []
    assert clean["assets_checked"] is True
    # 图上没有文件 = 转录无从复核
    missing = evidence_gate(db, guide_ids=[int(entry["id"])], assets_root=tmp_path / "elsewhere")
    assert "TRANSCRIPTION_ASSET_MISSING" in _problems(missing)
    db.close()


def test_transcription_without_declared_asset_is_a_hard_fail(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT])
    step = db.conn.execute(
        "SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)
    ).fetchone()
    _claim(db, int(entry["id"]), int(step["id"]), evidence_level="TRANSCRIPTION",
           grounding_tier="IMAGE_REF", asset_sha256=None)
    report = evidence_gate(db, guide_ids=[int(entry["id"])], staleness=False)
    assert _problems(report) == ["TRANSCRIPTION_ASSET_MISSING"]
    db.close()


def test_cross_inference_must_leave_its_basis(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT])
    step = db.conn.execute("SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)).fetchone()
    _claim(db, int(entry["id"]), int(step["id"]), evidence_level="CROSS_INFERENCE", basis_json=None)
    #: 手写的声明本来就不等于「按当前语料重算」，这里只看规则本身，关掉 staleness。
    report = evidence_gate(db, guide_ids=[int(entry["id"])], staleness=False)
    assert _problems(report) == ["INFERENCE_WITHOUT_BASIS"]

    db.conn.execute("UPDATE guide_claim_evidence SET basis_json = ? WHERE guide_id = ?",
                    (json.dumps({"refs": ["official:408", "page:1"]}, ensure_ascii=False), int(entry["id"])))
    db.conn.commit()
    again = evidence_gate(db, guide_ids=[int(entry["id"])], staleness=False)
    assert _problems(again) == []
    db.close()


def test_claim_without_any_provenance_is_a_hard_fail(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT])
    step = db.conn.execute("SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)).fetchone()
    _claim(db, int(entry["id"]), int(step["id"]), source_page_id=None, official_point_id=None)
    report = evidence_gate(db, guide_ids=[int(entry["id"])])
    assert _problems(report) == ["CLAIM_WITHOUT_PROVENANCE"]
    db.close()


def test_every_step_must_be_declared_once_the_guide_has_claims(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT, LOCATE_TEXT])
    backfill(db, apply=True)
    assert _problems(evidence_gate(db, guide_ids=[int(entry["id"])])) == []

    second = db.conn.execute(
        "SELECT id FROM guide_steps WHERE guide_id = ? ORDER BY step_index DESC LIMIT 1", (int(entry["id"]),)
    ).fetchone()
    db.conn.execute("DELETE FROM guide_claim_evidence WHERE step_id = ?", (int(second["id"]),))
    db.conn.commit()
    report = evidence_gate(db, guide_ids=[int(entry["id"])])
    assert _problems(report) == ["STEP_WITHOUT_CLAIM"]
    db.close()


def test_a_library_that_never_declared_evidence_is_reported_not_blocked(tmp_path):
    db = _db(tmp_path)
    _entry(db, [SOLVE_TEXT])
    report = evidence_gate(db)
    assert report["adopted"] is False and report["claims"] == 0
    assert _problems(report) == []
    db.close()


def test_claims_that_do_not_match_the_text_are_stale(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT])
    backfill(db, apply=True)
    assert _problems(evidence_gate(db, guide_ids=[int(entry["id"])])) == []

    step = db.conn.execute("SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)).fetchone()
    db.conn.execute("UPDATE guide_steps SET text = ? WHERE id = ?", (LOCATE_TEXT, int(step["id"])))
    db.conn.commit()
    report = evidence_gate(db, guide_ids=[int(entry["id"])])
    assert _problems(report) == ["STALE_CLAIM"]
    assert "SOLVE" in report["findings"][0]["detail"]
    db.close()


def test_stale_claim_blocks_the_publish_gate(tmp_path):
    working = _db(tmp_path, "working.db")
    published = _db(tmp_path, "published.db")
    entry = _entry(working, [SOLVE_TEXT])
    backfill(working, apply=True)
    step = working.conn.execute("SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)).fetchone()
    working.conn.execute("UPDATE guide_steps SET text = ? WHERE id = ?", (LOCATE_TEXT, int(step["id"])))
    working.conn.commit()
    report = _diff(working, published, tmp_path)
    assert any("stale_claim" in reason for reason in report["hard_errors"])
    assert report["gate"]["result"] == "FAIL"
    working.close()
    published.close()


def test_downgrade_from_text_to_transcription_is_review_required(tmp_path):
    working = _db(tmp_path, "working.db")
    published = _db(tmp_path, "published.db")
    entry = _entry(working, [SOLVE_TEXT])
    backfill(working, apply=True)
    sync_published(working, published)
    assert summary(published)["by_level"] == {"COMMUNITY_TEXT": 1}

    _asset_file(tmp_path / "assets")
    step = working.conn.execute("SELECT id FROM guide_steps WHERE guide_id = ?", (int(entry["id"]),)).fetchone()
    working.conn.execute("UPDATE guide_steps SET text = ? WHERE id = ?", (TRANS_TEXT, int(step["id"])))
    working.conn.execute("INSERT INTO guide_assets(guide_id, step_index, asset_sha256) VALUES (?, 0, ?)",
                         (int(entry["id"]), SHA))
    working.conn.commit()
    backfill(working, apply=True)

    report = _diff(working, published, tmp_path)
    assert report["evidence"]["downgrades"] == [{
        "guide_id": int(entry["id"]),
        "step_id": int(step["id"]),
        "claim_kind": "SOLVE",
        "from": "COMMUNITY_TEXT",
        "to": "TRANSCRIPTION",
        "direct_to_indirect": True,
    }]
    kinds = [change["type"] for change in report["changes"]]
    assert "EVIDENCE_DOWNGRADED" in kinds
    assert report["gate"]["result"] == "REVIEW"
    assert report["hard_errors"] == []

    # 明确写进 allowed_regressions.yaml 的退化才算被豁免
    waived = evaluate_gate(report, waivers=[{"target": "1", "topic": TOPIC, "reason": "来源页改版，只有图了"}])
    assert waived["result"] == "REVIEW" and waived["waived"]
    resolved = evaluate_gate(
        report, waivers=[{"target": "1", "topic": TOPIC, "reason": "来源页改版，只有图了"}]
    )
    assert [item["type"] for item in resolved["waived"]] == ["EVIDENCE_DOWNGRADED"]
    assert "EVIDENCE_DOWNGRADED" not in {item["type"] for item in resolved["review_required"]}
    assert resolved["result"] == "REVIEW"
    working.close()
    published.close()


def test_evidence_removed_for_a_guide_that_had_it_is_a_hard_fail(tmp_path):
    working = _db(tmp_path, "working.db")
    published = _db(tmp_path, "published.db")
    _entry(working, [SOLVE_TEXT])
    backfill(working, apply=True)
    sync_published(working, published)

    working.conn.execute("DELETE FROM guide_claim_evidence")
    working.conn.commit()
    report = _diff(working, published, tmp_path)
    assert [item["problem"] for item in report["evidence"]["losses"]] == ["EVIDENCE_REMOVED"]
    assert any("evidence removed" in reason for reason in report["hard_errors"])
    working.close()
    published.close()


def test_claims_travel_with_the_published_snapshot(tmp_path):
    working = _db(tmp_path, "working.db")
    published = _db(tmp_path, "published.db")
    _entry(working, [SOLVE_TEXT, TRANS_TEXT], images=[SHA])
    backfill(working, apply=True)
    copied = sync_published(working, published)
    assert copied == 1
    assert summary(published)["total"] == summary(working)["total"] == 2
    assert claims_mod.claim_digest(published) == claims_mod.claim_digest(working)

    # 再同步一次不能翻倍（声明跟着条目重建）
    sync_published(working, published)
    assert summary(published)["total"] == 2
    working.close()
    published.close()


def test_evidence_digest_lands_in_the_manifest_and_moves_with_the_level(tmp_path):
    db = _db(tmp_path)
    entry = _entry(db, [SOLVE_TEXT])
    backfill(db, apply=True)
    first = snapshot_manifest(db, official_points={TOPIC: _points()}, topics=[TOPIC])
    assert first["evidence"]["digest"]
    assert str(entry["id"]) in first["evidence"]["by_guide"]
    assert first["evidence"]["summary"]["by_level"] == {"COMMUNITY_TEXT": 1}
    assert first["evidence"]["by_guide"] == {"1": claims_mod.claim_digest(db)}

    # 步骤文字一个字没变，只有等级退化了
    db.conn.execute(
        "UPDATE guide_claim_evidence SET evidence_level = 'CROSS_INFERENCE', basis_json = ?",
        (json.dumps({"refs": ["official:408"]}),),
    )
    db.conn.commit()
    second = snapshot_manifest(db, official_points={TOPIC: _points()}, topics=[TOPIC])
    assert second["entries"]["1"]["content_sha256"] == first["entries"]["1"]["content_sha256"]
    assert second["evidence"]["digest"] != first["evidence"]["digest"]
    assert second["evidence"]["by_guide"] != first["evidence"]["by_guide"]
    assert manifest_digest(second) != manifest_digest(first)
    db.close()


def test_manifest_digest_of_the_body_changes_only_with_evidence(tmp_path):
    db = _db(tmp_path)
    _entry(db, [SOLVE_TEXT])
    backfill(db, apply=True)
    from hsrmap.guides.publishing.diff import _manifest_body, entry_snapshot

    entries = entry_snapshot(db)
    without = manifest_digest(_manifest_body(entries, {}))
    with_evidence = manifest_digest(_manifest_body(entries, {}, claims_mod.guide_digests(db)))
    assert without != with_evidence
    db.close()


def test_cli_publish_snapshot_blocks_on_an_evidence_gap(tmp_path):
    working_path = tmp_path / "working.db"
    published_path = tmp_path / "published.db"
    working = _db(tmp_path, "working.db")
    GuideDatabase.create(published_path).close()
    _entry(working, [TRANS_TEXT])
    backfill(working, apply=True)
    working.close()
    code = main([
        "guides", "publish-snapshot",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--no-core-check",
        "--dry-run",
        "--report", str(tmp_path / "reports" / "diff.json"),
        "--waivers", str(tmp_path / "none.yaml"),
    ])
    assert code == 2
    report = json.loads((tmp_path / "reports" / "diff.json").read_text(encoding="utf-8"))
    assert any("transcription_asset_not_attached" in reason for reason in report["gate"]["hard_fail"])
