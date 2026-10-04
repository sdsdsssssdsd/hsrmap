from __future__ import annotations

import hashlib
import io
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


@dataclass
class StoredAsset:
    sha256: str
    local_path: Path
    mime_type: str | None
    width: int | None
    height: int | None
    byte_size: int


class AssetStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._sources: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        if self.index_path.exists():
            self._sources = json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self) -> None:
        tmp = self.index_path.with_name(self.index_path.name + ".tmp")
        tmp.write_text(json.dumps(self._sources, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.index_path.exists():
            try:
                self.index_path.unlink()
            except OSError:
                return
        try:
            tmp.rename(self.index_path)
        except OSError:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def path_for(self, digest: str, extension: str = ".bin") -> Path:
        return self.root / digest[:2] / f"{digest}{extension}"

    def ingest_bytes(self, data: bytes, remote_url: str | None = None, validate_image: bool = True) -> StoredAsset:
        if data.lstrip()[:1] == b"<" or b"<html" in data[:200].lower():
            raise ValueError("asset body looks like HTML")
        digest = hashlib.sha256(data).hexdigest()
        mime = None
        width = height = None
        ext = ".bin"
        if validate_image:
            image = Image.open(io.BytesIO(data))
            image.load()
            width, height = image.size
            mime = Image.MIME.get(image.format, image.format)
            ext = f".{(image.format or 'bin').lower()}"
            if ext == ".jpeg":
                ext = ".jpg"
        dest = self.path_for(digest, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            part = dest.with_suffix(dest.suffix + ".part")
            part.write_bytes(data)
            part.replace(dest)
        if remote_url:
            with self._lock:
                urls = self._sources.setdefault(digest, [])
                if remote_url not in urls:
                    urls.append(remote_url)
                    self._save_index()
        return StoredAsset(
            sha256=digest,
            local_path=dest,
            mime_type=mime,
            width=width,
            height=height,
            byte_size=len(data),
        )

    def sources_for(self, digest: str) -> list[str]:
        return list(self._sources.get(digest, []))

    def exists(self, digest: str) -> bool:
        folder = self.root / digest[:2]
        if not folder.exists():
            return False
        return any(folder.glob(f"{digest}.*"))
