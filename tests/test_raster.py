"""RasterSpec must accept a single full-map slice, not a 2048 grid."""

from hsrmap_phase1.raster import raster_spec_from_detail


def test_single_full_map_slice_becomes_one_fragment_at_origin():
    detail = {
        "total_size": [8192, 4096],
        "origin": [3827, 2217],
        "padding": None,
        "slices": [[{"url": "https://example.test/full.png"}]],
    }

    spec = raster_spec_from_detail(
        map_id=842,
        detail=detail,
        fragment_sizes={(0, 0): (8192, 4096)},
        local_files={(0, 0): "raster/source/000.png"},
    )

    assert spec["canvas"] == {"width": 8192, "height": 4096}
    assert spec["origin"] == [3827, 2217]
    assert len(spec["fragments"]) == 1
    fragment = spec["fragments"][0]
    assert fragment["x"] == 0
    assert fragment["y"] == 0
    assert fragment["width"] == 8192
    assert fragment["height"] == 4096
    assert fragment["source_width"] == 8192
    assert fragment["source_height"] == 4096


def test_multi_slice_uses_official_equal_grid_not_2048():
    """Official GridLayer places slices at total_size / (cols, rows), not a 2048 assumption."""
    detail = {
        "total_size": [3000, 2000],
        "origin": [0, 0],
        "slices": [
            [
                {"url": "https://example.test/a.png"},
                {"url": "https://example.test/b.png"},
            ],
            [
                {"url": "https://example.test/c.png"},
            ],
        ],
    }

    spec = raster_spec_from_detail(
        map_id=1,
        detail=detail,
        fragment_sizes={(0, 0): (2048, 1500), (0, 1): (952, 1500), (1, 0): (3000, 500)},
        local_files={
            (0, 0): "a.png",
            (0, 1): "b.png",
            (1, 0): "c.png",
        },
    )

    by_index = {f["index"]: f for f in spec["fragments"]}
    assert by_index[0]["x"] == 0
    assert by_index[0]["y"] == 0
    assert by_index[0]["width"] == 1500
    assert by_index[0]["height"] == 1000
    assert by_index[1]["x"] == 1500
    assert by_index[1]["y"] == 0
    assert by_index[2]["x"] == 0
    assert by_index[2]["y"] == 1000
    assert by_index[0]["source_width"] == 2048
    assert by_index[0]["source_height"] == 1500
