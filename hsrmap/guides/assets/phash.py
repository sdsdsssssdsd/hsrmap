"""Perceptual hash: the same picture in another encoding (a1-6 §十, Sprint 2).

Exact SHA256 dedup catches a byte-identical re-download. It does not catch the
same screenshot re-uploaded as JPEG, resized, or re-encoded by another host —
which is exactly how one guide image ends up in the corpus many times.

The hash is the classic DCT one: grayscale, 32x32, 2-D DCT, keep the top-left
8x8 low frequencies, drop the DC term, and record which coefficients sit above
the median. Two images whose hashes differ by a few bits are the same picture.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageOps

#: Bits in one hash (8x8 coefficients minus the DC term).
HASH_BITS = 63

#: Working resolution for the DCT. Larger keeps more detail, costs more time.
IMAGE_SIZE = 32

#: Coefficients kept per axis.
HASH_SIZE = 8

#: Bits two hashes may differ by and still be the same picture. Re-encoding and
#: mild resizing move 0-4 bits; unrelated screenshots differ by ~30.
SIMILARITY_THRESHOLD = 6


def _open(body: bytes | None, path: str | Path | None) -> Image.Image | None:
    if body is None and path is None:
        return None
    try:
        if body is not None:
            image = Image.open(io.BytesIO(body))
        else:
            image = Image.open(str(path))
        image.load()
    except Exception:
        return None
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    if image.mode in {"RGBA", "LA", "P"}:
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        rgba = image.convert("RGBA")
        background.alpha_composite(rgba)
        image = background
    return image.convert("L")


@lru_cache(maxsize=4)
def _dct_matrix(size: int) -> np.ndarray:
    """Orthonormal DCT-II matrix (scipy is not a dependency)."""
    k = np.arange(size).reshape(-1, 1)
    n = np.arange(size).reshape(1, -1)
    matrix = np.cos(np.pi * (2 * n + 1) * k / (2 * size))
    matrix *= np.sqrt(2.0 / size)
    matrix[0, :] = np.sqrt(1.0 / size)
    return matrix


def _dct2(pixels: np.ndarray) -> np.ndarray:
    matrix = _dct_matrix(int(pixels.shape[0]))
    return matrix @ pixels @ matrix.T


def phash(
    body: bytes | None = None,
    *,
    path: str | Path | None = None,
    size: int = IMAGE_SIZE,
    hash_size: int = HASH_SIZE,
) -> str:
    """A 16-hex-character perceptual hash, or "" when the image cannot be read."""
    image = _open(body, path)
    if image is None:
        return ""
    small = image.resize((size, size), Image.LANCZOS)
    pixels = np.asarray(small, dtype=np.float64)
    dct = _dct2(pixels)
    low = dct[:hash_size, :hash_size].reshape(-1)[1:]  # drop the DC term
    if low.size == 0:
        return ""
    bits = low > np.median(low)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bool(bit))
    width = max(1, (len(bits) + 3) // 4)
    return f"{value:0{width}x}"


def phash_file(path: str | Path) -> str:
    return phash(path=path)


def hamming(left: str, right: str) -> int:
    """How many bits two hashes differ by (a large number when one is empty)."""
    if not left or not right:
        return 10 ** 6
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def similar(left: str, right: str, *, threshold: int = SIMILARITY_THRESHOLD) -> bool:
    return bool(left) and bool(right) and hamming(left, right) <= threshold


def duplicate_groups(
    records: Iterable[tuple[str, str]], *, threshold: int = SIMILARITY_THRESHOLD
) -> list[list[str]]:
    """Cluster (key, phash) pairs into near-identical groups of two or more."""
    items = [(str(key), str(value)) for key, value in records if key and value]
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if similar(items[i][1], items[j][1], threshold=threshold):
                union(i, j)
    buckets: dict[int, list[str]] = {}
    for index, (key, _) in enumerate(items):
        buckets.setdefault(find(index), []).append(key)
    return [sorted(group) for group in buckets.values() if len(group) > 1]
