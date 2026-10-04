from hsrmap.guides.vision.slicer import slice_long_image


def test_tall_image_is_sliced_and_keeps_original():
    # 10x40 synthetic gray rows; ratio 4 > 3
    blob = _pgm(10, 40)
    out = slice_long_image(blob)
    assert out["sliced"] is True
    assert out["original"] == blob
    assert len(out["segments"]) >= 2
    assert all("bbox" in item for item in out["segments"])
    assert all(item["bbox"]["y1"] > item["bbox"]["y0"] for item in out["segments"])


def test_normal_image_is_not_sliced():
    blob = _pgm(20, 20)
    out = slice_long_image(blob)
    assert out["sliced"] is False
    assert out["segments"] == []


def test_missing_image_bytes_are_not_sliced():
    empty = slice_long_image(b"")
    missing = slice_long_image(None)
    assert empty["sliced"] is False
    assert missing["sliced"] is False
    assert empty["segments"] == []
    assert missing["segments"] == []


def _pgm(width: int, height: int) -> bytes:
    header = f"P5\n{width} {height}\n255\n".encode()
    return header + (b"\x80" * (width * height))
