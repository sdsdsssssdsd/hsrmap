#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HSR HoYoLAB Interactive Map public-data dumper
==============================================

Scope is intentionally limited to:
  Honkai: Star Rail / 崩坏：星穹铁道
  https://act.hoyolab.com/sr/app/interactive-map/

It does NOT contain Genshin/ZZZ map endpoints and does NOT reuse your normal
browser profile, cookies, or HoYoLAB login.

Strategy:
1) Open only the official HSR interactive-map page in a fresh Chromium context.
2) Watch network responses.
3) Keep only responses whose URL is explicitly HSR-map-like (srmap/sr_map), or
   whose JSON body contains HSR/map-like structures.
4) Save accepted JSON locally.
5) Record request URL + non-sensitive headers, including map-version headers if
   the current frontend sends them.
6) Best-effort extract map/label/point objects into CSV/JSONL.
7) Optionally download image URLs referenced by the accepted JSON.

This avoids hard-coding the 2024 sr_map/v1 endpoint as the only truth.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HSR_MAP_URL = "https://act.hoyolab.com/sr/app/interactive-map/index.html?lang=zh-cn"

SENSITIVE_HEADERS = {
    "cookie",
    "set-cookie",
    "authorization",
    "x-rpc-device_id",
    "x-rpc-device_fp",
    "x-rpc-aigis",
}

# Strong URL indicators for HSR HoYoLAB map traffic.
HSR_URL_HINTS = (
    "/common/srmap/",
    "/srmap/",
    "sr_map",
)

# Generic schema indicators. A response needs several of these unless its URL
# already identifies it as HSR map traffic.
SCHEMA_KEYS = {
    "map_id",
    "label_id",
    "point_id",
    "x_pos",
    "y_pos",
    "slices",
    "total_size",
    "parent_name",
    "display_state",
}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")


