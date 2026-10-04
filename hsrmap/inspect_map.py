from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hsrmap.database import CoreDatabase
from hsrmap.paths import ASSETS, CURRENT_PATH, DATA


def _current_db() -> Path:
    current = json.loads(CURRENT_PATH.read_text(encoding="utf-8"))
    return DATA / current["core_db"]


def compose_map(db: CoreDatabase, map_row, assets_root: Path = ASSETS) -> Image.Image:
    canvas = Image.new(
        "RGBA",
        (int(map_row["canvas_width"]), int(map_row["canvas_height"])),
        (0, 0, 0, 0),
    )
    for fragment in db.fragments_for_map(map_row["id"]):
        sha = fragment["asset_sha256"]
        if not sha:
            raise FileNotFoundError(f"fragment {fragment['id']} missing asset")
        matches = list((assets_root / sha[:2]).glob(f"{sha}.*"))
        if not matches:
            raise FileNotFoundError(f"asset {sha} not on disk")
        image = Image.open(matches[0]).convert("RGBA")
        pos = json.loads(fragment["position_json"] or "{}")
        box = (int(round(pos.get("width") or image.width)), int(round(pos.get("height") or image.height)))
        if image.size != box:
            image = image.resize(box, Image.Resampling.LANCZOS)
        canvas.paste(image, (int(round(pos.get("x") or 0)), int(round(pos.get("y") or 0))))
    return canvas


def inspect(map_id_or_name: str, out_path: Path | None = None, db_path: Path | None = None) -> Path:
    db = CoreDatabase(db_path or _current_db())
    row = db.map_by_source(str(map_id_or_name)) or db.map_by_name(map_id_or_name)
    if row is None:
        raise SystemExit(f"map not found: {map_id_or_name}")
    image = compose_map(db, row)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    for point in db.points_for_map(row["id"]):
        x, y = float(point["raster_x"]), float(point["raster_y"])
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline=(255, 48, 64, 255), width=3)
        draw.text((x + 12, y - 10), str(point["source_id"]), fill=(255, 240, 200, 255), font=font)
    dest = out_path or (DATA / "snapshots" / "_inspect" / f"inspect-{row['name'] or row['source_id']}.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    db.close()
    return dest
