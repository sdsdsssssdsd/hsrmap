from __future__ import annotations

import hashlib
from typing import Any


def extract_paths(obj: Any, prefix: str = "$") -> list[str]:
    paths: set[str] = set()

    def walk(value: Any, path: str) -> None:
        if path != "$":
            paths.add(path)
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for child in value:
                walk(child, f"{path}[]")

    walk(obj, prefix)
    return sorted(paths)


def fingerprint_schema(obj: Any) -> str:
    text = "\n".join(extract_paths(obj))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
