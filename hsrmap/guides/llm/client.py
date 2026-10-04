from __future__ import annotations

from typing import Any


class FakeLLMClient:
    def __init__(self, payload: Any):
        self.payload = payload
        self.cache: dict[str, Any] = {}
        self.calls = 0

    def complete(self, text: str, prompt_version: str, model: str) -> Any:
        self.calls += 1
        return self.payload
