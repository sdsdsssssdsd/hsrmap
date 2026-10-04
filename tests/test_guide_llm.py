"""DeepSeek extract uses injected client and caches by content+prompt+model."""

from hsrmap.guides.llm.extract import PROMPT_VERSION, cache_key, extract_article
from hsrmap.guides.llm.vision import classify_image


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.cache = {}
        self.calls = 0

    def complete(self, text, prompt_version, model):
        self.calls += 1
        return self.payload


def test_extract_article_requires_sections_and_caches():
    blocks = [{"type": "heading", "text": "海原市"}, {"type": "paragraph", "text": "先转再点"}]
    payload = {"relevant": True, "guide_type": "floating_grease", "sections": [{"map": "海原市", "title": "海原市", "steps": [{"text": "先转再点"}]}]}
    client = FakeLLM(payload)
    first = extract_article(blocks, client)
    second = extract_article(blocks, client)
    assert first["sections"][0]["map"] == "海原市"
    assert client.calls == 1
    assert cache_key("x", PROMPT_VERSION, "deepseek-chat")


def test_vision_classifies_from_fake_client():
    client = FakeLLM("puzzle_step")
    assert classify_image("旋转机关截图", client) == "puzzle_step"
