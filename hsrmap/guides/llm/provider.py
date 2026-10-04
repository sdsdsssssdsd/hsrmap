from __future__ import annotations

import os
from typing import Any, Protocol


class GuideLLMProvider(Protocol):
    def classify_article(self, blocks: list[dict[str, Any]]) -> dict[str, Any]: ...
    def extract_sections(self, blocks: list[dict[str, Any]], page_id: int, topic: str) -> dict[str, Any]: ...
    def classify_image(self, hint: str = "", image_b64: str | None = None, **kwargs) -> Any: ...
    def read_region(self, **kwargs) -> dict[str, Any]: ...
    def compare_candidates(self, unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]: ...
    def assist_point_match(self, section: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]: ...


def hallucinated_steps(extracted: dict[str, Any], blocks: list[dict[str, Any]]) -> list[str]:
    corpus = " ".join(b.get("text") or "" for b in blocks if b.get("text"))
    bad = []
    for section in extracted.get("sections") or []:
        for step in section.get("steps") or []:
            text = (step.get("text_original") or step.get("text") or "").strip()
            if text and text not in corpus:
                bad.append(text)
    return bad


def build_provider(
    name: str | None = None,
    *,
    missing_ok: bool = False,
    cache: Any = None,
    db: Any = None,
    cache_root: Any = None,
):
    """Build a provider, optionally behind the unified AI cache (a1-6 §29)."""
    from hsrmap.guides.llm.config import load_llm_config
    from hsrmap.guides.llm.fake import FakeGuideLLMProvider

    cfg = load_llm_config()
    chosen = (name or cfg.get("provider") or "fake").lower()
    provider = None
    if chosen in {"fake", "deterministic"}:
        if chosen == "deterministic":
            from hsrmap.guides.extract.deterministic import DeterministicGuideExtractor

            provider = DeterministicGuideExtractor()
        else:
            provider = FakeGuideLLMProvider()
    elif chosen == "deepseek":
        from hsrmap.guides.llm.config import load_env_file

        load_env_file()
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            if missing_ok:
                return None
            raise RuntimeError("DEEPSEEK_API_KEY missing")
        from hsrmap.guides.llm.deepseek import DeepSeekGuideLLMProvider

        provider = DeepSeekGuideLLMProvider(cfg, key)
    else:
        raise ValueError(f"unknown provider {chosen}")
    if cache is None and (db is not None or cache_root is not None):
        from hsrmap.guides.ai_cache import wrap_provider

        cache = wrap_provider(provider, db=db, root=cache_root, name=chosen)
        return cache
    if cache is not None:
        from hsrmap.guides.ai_cache import CachedProvider

        return CachedProvider(provider, cache, name=chosen)
    return provider
