from __future__ import annotations

from pathlib import Path

EXTRACT_JS = (Path(__file__).resolve().parent / "live_extract.js").read_text(encoding="utf-8")


def capture_official_raster_points(map_id: int, timeout_ms: int = 90000) -> dict[int, list[float]] | None:
    """Read live Leaflet map.project(latlng, 0) from the official HSR map page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    url = (
        "https://act.hoyolab.com/sr/app/interactive-map/index.html"
        f"?lang=zh-cn#/map/{map_id}"
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000}, locale="zh-CN")
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(".leaflet-marker-icon", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            data = page.evaluate(EXTRACT_JS)
            browser.close()
    except Exception:
        return None

    points = (data or {}).get("points") if isinstance(data, dict) else None
    if not points:
        return None
    return {int(pid): [float(xy[0]), float(xy[1])] for pid, xy in points.items()}
