"""Request keys must ignore host so a CDN prefix change does not create new jobs."""

from hsrmap.request_key import request_key


def test_request_key_uses_endpoint_params_and_app_version_not_host():
    a = request_key("map_info", {"map_id": 842, "app_sn": "sr_map", "lang": "zh-cn"}, "abc")
    b = request_key("map_info", {"lang": "zh-cn", "app_sn": "sr_map", "map_id": 842}, "abc")
    c = request_key("map_info", {"map_id": 842, "app_sn": "sr_map", "lang": "zh-cn"}, "def")

    assert a == b
    assert a != c
    assert len(a) == 64
