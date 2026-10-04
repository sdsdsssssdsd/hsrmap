"""Raster bounds come from origin+canvas; padding only expands maxBounds."""

from hsrmap.viewer_crs import get_max_bounds, get_raster_bounds


def test_haiyuanshi_raster_bounds_from_origin():
    bounds = get_raster_bounds(origin_x=3827, origin_y=2217, width=8192, height=4096)
    assert bounds["south_west"] == [-2217.0, -3827.0]
    assert bounds["north_east"] == [1879.0, 4365.0]


def test_padding_expands_max_bounds_not_raster():
    raster = get_raster_bounds(origin_x=3827, origin_y=2217, width=8192, height=4096)
    max_bounds = get_max_bounds(origin_x=3827, origin_y=2217, width=8192, height=4096, padding=[1706, 219])
    assert max_bounds["south_west"] == [-2217.0 - 219, -3827.0 - 1706]
    assert max_bounds["north_east"] == [1879.0 + 219, 4365.0 + 1706]
    assert raster["south_west"] != max_bounds["south_west"]


def test_marker_project_matches_raster_without_using_raster_xy_as_latlng():
    from hsrmap.transform import source_to_raster

    x_pos, y_pos = 85.5, -81.5
    origin_x, origin_y = 3827.0, 2217.0
    rx, ry = source_to_raster(x_pos, y_pos, origin_x, origin_y)
    assert (rx, ry) == (3912.5, 2135.5)
    projected = (x_pos + origin_x, y_pos + origin_y)
    assert projected == (rx, ry)
