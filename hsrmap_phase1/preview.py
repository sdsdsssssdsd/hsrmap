from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from hsrmap_phase1.raster import raster_spec_from_detail
from hsrmap_phase1.transform import Transform, source_to_raster


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_image(data: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {
        "sha256": sha256_bytes(data),
        "bytes": len(data),
        "is_html": data.lstrip().startswith(b"<") or b"<html" in data[:200].lower(),
        "decodable": False,
        "width": None,
        "height": None,
        "mime": None,
    }
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        info["decodable"] = True
        info["width"], info["height"] = image.size
        info["mime"] = Image.MIME.get(image.format, image.format)
    except Exception as exc:
        info["error"] = str(exc)
    return info


def compose_raster(spec: dict[str, Any], fragment_images: dict[int, Image.Image]) -> Image.Image:
    canvas = Image.new(
        "RGBA",
        (int(spec["canvas"]["width"]), int(spec["canvas"]["height"])),
        (0, 0, 0, 0),
    )
    for fragment in spec["fragments"]:
        image = fragment_images.get(fragment["index"])
        if image is None:
            continue
        box = (
            int(round(fragment["width"])),
            int(round(fragment["height"])),
        )
        if image.size != box:
            image = image.resize(box, Image.Resampling.LANCZOS)
        canvas.paste(image, (int(round(fragment["x"])), int(round(fragment["y"]))))
    return canvas


def draw_alignment_preview(
    composed: Image.Image,
    points: list[dict[str, Any]],
    transform: Transform,
) -> Image.Image:
    preview = composed.convert("RGBA")
    draw = ImageDraw.Draw(preview)
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    for item in points:
        src = item["source_coordinate"]
        x, y = source_to_raster(src["x"], src["y"], transform)
        r = 18
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(255, 48, 64, 255), width=4)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 255))
        label = f"{item['source_point_id']} e={item.get('error_px', 0):.2f}"
        draw.text((x + 22, y - 18), label, fill=(255, 240, 200, 255), font=font)
    return preview


def build_raster_bundle(
    map_id: Any,
    detail: dict[str, Any],
    downloaded: list[tuple[tuple[int, int], str, bytes, dict[str, Any]]],
) -> tuple[dict[str, Any], Image.Image]:
    sizes = {key: (meta["width"], meta["height"]) for key, _name, _data, meta in downloaded if meta.get("decodable")}
    files = {key: name for key, name, _data, _meta in downloaded}
    spec = raster_spec_from_detail(map_id, detail, fragment_sizes=sizes, local_files=files)
    images: dict[int, Image.Image] = {}
    for fragment in spec["fragments"]:
        for key, _name, data, meta in downloaded:
            if key == (fragment["row"], fragment["col"]) and meta.get("decodable"):
                images[fragment["index"]] = Image.open(io.BytesIO(data)).convert("RGBA")
    composed = compose_raster(spec, images)
    return spec, composed
