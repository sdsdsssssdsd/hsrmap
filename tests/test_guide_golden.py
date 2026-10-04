"""Golden 20 reports split metrics; hallucinated steps are fatal."""

from pathlib import Path

from hsrmap.guides.golden import run_golden
from hsrmap.guides.llm.fake import FakeGuideLLMProvider

CANARY = Path(__file__).parent / "fixtures" / "guides" / "canary"


def test_golden_20_split_metrics_with_fake():
    report = run_golden(CANARY, FakeGuideLLMProvider())
    assert report["article_relevance"]["ok"] == 20
    assert report["article_relevance"]["total"] == 20
    assert report["map_name_extraction"]["ok"] == 20
    assert report["section_segmentation"]["rate"] >= 0.95
    assert report["step_segmentation"]["rate"] >= 0.90
    assert report["image_assignment"]["rate"] >= 0.90
    assert report["hallucinated_steps"] == 0
    assert report["fatal_json_invalid"] == 0
