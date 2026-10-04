from hsrmap.guides.matching.image_similarity import average_hash, score_official_images


def test_identical_bytes_rank_first():
    blob = b"PNG-haiyuan-1" * 20
    ranked = score_official_images(blob, [{"source_point_id": "far", "bytes": b"xxxx" * 20}, {"source_point_id": "near", "bytes": blob}])
    assert ranked[0]["source_point_id"] == "near"
    assert average_hash(blob) == average_hash(blob)
