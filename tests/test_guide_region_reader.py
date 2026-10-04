from hsrmap.guides.vision.region_reader import read_region


def test_version_banner_is_not_a_map():
    class Fake:
        def read_region(self, **kwargs):
            return {"map_name_raw": "4.2版本", "visible_text": ["4.2版本"], "grounded": True}

    out = read_region(b"x", mime="image/png", sha256="v", provider=Fake(), whitelist=["海原市"])
    assert out["map_name_raw"] is None


def test_read_region_parses_relative_position_from_visible_text():
    class Fake:
        def read_region(self, **kwargs):
            return {
                "map_name_raw": "二维市",
                "visible_text": ["3号点位：【二维市】左下角"],
                "article_ordinal": 3,
                "grounded": True,
            }

    out = read_region(b"x", mime="image/png", sha256="v", provider=Fake(), whitelist=["二维市"])
    assert out["spatial_anchor"] == "左下角"
