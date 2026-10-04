"""本地服务：端口与数据模式的默认值（审核台与离线地图是两个端口）。"""

from hsrmap.serve import LANDING, MAP_PORT, REVIEW_PORT, resolve_data_mode, resolve_kind, resolve_port


def test_map_and_review_default_to_separate_ports():
    #: 用户 2026-10：审核网页与离线地图不要耦合 —— 地图 8766、审核台 8767。
    assert MAP_PORT == 8766
    assert REVIEW_PORT == 8767
    assert resolve_port(None, "map") == 8766
    assert resolve_port(None, "review") == 8767
    assert resolve_port(0, "map") == 8766
    assert resolve_port(9000, "review") == 9000
    assert resolve_kind(None) == "map"
    assert LANDING["map"] == "/" and LANDING["review"] == "/review"


def test_viewer_data_mode_defaults_offline():
    assert resolve_data_mode(None) == "offline"
    assert resolve_data_mode("hybrid") == "hybrid"
    assert resolve_data_mode("live") == "live"
    assert resolve_data_mode("nope") == "offline"
