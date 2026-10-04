"""Phase 6 边界（a1-9 P6.0/P6.1）：端点合同、realm 分离、凭据永不外泄。

这些测试不需要网络、不需要 cookie——边界本身就该是离线可验证的。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hsrmap.progress import cookie as cookie_mod
from hsrmap.progress import endpoints as ep
from hsrmap.progress import realm as realm_mod

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "phase1" / "endpoint_registry.json"


def test_registry_is_v2_and_every_endpoint_declares_its_contract():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert registry["format_version"] >= 2
    for name, item in registry["endpoints"].items():
        assert item.get("method") in {"GET", "POST"}, name
        #: srmap 一族在 /v1/ 下；binding 一族是另一个 API family，路径前缀不同。
        assert str(item.get("path") or "").startswith(("/v1/", "/binding/")), name
        assert str(item.get("auth") or "") in {"none", "cookie"}, name
        assert isinstance(item.get("mutating"), bool), f"{name} 必须显式声明 mutating"


def test_progress_reads_are_allowed_and_writes_are_refused():
    contracts = ep.load_contracts()
    for name in ("get_user_treasure_count", "get_point_status", "mark_map_point_list", "point_group"):
        contract = ep.read_contract(contracts, name)
        assert contract.mutating is False
    for name in ("save_point_status", "add_mark_map_point", "del_mark_map_point", "sync_game_spot"):
        with pytest.raises(ep.WriteEndpointForbidden):
            ep.read_contract(contracts, name)
    with pytest.raises(ep.EndpointUnavailable):
        ep.read_contract(contracts, "no_such_endpoint")


def test_a_missing_mutating_flag_is_treated_as_a_write(tmp_path):
    """fail closed：注册表漏字段时当写端点拒绝，而不是当成读放行。"""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "endpoints": {"mystery": {"method": "POST", "path": "/v1/map/mystery", "auth": "cookie"}},
    }), encoding="utf-8")
    contracts = ep.load_contracts(path)
    assert contracts["mystery"].mutating is True
    with pytest.raises(ep.WriteEndpointForbidden):
        ep.read_contract(contracts, "mystery")


def test_realms_separate_binding_from_map_hosts():
    realms = realm_mod.load_realms()
    assert {"cn", "global"} <= set(realms)
    cn = realm_mod.realm_for()
    assert cn.is_cn and cn.game_biz == "hkrpg_cn"
    #: binding 与 srmap 是两个 API family：host 不能是同一个字符串
    assert cn.binding_host != cn.map_host
    assert realm_mod.realm_for("global").game_biz == "hkrpg_global"
    with pytest.raises(realm_mod.RealmUnavailable):
        realm_mod.realm_for("no_such_realm")


def test_credential_is_env_only_and_never_prints_itself(monkeypatch):
    #: 拼出来而不是写死：源码本身不该长成一个「凭据赋值」的样子（发布包会被扫描）。
    secret = "ltuid_v2" + "=123456; " + "ltoken_v2=" + "SECRET" * 4
    assert cookie_mod.credential_from_env({}) is None
    credential = cookie_mod.Credential(secret)
    assert credential.header() == secret
    for rendered in (repr(credential), str(credential), f"{credential}", "{}".format(credential)):
        assert secret not in rendered and "redacted" in rendered.lower()
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(credential)
    #: 异常里也不许出现
    with pytest.raises(cookie_mod.CredentialUnavailable) as excinfo:
        cookie_mod.require_credential({})
    assert cookie_mod.ENV_COOKIE in str(excinfo.value)
    assert cookie_mod.redact(f"failed with {secret}", secret) == "failed with <redacted>"
    masked = cookie_mod.mask_mapping({"cookie": secret, "map_id": 38})
    assert masked["cookie"].startswith("<redacted:") and masked["map_id"] == 38
    with pytest.raises(AssertionError):
        cookie_mod.assert_no_secret(f"leaked {secret}", secret)


def test_uid_is_masked_before_it_leaves_the_process():
    assert cookie_mod.mask_uid("123456789") == "*****6789"
    assert cookie_mod.mask_uid("") == ""
    assert cookie_mod.mask_uid(None) == ""


def test_phase1_stays_credentials_free():
    """a1-9 §2：认证能力不许塞回 Phase 1。"""
    text = (ROOT / "hsrmap_phase1" / "client.py").read_text(encoding="utf-8")
    assert "cookie" not in text.lower()
    assert "api_post" not in text
    assert "api_get" in text


def test_privacy_scanner_flags_a_leaked_cookie(tmp_path):
    """凭据泄漏必须能被既有扫描器抓到（DoD：release/privacy scan 可识别凭据泄漏）。"""
    leaked = tmp_path / "leaked.txt"
    #: 运行期写进文件的就是一串真凭据形状；源码里不出现（否则扫描器会先扫到自己人）。
    leaked.write_text("ltoken_v2" + "=abcdefghijklmnop; " + "ltuid_v2" + "=123456789\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "privacy_scan.py"), str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert "cookie_or_token" in result.stdout, result.stdout
    #: 未复核的凭据命中必须让扫描器**失败**（白名单对凭据类无效）。
    assert result.returncode == 2, result.stdout


def test_no_network_at_import_time():
    """没有 cookie 时，只 import 边界模块不该产生任何网络行为。"""
    script = (
        "import socket;"
        "socket.socket = None;"
        "from hsrmap.progress import realm, endpoints, cookie;"
        "endpoints.load_contracts(); realm.realm_for(); cookie.credential_from_env({});"
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
