from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


PROMPT_VERSION = "article_extract_v1"


def cache_key(content_sha: str, prompt_version: str, model: str) -> str:
    return sha256(f"{content_sha}:{prompt_version}:{model}".encode()).hexdigest()


def extract_article(blocks: list[dict[str, Any]], client: Any, *, model: str = "deepseek-chat") -> dict[str, Any]:
    text = "\n".join(b.get("text") or "" for b in blocks if b.get("text"))
    content_sha = sha256(text.encode()).hexdigest()
    key = cache_key(content_sha, PROMPT_VERSION, model)
    cached = getattr(client, "cache", {}).get(key)
    if cached is not None:
        return cached
    payload = client.complete(text, prompt_version=PROMPT_VERSION, model=model)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or "sections" not in payload:
        raise ValueError("extract payload must be JSON object with sections")
    getattr(client, "cache", {})[key] = payload
    return payload
