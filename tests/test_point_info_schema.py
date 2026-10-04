"""point/info required schema is identity only; empty content is valid."""

import json
from pathlib import Path

from hsrmap.schema import SchemaCheck, check_payload

ROOT = Path(__file__).resolve().parents[1]
EMPTY = json.loads((ROOT / "phase1" / "samples" / "point_info" / "5260.json").read_text(encoding="utf-8"))
NONEMPTY = json.loads((ROOT / "phase1" / "samples" / "point_info" / "5171.json").read_text(encoding="utf-8"))


def test_empty_point_5260_passes_required_schema():
    result = check_payload("point_info", EMPTY, observed_fingerprint=None)
    assert result.ok is True
    assert result.level == SchemaCheck.OK


def test_nonempty_point_5171_passes_required_schema():
    result = check_payload("point_info", NONEMPTY, observed_fingerprint=None)
    assert result.ok is True


def test_point_info_missing_identity_fails():
    result = check_payload("point_info", {"retcode": 0, "data": {}}, observed_fingerprint=None)
    assert result.ok is False
    assert result.level == SchemaCheck.FAIL


def test_point_info_extra_optional_field_is_warning():
    payload = json.loads(json.dumps(NONEMPTY))
    payload["new_optional"] = True
    result = check_payload("point_info", payload, observed_fingerprint="deadbeef")
    assert result.ok is True
    assert result.level == SchemaCheck.WARNING
    assert result.reason == "SCHEMA_CHANGED"
