from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class GuideAsset:
    sha256: str
    path: Path
    source_url: str


class RawGuideStore:
    def __init__(self, root: Path, assets_root: Path | None = None):
        self.root = Path(root)
        self.assets_root = Path(assets_root) if assets_root else self.root.parent / "guide-assets" / "sha256"
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)

    def start_run(self, topic: str) -> str:
        run_id = _now()
        base = self.root / "raw" / run_id
        for name in ("pages", "extracted", "manifests"):
            (base / name).mkdir(parents=True, exist_ok=True)
        manifest = {"topic": topic, "run_id": run_id, "started_at": run_id}
        (base / "manifests" / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return run_id

    def _run(self, run_id: str) -> Path:
        return self.root / "raw" / run_id

    def save_html(self, run_id: str, page_key: str, html: str) -> Path:
        path = self._run(run_id) / "pages" / f"{page_key}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return path

    def save_text(self, run_id: str, page_key: str, text: str) -> Path:
        path = self._run(run_id) / "pages" / f"{page_key}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def save_extracted(self, run_id: str, page_key: str, blocks: list[dict[str, Any]]) -> Path:
        path = self._run(run_id) / "extracted" / f"{page_key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_asset(self, body: bytes, source_url: str) -> GuideAsset:
        digest = sha256(body).hexdigest()
        ext = Path(urlparse(source_url).path).suffix or ".bin"
        if len(ext) > 8:
            ext = ".bin"
        dest = self.assets_root / digest[:2] / f"{digest}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(body)
        return GuideAsset(sha256=digest, path=dest, source_url=source_url)