def redact(headers: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in (headers or {}).items():
        if k.lower() in SENSITIVE_HEADERS:
            continue
        out[k] = v
    return out


def safe_name(url: str, idx: int) -> str:
    p = urlparse(url)
    base = Path(p.path).name or "response"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "response"
    if not base.endswith(".json"):
        base += ".json"
    return f"{idx:04d}_{hashlib.sha1(url.encode()).hexdigest()[:10]}_{base[:90]}"


def json_key_score(obj) -> int:
    found = set()

    def walk(x, depth=0):
        if depth > 10:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if k in SCHEMA_KEYS:
                    found.add(k)
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x[:500]:
                walk(v, depth + 1)

    walk(obj)
    return len(found)


def hsr_url_match(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in HSR_URL_HINTS)


def looks_like_hsr_map_json(url: str, obj) -> bool:
    # HSR-specific URL is sufficient.
    if hsr_url_match(url):
        return True

    # Otherwise require several characteristic map fields.
    score = json_key_score(obj)
    if score >= 4:
        return True

    # Some config responses may identify the app explicitly.
    blob = json.dumps(obj, ensure_ascii=False)[:2_000_000].lower()
    if '"app_sn"' in blob and "sr_map" in blob:
        return True
    if "srmap" in blob and score >= 2:
        return True

    return False


def walk_dicts(obj, path="$"):
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk_dicts(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_dicts(v, f"{path}[{i}]")


def extract_objects(raw_dir: Path, out_dir: Path):
    maps = []
    labels = []
    points = []
    seen_maps = set()
    seen_labels = set()
    seen_points = set()

    for fp in sorted(raw_dir.glob("*.json")):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        for jpath, d in walk_dicts(doc):
            # Point-like objects.
            if "x_pos" in d and "y_pos" in d and (
                "id" in d or "point_id" in d
            ):
                row = {
                    "id": d.get("id", d.get("point_id")),
                    "map_id": d.get("map_id"),
                    "label_id": d.get("label_id"),
                    "x_pos": d.get("x_pos"),
                    "y_pos": d.get("y_pos"),
                    "z_pos": d.get("z_pos"),
                    "name": d.get("name") or d.get("title"),
                    "content": d.get("content") or d.get("description") or d.get("desc"),
                    "icon": d.get("icon") or d.get("icon_url"),
                    "source_file": fp.name,
                    "source_path": jpath,
                }
                key = (
                    str(row["id"]), str(row["map_id"]), str(row["label_id"]),
                    str(row["x_pos"]), str(row["y_pos"])
                )
                if key not in seen_points:
                    seen_points.add(key)
                    points.append(row)

            # Label/category-like objects.
            if "id" in d and (
                "label_type" in d
                or ("icon" in d and "name" in d and "x_pos" not in d and "y_pos" not in d)
            ):
                row = {
                    "id": d.get("id"),
                    "parent_id": d.get("parent_id"),
                    "name": d.get("name"),
                    "icon": d.get("icon") or d.get("icon_url"),
                    "label_type": d.get("label_type"),
                    "source_file": fp.name,
                    "source_path": jpath,
                }
                key = (str(row["id"]), str(row["name"]), str(row["parent_id"]))
                if key not in seen_labels:
                    seen_labels.add(key)
                    labels.append(row)

            # Map-like objects.
            if "id" in d and "name" in d and (
                "parent_name" in d or "detail" in d or "map_id" in d
            ) and "x_pos" not in d:
                row = {
                    "id": d.get("id"),
                    "map_id": d.get("map_id"),
                    "name": d.get("name"),
                    "parent_name": d.get("parent_name"),
                    "parent_id": d.get("parent_id"),
                    "source_file": fp.name,
                    "source_path": jpath,
                }
                key = (str(row["id"]), str(row["name"]), str(row["parent_name"]))
                if key not in seen_maps:
                    seen_maps.add(key)
                    maps.append(row)

    def save_csv(name, rows, fields):
        path = out_dir / name
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def save_jsonl(name, rows):
        path = out_dir / name
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    save_csv(
        "points.csv", points,
        ["id","map_id","label_id","x_pos","y_pos","z_pos","name","content","icon","source_file","source_path"]
    )
    save_jsonl("points.jsonl", points)

    save_csv(
        "labels.csv", labels,
        ["id","parent_id","name","icon","label_type","source_file","source_path"]
    )
    save_jsonl("labels.jsonl", labels)

    save_csv(
        "maps.csv", maps,
        ["id","map_id","name","parent_name","parent_id","source_file","source_path"]
    )
    save_jsonl("maps.jsonl", maps)

    return {
        "maps": len(maps),
        "labels": len(labels),
        "points": len(points),
    }


def collect_image_urls(obj):
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            s = x.strip()
            if s.startswith(("https://", "http://")):
                path = urlparse(s).path.lower()
                if path.endswith(IMAGE_EXTS) or any(ext + "?" in s.lower() for ext in IMAGE_EXTS):
                    found.add(s)

    walk(obj)
    return found


def download_images(urls, out_dir: Path):
    img_dir = out_dir / "referenced_images"
    img_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for i, url in enumerate(sorted(urls), 1):
        try:
            p = urlparse(url)
            ext = Path(p.path).suffix.lower()
            if ext not in IMAGE_EXTS:
                ext = ".bin"
            name = f"{i:05d}_{hashlib.sha1(url.encode()).hexdigest()[:12]}{ext}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "")
            (img_dir / name).write_bytes(body)
            manifest.append({
                "url": url,
                "file": f"referenced_images/{name}",
                "bytes": len(body),
                "content_type": ctype,
            })
            print(f"[image {i}/{len(urls)}] {name}")
        except Exception as e:
            manifest.append({"url": url, "error": str(e)})

    (out_dir / "images_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def capture(args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "缺少 Playwright。\n"
            "先运行：\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        raise SystemExit(2)

    out = Path(args.out).expanduser().resolve()
    raw = out / "raw_json"
    out.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)

    accepted = []
    image_urls = set()
    seq = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1500, "height": 1000},
        )
        page = context.new_page()

        def on_response(resp):
            nonlocal seq
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
                url = resp.url

                # We only inspect JSON-like responses, plus explicit srmap endpoints.
                if "json" not in ctype and not hsr_url_match(url):
                    return

                body = resp.body()
                try:
                    obj = json.loads(body.decode("utf-8"))
                except Exception:
                    return

                if not looks_like_hsr_map_json(url, obj):
                    return

                seq += 1
                fn = safe_name(url, seq)
                (raw / fn).write_text(
                    json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                req_headers = redact(resp.request.headers)
                # Keep useful version/app headers if present; sensitive ones were removed.
                accepted.append({
                    "url": url,
                    "status": resp.status,
                    "content_type": ctype,
                    "file": f"raw_json/{fn}",
                    "request_method": resp.request.method,
                    "request_headers": req_headers,
                })

                image_urls.update(collect_image_urls(obj))
                print(f"[HSR JSON] {url}")
            except Exception as e:
                print(f"[warn] {getattr(resp, 'url', '?')}: {e}", file=sys.stderr)

        page.on("response", on_response)
        page.goto(HSR_MAP_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)

        if args.headed:
            print()
            print("只打开了《崩坏：星穹铁道》HoYoLAB 官方互动地图。")
            print("请在这个临时浏览器里切换你要保存的崩铁地图/点位分类。")
            print("建议把「浮脂溯源」所在地图逐张点开，并点开具体点位详情。")
            print("不要登录 HoYoLAB；公开地图数据不需要你的账号 Cookie。")
            print()
            input("操作完成后回到这里按 Enter：")
        else:
            page.wait_for_timeout(int(max(1, args.seconds) * 1000))

        # Save only the HSR page HTML for debugging current frontend/version.
        (out / "hsr_map_page.html").write_text(page.content(), encoding="utf-8")

        context.close()
        browser.close()

    (out / "api_requests.json").write_text(
        json.dumps(accepted, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    counts = extract_objects(raw, out)

    if args.download_images and image_urls:
        download_images(image_urls, out)

    summary = {
        "scope": "Honkai: Star Rail only",
        "official_entry": HSR_MAP_URL,
        "accepted_hsr_json_responses": len(accepted),
        "referenced_image_urls": len(image_urls),
        **counts,
        "notes": [
            "No Genshin/ZZZ endpoint is configured in this script.",
            "The browser context is fresh and does not reuse your normal Chrome cookies.",
            "api_requests.json records the CURRENT HSR map request URLs and non-sensitive headers.",
            "If the frontend currently sends X-Rpc-Map_version/app-version headers, they will appear there.",
            "Only data loaded during this browsing session can be saved; browse more HSR maps/categories for a fuller dump.",
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    (out / "referenced_image_urls.txt").write_text(
        "\n".join(sorted(image_urls)) + ("\n" if image_urls else ""),
        encoding="utf-8"
    )

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n输出目录：{out}")


def main():
    ap = argparse.ArgumentParser(
        description="仅抓取 HoYoLAB《崩坏：星穹铁道》官方互动地图公开数据"
    )
    ap.add_argument("--out", default="hsr_hoyolab_dump", help="输出目录")
    ap.add_argument("--headed", action="store_true", help="打开可视 Chromium，手动浏览地图以抓全数据")
    ap.add_argument("--seconds", type=float, default=25, help="无界面模式额外等待秒数")
    ap.add_argument("--download-images", action="store_true", help="下载 HSR JSON 中直接引用的图片 URL")
    args = ap.parse_args()
    capture(args)


if __name__ == "__main__":
    main()
