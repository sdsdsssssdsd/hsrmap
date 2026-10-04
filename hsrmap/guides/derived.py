from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DerivedGuideStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _page(self, page_id: int) -> Path:
        path = self.root / str(page_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load(self, page_id: int, name: str) -> dict[str, Any] | None:
        path = self._page(page_id) / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, page_id: int, name: str, payload: dict[str, Any]) -> Path:
        path = self._page(page_id) / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_section_extraction(self, page_id: int, payload: dict[str, Any]) -> Path:
        return self.save(page_id, "section-extraction.json", payload)

    def save_article_classification(self, page_id: int, payload: dict[str, Any]) -> Path:
        return self.save(page_id, "article-classification.json", payload)

    def save_image_classification(self, page_id: int, payload: dict[str, Any]) -> Path:
        return self.save(page_id, "image-classification.json", payload)

    def save_match_assist(self, page_id: int, payload: dict[str, Any]) -> Path:
        return self.save(page_id, "match-assist.json", payload)
