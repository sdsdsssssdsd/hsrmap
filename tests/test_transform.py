"""Coordinate transform must follow the official HoYoLAB CRS, not a guessed flip."""

from hsrmap_phase1.transform import Transform, source_to_raster, raster_to_source


def test_official_crs_adds_origin_and_does_not_flip_y():
    transform = Transform.from_map_detail(
        {
            "total_size": [8192, 4096],
            "origin": [3827, 2217],
            "padding": [0, 0],
        }
    )

    rx, ry = source_to_raster(85.5, -81.5, transform)

    assert rx == 3912.5
    assert ry == 2135.5
    assert transform.transform["flip_y"] is False
    assert transform.transform["offset_x"] == 3827
    assert transform.transform["offset_y"] == 2217


def test_live_leaflet_sample_from_seafeld_matches_official_crs():
    """Regression fixture from official #/map/842 Leaflet map.project(..., 0)."""
    transform = Transform.from_map_detail({"total_size": [8192, 4096], "origin": [3827, 2217]})
    # pointId 5152 observed live: lat=-771.5, lng=1727.5, x0=5554.5, y0=1445.5
    assert source_to_raster(1727.5, -771.5, transform) == (5554.5, 1445.5)


def test_padding_is_not_part_of_point_transform():
    with_padding = Transform.from_map_detail(
        {"total_size": [100, 80], "origin": [10, 20], "padding": [50, 50]}
    )
    without_padding = Transform.from_map_detail(
        {"total_size": [100, 80], "origin": [10, 20], "padding": [0, 0]}
    )

    assert source_to_raster(3, 4, with_padding) == source_to_raster(3, 4, without_padding)
    assert raster_to_source(13, 24, with_padding) == (3.0, 4.0)
