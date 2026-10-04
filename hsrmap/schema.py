from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from hsrmap_phase1.fingerprint import extract_paths, fingerprint_schema

REQUIRED_PATHS = {
    "map_tree": ("$.retcode", "$.data", "$.data.tree"),
    "label_tree": ("$.retcode", "$.data", "$.data.tree"),
    "map_info": (
        "$.retcode",
        "$.data",
        "$.data.info",
        "$.data.info.id",
        "$.data.info.detail",
    ),
    "point_list": ("$.retcode", "$.data", "$.data.point_list"),
    "point_info": ("$.retcode", "$.data", "$.data.info", "$.data.info.id"),
}


class SchemaCheck(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass
class SchemaResult:
    ok: bool
    level: SchemaCheck
    reason: str | None = None
    missing: tuple[str, ...] = ()
    fingerprint: str | None = None


def _has_path(obj: Any, path: str) -> bool:
    if not path.startswith("$"):
        return False
    cur = obj
    for part in path[1:].split("."):
        if part == "":
            continue
        if part.endswith("[]"):
            part = part[:-2]
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def check_payload(endpoint: str, payload: dict[str, Any], observed_fingerprint: str | None) -> SchemaResult:
    required = REQUIRED_PATHS.get(endpoint, ())
    missing = tuple(path for path in required if not _has_path(payload, path))
    if missing:
        return SchemaResult(ok=False, level=SchemaCheck.FAIL, reason="REQUIRED_FIELD_MISSING", missing=missing)
    if endpoint == "map_info":
        detail = ((payload.get("data") or {}).get("info") or {}).get("detail")
        if not detail:
            return SchemaResult(ok=False, level=SchemaCheck.FAIL, reason="REQUIRED_FIELD_MISSING", missing=("detail",))
    digest = fingerprint_schema(payload)
    if observed_fingerprint and observed_fingerprint != digest:
        return SchemaResult(ok=True, level=SchemaCheck.WARNING, reason="SCHEMA_CHANGED", fingerprint=digest)
    return SchemaResult(ok=True, level=SchemaCheck.OK, fingerprint=digest)
