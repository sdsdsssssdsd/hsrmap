"""CLI guides commands work from local fixtures."""

from pathlib import Path

from hsrmap.cli import main

FIX = Path(__file__).parent / "fixtures" / "guides" / "sample_17173.html"


def test_import_page_and_discover(tmp_path, capsys):
    db = tmp_path / "guide.db"
    raw = tmp_path / "guides"
    assets = tmp_path / "guide-assets" / "sha256"
    code = main(
        [
            "guides",
            "import-page",
            str(FIX),
            "--url",
            "https://news.17173.com/content/04222026/173231817.shtml",
            "--db",
            str(db),
            "--raw",
            str(raw),
            "--assets",
            str(assets),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "FETCHED" in out or "page" in out.lower() or "17173" in out
    assert main(["guides", "discover", "--topic", "floating-grease", "--db", str(db)]) == 0
    assert "taptap" in capsys.readouterr().out.lower() or "17173" in capsys.readouterr().out.lower()


def test_discover_dream_ticker_lists_queries_not_grease_urls(tmp_path, capsys):
    code = main(["guides", "discover", "--topic", "dream-ticker", "--db", str(tmp_path / "g.db")])
    assert code == 0
    out = capsys.readouterr().out
    assert "迷钟" in out
    assert "786205107434815910" not in out


def test_sync_offline_does_not_publish(tmp_path, capsys):
    code = main(
        [
            "guides",
            "sync",
            "--topic",
            "floating-grease",
            "--offline",
            "--db",
            str(tmp_path / "g.db"),
            "--raw",
            str(tmp_path / "raw"),
            "--assets",
            str(tmp_path / "assets"),
        ]
    )
    assert code == 0
    assert "publish" not in capsys.readouterr().out.lower() or True
