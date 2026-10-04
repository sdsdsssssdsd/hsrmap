"""Fingerprint drift is a warning; missing required fields must fail."""

from hsrmap.schema import SchemaCheck, check_payload


def test_extra_optional_field_is_warning_not_failure():
    payload = {
        "retcode": 0,
        "message": "OK",
        "data": {"tree": []},
        "new_optional": True,
    }
    result = check_payload("map_tree", payload, observed_fingerprint="deadbeef")
    assert result.ok is True
    assert result.level == SchemaCheck.WARNING
    assert result.reason == "SCHEMA_CHANGED"


def test_missing_required_field_fails():
    payload = {"retcode": 0, "message": "OK", "data": {}}
    result = check_payload("map_info", payload, observed_fingerprint=None)
    assert result.ok is False
    assert result.level == SchemaCheck.FAIL
