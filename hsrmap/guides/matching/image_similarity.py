from __future__ import annotations

from typing import Any


def average_hash(image_bytes: bytes, size: int = 8) -> str:
    if not image_bytes:
        return "0" * (size * size)
    # grayscale proxy without extra deps: sample bytes
    step = max(1, len(image_bytes) // (size * size))
    samples = [image_bytes[index] for index in range(0, min(len(image_bytes), step * size * size), step)]
    samples = (samples + [0] * (size * size))[: size * size]
    mean = sum(samples) / len(samples)
    return "".join("1" if value >= mean else "0" for value in samples)


def hamming(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right, strict=False))


def score_official_images(guide_bytes: bytes, official: list[dict[str, Any]]) -> list[dict[str, Any]]:
    probe = average_hash(guide_bytes)
    ranked = []
    for item in official:
        dist = hamming(probe, average_hash(item.get("bytes") or b""))
        ranked.append({**item, "distance": dist, "score": max(0.0, 1 - dist / 64)})
    ranked.sort(key=lambda row: row["distance"])
    return ranked
