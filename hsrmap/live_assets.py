from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hsrmap.providers.live import live_asset_key


class LiveAssetStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, url: str) -> Path:
        key = live_asset_key(url)
        ext = Path(urlparse(url).path).suffix or ".bin"
        if len(ext) > 8:
            ext = ".bin"
        return self.root / f"{key}{ext}"

    def fetch(self, client: Any, url: str) -> Path:
        path = self.path_for(url)
        if path.exists() and path.stat().st_size > 0:
            return path
        response = client.get(url)
        path.write_bytes(response.body)
        return path
