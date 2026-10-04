from hsrmap.guides.vision.variants import build_image_variants


def test_variant_cache_writes_rebuildable_derivatives(tmp_path):
    blob = _pgm(80, 80)
    out = build_image_variants(blob, sha256="ab" + "c" * 62, cache_root=tmp_path)
    assert out["original_kept"] is True
    assert (tmp_path / "thumbnail_384" / "ab" / ("c" * 62 + ".png")).exists() or any(tmp_path.rglob("thumbnail_384*"))
    assert "thumbnail_384" in out["variants"]
    assert "vision_preview_1024" in out["variants"]
    assert out["variants"]["thumbnail_384"] != blob


def _pgm(width: int, height: int) -> bytes:
    header = f"P5\n{width} {height}\n255\n".encode()
    return header + (b"\x7f" * (width * height))
