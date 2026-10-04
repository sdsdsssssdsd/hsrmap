"""Phase 6 P6.2：语义探针（只读、打码、不写 user.db）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrmap.progress import probe as probe_mod
from hsrmap.progress.adapter import ProgressApiError, ReadOnlyClient
from hsrmap.progress.cookie import Credential
from hsrmap.progress.realm import realm_for

SECRET = "ltuid_v2=123456; ltoken_v2=" + "S" * 24


def _fake_client(secret: str = SECRET) -> ReadOnlyClient:
    def transport(method, url, params, body, headers):
        if url.endswith("getUserGameRolesByCookie"):
            return 200, json.dumps({"retcode": 0, "data": {"list": [
                {"game_uid": "812345679", "region": "prod_gf_cn", "nickname": "开拓者", "level": 70,
                 "game_biz": "hkrpg_cn"},
            ]}})
        if "treasure_count" in url:
            return 200, json.dumps({"retcode": 0, "data": {
                "user_obtained_count": 17, "map_all_treasure_count": 23, "treasure_label_id": 9}})
        return 200, json.dumps({"retcode": 0, "data": {"point_status": {}}})

    return ReadOnlyClient(realm_for("cn"), credential=Credential(secret), transport=transport,
                          app_version="app-version-test", min_interval=0)


def test_without_a_cookie_the_probe_says_so_and_stops(monkeypatch):
    monkeypatch.delenv("HSRMAP_HOYOLAB_COOKIE", raising=False)
    report = probe_mod.run_probe()
    assert report["ok"] is False and report["reason"] == "no_cookie"
    assert report["credential_present"] is False
    #: 没有 cookie 也要把实验清单给出来：用户可以先做游戏内那半边
    assert len(report["experiments"]) == 4
    assert "user.db" not in json.dumps(report)


def test_full_probe_masks_uid_and_never_carries_the_cookie():
    client = _fake_client()
    report = probe_mod.run_probe(client=client, map_id="38")
    assert report["ok"] is True
    assert report["role"]["uid_masked"] == "*****5679"
    assert report["role"]["nickname_masked"].startswith("开")
    treasure = report["steps"]["treasure"]
    assert treasure["user_obtained_count"] == 17 and treasure["map_all_treasure_count"] == 23
    #: 测试环境没有快照，拿不到 point_id 样本 → attempted=False 是如实回答（不是失败）
    assert report["steps"]["point_status"]["attempted"] in {True, False}
    blob = json.dumps(report, ensure_ascii=False)
    assert SECRET not in blob and "ltoken_v2" not in blob
    assert report["semantics"]["rule"].startswith("Gate 0")
    assert all("cookie" not in json.dumps(item) or item["cookie_sent"] for item in report["exchanges"])


def test_app_version_discovery_failure_fails_closed(monkeypatch):
    """a1-9 §18：app_version 自动发现失败必须 fail closed，不猜一个版本号。"""
    import hsrmap_phase1.client as phase1_client

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(phase1_client, "discover_frontend", boom)
    report = probe_mod.run_probe(credential=Credential(SECRET))
    assert report["ok"] is False and report["reason"] == "app_version_unavailable"
    assert any("fail closed" in note for note in report["notes"])


def test_probe_uses_point_samples_when_available(monkeypatch):
    """有快照时要真的去试 point_status 的请求体（形状 unverified，但必须试）。"""
    monkeypatch.setattr(probe_mod, "sample_point_ids", lambda map_id, limit=5: ["1234", "5678"])
    report = probe_mod.run_probe(client=_fake_client(), map_id="38")
    status = report["steps"]["point_status"]
    assert status["attempted"] is True and status["verified"] is False
    assert "point_ids" in status["request_body_keys"]
    assert status["data_keys"] == ["point_status"]


def test_probe_module_never_writes_a_database():
    source = Path(probe_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("from hsrmap.user_db", "UserDatabase", "INSERT INTO", "UPDATE ", "DELETE FROM", "GuideDatabase"):
        assert forbidden not in source, f"探针不该出现 {forbidden!r}"


def test_api_error_carries_no_credential():
    def transport(method, url, params, body, headers):
        return 200, json.dumps({"retcode": -100, "message": "not logged in"})

    client = ReadOnlyClient(realm_for("cn"), credential=Credential(SECRET), transport=transport,
                            app_version="v", min_interval=0)
    with pytest.raises(ProgressApiError) as excinfo:
        client.require_ok(client.request("get_user_treasure_count", params={"uid": 1}))
    text = str(excinfo.value)
    assert "retcode=-100" in text and SECRET not in text


def test_cli_probe_reports_and_writes_the_json(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HSRMAP_HOYOLAB_COOKIE", raising=False)
    from hsrmap.cli import main

    out = tmp_path / "progress-probe.json"
    code = main(["progress", "probe", "--json", "--out", str(out)])
    printed = capsys.readouterr().out
    payload = json.loads(printed[printed.index("{"):])
    assert code == 1 and payload["reason"] == "no_cookie"
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["reason"] == "no_cookie"
