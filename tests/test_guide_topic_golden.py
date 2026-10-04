from pathlib import Path

from hsrmap.guides.golden import missing_topic_goldens, run_topic_golden
from hsrmap.guides.llm.fake import FakeGuideLLMProvider

CANARY = Path(__file__).parent / "fixtures" / "guides" / "canary"


class _RecordingProvider(FakeGuideLLMProvider):
    def __init__(self):
        self.topics: list[str] = []

    def extract_sections(self, blocks, page_id, topic):
        self.topics.append(topic)
        return super().extract_sections(blocks, page_id, topic)

    def classify_article(self, blocks):
        return {"relevant": True, "guide_type": "dream_ticker"}


def test_enabled_topics_register_golden_set():
    missing = missing_topic_goldens()
    assert missing == [], f"topics missing golden.json: {missing}"


def test_run_topic_golden_passes_topic_and_real_html():
    provider = _RecordingProvider()
    report = run_topic_golden("dream_ticker", CANARY, provider)
    assert provider.topics
    assert all(topic == "dream_ticker" for topic in provider.topics)
    assert report["hallucinated_steps"] == 0
    assert report["fatal_json_invalid"] == 0
    assert report["map_name_extraction"]["ok"] >= 15
    assert report["section_segmentation"]["ok"] >= 15
    bird = run_topic_golden("origami_bird", CANARY, FakeGuideLLMProvider())
    assert bird["map_name_extraction"]["ok"] >= 20
    assert bird["hallucinated_steps"] == 0
    for key, expect in (
        ("hanu", "黄金的时刻"),
        ("king_bucket", "朝露公馆"),
        ("dimensional_trotter", "鳞渊境"),
        ("jump", "二次元JUMP"),
    ):
        extra = run_topic_golden(key, CANARY, FakeGuideLLMProvider())
        assert extra["map_name_extraction"]["ok"] >= 1, key
        assert extra["hallucinated_steps"] == 0, key
        assert extra["section_segmentation"]["ok"] >= 1, key
