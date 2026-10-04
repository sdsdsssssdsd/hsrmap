"""Robots-aware fetch never bypasses Disallow."""

from hsrmap.guides.crawler.fetch import fetch_page
from hsrmap.guides.crawler.robots import path_allowed


def test_disallow_article_requires_manual_import():
    robots = "User-agent: *\nDisallow: /content/\n"
    assert path_allowed(robots, "/handbook/1.html") is True
    assert path_allowed(robots, "/content/04222026/173231817.shtml") is False
    result = fetch_page(
        "https://news.17173.com/content/04222026/173231817.shtml",
        client=object(),
        robots_txt=robots,
    )
    assert result["status"] == "NEEDS_MANUAL_IMPORT"


def test_allowed_path_uses_injected_client():
    class Fake:
        def get(self, url, timeout=30):
            return type("R", (), {"body": b"<html>ok</html>", "status": 200})()

    result = fetch_page("https://news.17173.com/handbook/1.html", client=Fake(), robots_txt="User-agent: *\nAllow: /\n")
    assert result["status"] == "ok"
    assert "ok" in result["html"]
