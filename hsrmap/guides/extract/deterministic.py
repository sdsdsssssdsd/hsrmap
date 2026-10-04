from __future__ import annotations

from typing import Any

from hsrmap.guides.llm.fake import FakeGuideLLMProvider


class DeterministicGuideExtractor(FakeGuideLLMProvider):
    """Heading/paragraph/image split only. No model, no invented steps."""

    name = "deterministic"


def extract_sections(blocks: list[dict[str, Any]], page_id: int, topic: str) -> dict[str, Any]:
    return DeterministicGuideExtractor().extract_sections(blocks, page_id, topic)
