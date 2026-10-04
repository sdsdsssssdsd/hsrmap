"""Fake and DeepSeek share one interface; tests never call the network."""

import os

from hsrmap.guides.derived import DerivedGuideStore
from hsrmap.guides.llm.config import load_llm_config
from hsrmap.guides.llm.fake import FakeGuideLLMProvider
from hsrmap.guides.llm.provider import build_provider, hallucinated_steps


def test_fake_extract_stays_inside_blocks_and_writes_derived(tmp_path):
    blocks = [
        {"id": "b1", "type": "heading", "text": "海原市"},
        {"id": "b2", "type": "paragraph", "text": "开局使用Q技能对准旋转机关。"},
        {"id": "b3", "type": "image", "asset": "aaa", "src": "https://cdn.example.test/haiyuan-1.png"},
    ]
    provider = FakeGuideLLMProvider()
    extracted = provider.extract_sections(blocks, page_id=17, topic="floating_grease")
    assert extracted["page_id"] == 17
    assert extracted["sections"][0]["map_name"] == "海原市"
    assert extracted["sections"][0]["steps"][0]["text_original"] == "开局使用Q技能对准旋转机关。"
    assert extracted["sections"][0]["image_block_ids"] == ["b3"]
    assert hallucinated_steps(extracted, blocks) == []
    store = DerivedGuideStore(tmp_path / "derived")
    path = store.save_section_extraction(17, extracted)
    assert path.exists()
    raw = (tmp_path / "not-raw.html")
    assert "raw" not in str(path)


def test_env_file_loads_key_without_writing_config(tmp_path, monkeypatch):
    from hsrmap.guides.llm.config import load_env_file

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test-local\n", encoding="utf-8")
    load_env_file(env)
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-local"
    cfg = load_llm_config()
    assert "sk-test-local" not in str(cfg.values())


def test_build_provider_fake_by_default_and_key_not_required():
    cfg = load_llm_config()
    assert "text_model" in cfg
    provider = build_provider("fake")
    assert provider.classify_article([{"type": "heading", "text": "海原市"}])["relevant"] is True
    assert os.environ.get("DEEPSEEK_API_KEY") or build_provider("deepseek", missing_ok=True) is None


def test_fake_classify_article_uses_topic_registry():
    provider = FakeGuideLLMProvider()
    ticker = provider.classify_article([{"type": "heading", "text": "筑梦边境梦境迷钟解密"}])
    assert ticker["relevant"] is True
    assert ticker["guide_type"] == "dream_ticker"
    bird = provider.classify_article([{"type": "heading", "text": "黄金的时刻折纸小鸟"}])
    assert bird["relevant"] is True
    assert bird["guide_type"] == "origami_bird"
