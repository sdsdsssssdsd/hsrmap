"""Render a page that needs JavaScript, with a browser that is already installed.

miyoushe, taptap and bilibili-video pages answer a plain GET with an app shell:
QA says JS_RENDER_REQUIRED and the URL is filed as unreachable. The article is
there all the same — it is written into the DOM after the bundle runs. A headless
Chrome/Edge (--dump-dom) prints that DOM, so the ordinary parser can read it.

No new dependency: the machine already has a browser, and the renderer writes its
output to a file instead of a pipe so a megabyte of DOM cannot deadlock it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

#: Where Chrome and Edge install themselves on Windows; elsewhere PATH is used.
CHROME_NAMES = ("chrome", "chrome.exe")
EDGE_NAMES = ("msedge", "msedge.exe", "chromium", "chromium-browser")
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
#: Edge hangs under --headless=new on a shared profile here, so it comes second.
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

#: How long the page may keep fetching data before the DOM is dumped.
DEFAULT_BUDGET_MS = 12000


def _which(name: str, env: dict[str, str]) -> str:
    for folder in str(env.get("PATH") or "").split(os.pathsep):
        if not folder:
            continue
        candidate = Path(folder) / name
        if candidate.exists():
            return str(candidate)
    return ""


def browser_path(env: dict[str, str] | None = None) -> str:
    """The browser to render with: GUIDE_BROWSER, then PATH, then known installs."""
    values = env if env is not None else os.environ
    override = str(values.get("GUIDE_BROWSER") or "").strip()
    if override and Path(override).exists():
        return override
    for names, candidates in ((CHROME_NAMES, CHROME_CANDIDATES), (EDGE_NAMES, EDGE_CANDIDATES)):
        for name in names:
            found = _which(name, values)
            if found:
                return found
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
    return ""


def render_command(url: str, browser: str, *, profile: str, budget_ms: int = DEFAULT_BUDGET_MS) -> list[str]:
    """The argv that prints the rendered DOM of a url to stdout."""
    #: plain --headless plus --timeout, not "--headless=new --virtual-time-budget":
    #: on this machine the newer headless modes wait on a Chrome updater pipe that
    #: is denied (WinError 5) and never exit, while the classic flag dumps the DOM
    #: in seconds. --timeout makes the browser exit on its own.
    return [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--user-data-dir=" + str(profile),
        "--timeout=" + str(int(budget_ms)),
        "--dump-dom",
        url,
    ]


def _remove_later(folder: Path, attempts: int = 6) -> None:
    """Delete a temp folder best effort.

    Chrome's children can outlive the main process for a moment and keep the
    dumped DOM open, and on Windows that turns cleanup into WinError 32. Retry a
    few times and never raise: a leftover temp file is not a crawl failure.
    """
    import shutil
    import time

    for _ in range(attempts):
        try:
            shutil.rmtree(folder)
            return
        except OSError:
            time.sleep(0.25)


def render_page(
    url: str,
    *,
    browser: str = "",
    budget_ms: int = DEFAULT_BUDGET_MS,
    timeout: float = 45.0,
    profile_root: Path | str | None = None,
) -> dict:
    """(status, html) for a page that needs JavaScript.

    Status is "ok", "NO_BROWSER" (nothing to render with) or "RENDER_FAILED"
    (the browser ran but produced nothing), so a caller can tell "this site is
    unreachable" apart from "this machine cannot render".
    """
    executable = browser or browser_path()
    if not executable:
        return {"status": "NO_BROWSER", "url": url, "html": "", "reason": "no chrome or edge found"}
    root = Path(profile_root) if profile_root else Path(tempfile.gettempdir()) / "guide-render-profile"
    root.mkdir(parents=True, exist_ok=True)
    #: a profile per render: a shared one can be locked by a browser that has not
    #: exited yet, which is exactly how a render turns into a timeout
    profile = Path(tempfile.mkdtemp(prefix="profile-", dir=str(root)))
    work = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        dom_path = work / "dom.html"
        err_path = work / "err.txt"
        with dom_path.open("wb") as out, err_path.open("wb") as err:
            try:
                completed = subprocess.run(
                    render_command(url, executable, profile=str(profile), budget_ms=budget_ms),
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"status": "RENDER_FAILED", "url": url, "html": "", "reason": "timeout"}
            except OSError as exc:
                return {"status": "RENDER_FAILED", "url": url, "html": "", "reason": str(exc)[:120]}
        html = dom_path.read_text(encoding="utf-8", errors="replace") if dom_path.exists() else ""
        if completed.returncode != 0 and not html.strip():
            detail = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
            return {
                "status": "RENDER_FAILED",
                "url": url,
                "html": "",
                "reason": ("exit %s: %s" % (completed.returncode, detail.strip()[:120])).strip(),
            }
    finally:
        _remove_later(work)
        _remove_later(profile)
    if len(html.strip()) < 200:
        return {"status": "RENDER_FAILED", "url": url, "html": html, "reason": "empty dom"}
    return {"status": "ok", "url": url, "html": html}
