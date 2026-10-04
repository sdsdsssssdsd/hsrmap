"""Perceptual hashing: one picture, many encodings (Sprint 2)."""

import io

from PIL import Image, ImageDraw

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.assets.cache import AssetCache
from hsrmap.guides.assets.fetcher import FETCHED, AssetFetchResult
from hsrmap.guides.assets.phash import duplicate_groups, hamming, phash, similar


def _picture(kind: str = "a", size: tuple[int, int] = (240, 160)) -> Image.Image:
    image = Image.new("RGB", size, (20, 30, 40))
    draw = ImageDraw.Draw(image)
    if kind == "a":
        draw.rectangle([20, 20, 120, 100], fill=(230, 200, 60))
        draw.ellipse([140, 40, 210, 120], fill=(60, 200, 230))
    else:
        draw.line([0, 0, size[0], size[1]], fill=(250, 250, 250), width=6)
    return image


def _bytes(image: Image.Image, fmt: str = "PNG", **save) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **save)
    return buffer.getvalue()


def _result(url: str, body: bytes, *, fmt: str = "PNG") -> AssetFetchResult:
    from hashlib import sha256

    with Image.open(io.BytesIO(body)) as opened:
        width, height = opened.size
    digest = sha256(body).hexdigest()
    return AssetFetchResult(
        status=FETCHED,
        source_url=url,
        final_url=url,
        content_type=f"image/{fmt.lower()}",
        sha256=digest,
        width=width,
        height=height,
        format=fmt,
        byte_size=len(body),
        body=body,
    )


def test_reencoded_image_keeps_its_hash():
    original = _picture("a")
    small_jpeg = _bytes(original.resize((120, 80)), "JPEG", quality=85)
    assert similar(phash(_bytes(original)), phash(small_jpeg))
    assert hamming(phash(_bytes(original)), phash(small_jpeg)) <= 6


def test_different_pictures_are_far_apart():
    left, right = phash(_bytes(_picture("a"))), phash(_bytes(_picture("b")))
    assert left and right
    assert not similar(left, right)
    assert hamming(left, right) > 20


def test_unreadable_bytes_have_no_hash():
    assert phash(b"not an image") == ""
    assert phash() == ""
    assert not similar("", "44c42b3f913f36c4")
    assert hamming("", "44c42b3f913f36c4") > 1000


def test_duplicate_groups_clusters_variants_only():
    picture = _picture("a")
    records = [
        ("png", phash(_bytes(picture))),
        ("jpeg", phash(_bytes(picture.resize((120, 80)), "JPEG", quality=80))),
        ("other", phash(_bytes(_picture("b")))),
    ]
    groups = duplicate_groups(records)
    assert groups == [["jpeg", "png"]]


def test_asset_cache_persists_phash_and_finds_visual_duplicates(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    cache = AssetCache(tmp_path / "cache", db=db)
    picture = _picture("a")
    png = _result("https://cdn.test/a.png", _bytes(picture))
    jpeg = _result("https://cdn.test/a.jpg", _bytes(picture.resize((120, 80)), "JPEG", quality=80), fmt="JPEG")
    other = _result("https://cdn.test/b.png", _bytes(_picture("b")))
    for result in (png, jpeg, other):
        stored = cache.store(result)
        assert stored["phash"]
    assert cache.phash_of(png.sha256) == phash(_bytes(picture))
    groups = cache.visual_duplicates()
    assert groups == [sorted([png.sha256, jpeg.sha256])]
    assert other.sha256 not in sum(groups, [])
    db.close()


def test_phash_of_a_missing_asset_is_empty(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    cache = AssetCache(tmp_path / "cache", db=db)
    assert cache.phash_of("0" * 64) == ""
    assert cache.visual_duplicates() == []
    db.close()

def test_cli_asset_dedup_reports_visual_groups(tmp_path, capsys):
    import json

    from hsrmap.cli import main

    db_path = tmp_path / "guide.db"
    cache_root = tmp_path / "cache"
    db = GuideDatabase(db_path)
    cache = AssetCache(cache_root, db=db)
    picture = _picture("a")
    cache.store(_result("https://cdn.test/a.png", _bytes(picture)))
    cache.store(
        _result("https://cdn.test/a.jpg", _bytes(picture.resize((120, 80)), "JPEG", quality=80), fmt="JPEG")
    )
    db.close()

    code = main([
        "guides", "asset-dedup",
        "--db", str(db_path),
        "--cache", str(cache_root),
        "--limit", "5",
    ])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["groups"] == 1
    assert body["assets"] == 2
    assert body["threshold"] == 6

