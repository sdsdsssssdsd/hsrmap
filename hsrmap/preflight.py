from __future__ import annotations

import hashlib
import json
from typing import Any

from hsrmap.http import RateLimitedClient
from hsrmap.schema import SchemaCheck, check_payload
from hsrmap_phase1.client import discover_frontend


def load_registry(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def preflight(registry: dict[str, Any], client: RateLimitedClient | None = None) -> dict[str, Any]:
    client = client or RateLimitedClient()
    frontend = discover_frontend()
    bundle_sha = hashlib.sha256(frontend["bundle_bytes"]).hexdigest()
    result = {
        "ok": True,
        "state": "OK",
        "bundle_url": frontend["bundle_url"],
        "bundle_sha256": bundle_sha,
        "public_host": frontend["public_host"],
        "app_version": frontend["app_version"],
        "bundle_changed": bundle_sha != registry["entry"]["bundle_sha256"],
        "schema": {},
        "registry_candidate": None,
    }
    host = frontend["public_host"]
    base = {
        "app_sn": "sr_map",
        "lang": "zh-cn",
        "app_version": frontend["app_version"],
    }
    probes = {
        "map_tree": ("/v1/map/tree", [{**base, "map_id": 0}, {**base, "map_id": 38}]),
        "label_tree": ("/v1/map/label/tree", [{**base, "map_id": 38}, {**base, "map_id": 842}]),
        "map_info": ("/v1/map/info", [{**base, "map_id": 842}]),
        "point_list": ("/v1/map/point/list", [{**base, "map_id": 842}]),
    }
    for name, (path, param_sets) in probes.items():
        payload = None
        for params in param_sets:
            payload = client.api_get(host, path, params).json()
            if payload.get("retcode") == 0:
                break
        check = check_payload(name, payload, registry.get("schema_fingerprints", {}).get(name))
        result["schema"][name] = {
            "ok": check.ok,
            "level": check.level.value,
            "reason": check.reason,
            "retcode": None if payload is None else payload.get("retcode"),
        }
        if not check.ok:
            result["ok"] = False
            result["state"] = "FAILED_SCHEMA"
    if result["bundle_changed"] and result["ok"]:
        result["registry_candidate"] = {
            **registry,
            "entry": {**registry["entry"], "bundle_url": frontend["bundle_url"], "bundle_sha256": bundle_sha},
            "client": {**registry["client"], "app_version": frontend["app_version"]},
            "hosts": {**registry["hosts"], "public": frontend["public_host"]},
        }
    return result
