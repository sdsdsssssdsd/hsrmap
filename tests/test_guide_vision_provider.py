"""Vision provider must send image bytes. Tests never hit the network."""

from hsrmap.guides.llm.deepseek import DeepSeekGuideLLMProvider


def test_classify_image_includes_data_url(monkeypatch):
    seen: dict = {}

    def fake_vision(self, **kwargs):
        seen.update(kwargs)
        return {"role": "puzzle_step", "confidence": 0.9}

    monkeypatch.setattr(DeepSeekGuideLLMProvider, "_vision_json", fake_vision)
    provider = DeepSeekGuideLLMProvider(
        {"endpoint": "https://example.invalid", "vision_model": "deepseek-flash"},
        "k",
    )
    out = provider.classify_image(image_bytes=b"png-bytes", mime="image/png", sha256="aa")
    assert seen.get("image_b64")
    assert b"png-bytes" in __import__("base64").b64decode(seen["image_b64"])
    assert out["role"] == "puzzle_step"


def test_classify_image_without_bytes_is_unknown():
    provider = DeepSeekGuideLLMProvider(
        {"endpoint": "https://example.invalid", "vision_model": "deepseek-flash"},
        "k",
    )
    assert provider.classify_image()["role"] == "unknown"
