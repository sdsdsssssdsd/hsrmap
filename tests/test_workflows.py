"""正式工作流（a1-8 七）：检索账本写入与图解转录发布。

这两条之前散在 %TEMP% 的一次性脚本里；现在有 schema、有校验、有幂等、有测试。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.workflows import (
    SEARCH_PAYLOAD_VERSION,
    TRANSCRIBE_PAYLOAD_VERSION,
    WorkflowError,
    record_search_payload,
    transcribe_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp_path: Path) -> GuideDatabase:
    return GuideDatabase.create(tmp_path / "guide.db")


def _payload(**over):
    payload = {
        "schema_version": SEARCH_PAYLOAD_VERSION,
        "topic": "golden_scapegoat",
        "target_key": "point:3556",
        "query": "单元测试检索",
        "provider": "unit-test",
        "results": [{"url": "https://example.test/a", "decision": "IRRELEVANT", "reason": "正文只有图"}],
    }
    payload.update(over)
    return payload


def test_record_search_payload_writes_then_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = record_search_payload(db, _payload())
    assert first["skipped"] is False and first["run_id"] and first["result_count"] == 1
    again = record_search_payload(db, _payload())
    assert again["skipped"] is True and again["run_id"] == first["run_id"]
    assert db.conn.execute("SELECT COUNT(*) FROM source_search_run").fetchone()[0] == 1
    db.close()


def test_record_search_payload_new_decision_creates_a_new_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = record_search_payload(db, _payload())
    payload = _payload()
    payload["results"][0]["decision"] = "ACCEPTED"
    second = record_search_payload(db, payload)
    assert second["skipped"] is False and second["run_id"] != first["run_id"]
    assert db.conn.execute("SELECT COUNT(*) FROM source_search_run").fetchone()[0] == 2
    db.close()


def test_record_search_payload_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    out = record_search_payload(db, _payload(), dry_run=True)
    assert out["dry_run"] is True
    assert db.conn.execute("SELECT COUNT(*) FROM source_search_run").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("override", [
    {"schema_version": 99},
    {"topic": ""},
    {"results": []},
    {"results": [{"url": "", "decision": "IRRELEVANT"}]},
    {"results": [{"url": "https://example.test/a", "decision": "MAYBE"}]},
])
def test_record_search_payload_rejects_bad_payloads(tmp_path: Path, override: dict) -> None:
    db = _db(tmp_path)
    with pytest.raises(WorkflowError):
        record_search_payload(db, _payload(**override))
    db.close()


def _page(db: GuideDatabase, url: str = "https://example.test/guide") -> None:
    db.conn.execute("INSERT OR IGNORE INTO guide_source(id, name, domain) VALUES (1, 'test', 'example.test')")
    db.conn.execute(
        "INSERT INTO guide_page(source_id, canonical_url, title) VALUES (1, ?, '测试来源')", (url,)
    )
    db.conn.commit()


def _transcribe_payload(url: str, sha: str) -> dict:
    return {
        "schema_version": TRANSCRIBE_PAYLOAD_VERSION,
        "topic_key": "golden_scapegoat",
        "target_key": "set:3556-3622:topic:golden_scapegoat",
        "map_name": "翁法罗斯 / 特殊房间",
        "page": {"url": url},
        "steps": [{"text": f"[图解法转录 {sha[:8]}] 第1步：面板第一排是右右左右右右。", "assets": [sha]}],
    }


def test_transcribe_requires_an_imported_page(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with pytest.raises(WorkflowError):
        transcribe_payload(db, _transcribe_payload("https://example.test/missing", "a" * 64),
                           assets_root=tmp_path / "assets", dry_run=True)
    db.close()


def test_transcribe_requires_the_asset_on_disk(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _page(db)
    with pytest.raises(WorkflowError):
        transcribe_payload(db, _transcribe_payload("https://example.test/guide", "b" * 64),
                           assets_root=tmp_path / "assets", dry_run=True)
    db.close()


def test_transcribe_dry_run_and_apply_use_the_review_channel(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _page(db)
    sha = "c" * 64
    folder = tmp_path / "assets" / sha[:2]
    folder.mkdir(parents=True)
    (folder / f"{sha}.png").write_bytes(b"png")
    payload = _transcribe_payload("https://example.test/guide", sha)

    dry = transcribe_payload(db, payload, assets_root=tmp_path / "assets", dry_run=True)
    assert dry["dry_run"] is True and dry["steps"] == 1

    created: list[dict] = []
    approved: list[int] = []

    def create_item(_db, body):
        created.append(body)
        return {"id": 7}

    def approve_item(_db, item_id, official_points=None):
        approved.append(int(item_id))
        return {"entry": {"id": 42, "source_point_id": "set:3556-3622:topic:golden_scapegoat", "status": "published"}}

    out = transcribe_payload(db, payload, assets_root=tmp_path / "assets",
                             create_item=create_item, approve_item=approve_item)
    assert out["entry_id"] == 42 and approved == [7]
    draft = created[0]["draft"]
    assert draft["binding_method"] == "IMAGE_TRANSCRIPTION"
    assert draft["member_points"] == ["3556", "3622"]
    assert draft["steps"][0]["images"] == [sha]
    db.close()


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "hsrmap", *argv], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)


def test_cli_search_record_round_trip(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(_payload(query="CLI 测试检索"), ensure_ascii=False), encoding="utf-8")
    runtime = tmp_path / "rt"
    first = _run_cli("--data-dir", str(runtime), "guides", "search-record", "--json", str(payload_path))
    assert first.returncode == 0, first.stderr
    body = json.loads(first.stdout)
    assert body["skipped"] is False and body["run_id"]
    second = _run_cli("--data-dir", str(runtime), "guides", "search-record", "--json", str(payload_path))
    assert json.loads(second.stdout)["skipped"] is True


def test_cli_evidence_transcribe_defaults_to_dry_run(tmp_path: Path) -> None:
    payload_path = tmp_path / "spec.json"
    payload_path.write_text(json.dumps(_transcribe_payload("https://example.test/nope", "d" * 64)), encoding="utf-8")
    runtime = tmp_path / "rt"
    result = _run_cli("--data-dir", str(runtime), "guides", "evidence-transcribe", "--spec", str(payload_path))
    assert result.returncode == 1, (result.returncode, result.stdout[:200])
    assert "载荷不合法" in (result.stdout + result.stderr)
