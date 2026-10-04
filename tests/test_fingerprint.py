"""Schema fingerprint: missing path extraction or []-normalization would fail these."""

from hsrmap_phase1.fingerprint import extract_paths, fingerprint_schema


def test_extract_paths_walks_nested_objects_and_normalizes_array_indexes():
    sample = {
        "retcode": 0,
        "message": "OK",
        "data": {
            "info": {
                "id": 842,
                "detail": {
                    "total_size": [8192, 4096],
                    "origin": [3827, 2217],
                    "slices": [[{"url": "https://example.test/a.png"}]],
                },
            }
        },
    }

    paths = extract_paths(sample)

    assert "$.retcode" in paths
    assert "$.data.info.id" in paths
    assert "$.data.info.detail.total_size[]" in paths
    assert "$.data.info.detail.slices[][]" in paths
    assert "$.data.info.detail.slices[0][0]" not in paths


def test_fingerprint_is_sha256_of_sorted_paths_joined_by_newline():
    import hashlib

    sample = {"b": 1, "a": {"z": 2}}
    # Hand-checked paths for this fixture, independent of the production walker.
    expected_text = "\n".join(["$.a", "$.a.z", "$.b"])
    expected = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()

    digest = fingerprint_schema(sample)

    assert digest == expected
    assert digest == fingerprint_schema({"a": {"z": 9}, "b": 8})
    assert digest != fingerprint_schema({"a": {"z": 2}, "b": 1, "c": 3})
