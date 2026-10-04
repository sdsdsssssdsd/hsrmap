"""Leaflet markers must stay position:absolute or they drift on zoom."""

from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "web" / "src" / "styles" / "layout.css"


def _rule(css: str, selector: str) -> str:
    start = css.find(selector)
    assert start >= 0, selector
    body_start = css.find("{", start)
    body_end = css.find("}", body_start)
    return css[body_start : body_end + 1]


def test_hsr_marker_does_not_override_leaflet_absolute():
    rule = _rule(CSS.read_text(encoding="utf-8"), ".hsr-marker {")
    assert "position: relative" not in rule
    assert "position: absolute" in rule
