from hsrmap.guides.regions.resolver import resolve_map


def test_erxiang_is_parent_not_renderable():
    maps = [{"map_id": "root", "name": "二相乐园", "renderable": False, "path": "二相乐园"}]
    out = resolve_map("二相乐园", maps)
    assert out["status"] == "PARENT"
    assert out["map_id"] is None


def test_exact_haiyuan_matches():
    maps = [{"map_id": "842", "name": "海原市", "renderable": True, "path": "海原市"}]
    out = resolve_map("海原市", maps)
    assert out["status"] == "MATCH"
    assert out["map_id"] == "842"


def test_guanlan_alias_from_image_hud():
    maps = [
        {
            "map_id": "683",
            "name": "观览云岛站",
            "renderable": True,
            "path": "观览云岛站",
            "aliases": ["观觉云岛站"],
        }
    ]
    out = resolve_map("观觉云岛站", maps)
    assert out["status"] == "MATCH"
    assert out["map_id"] == "683"
    assert out["map_name"] == "观览云岛站"
