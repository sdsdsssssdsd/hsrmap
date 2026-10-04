from hsrmap.guides.semantic.classifier import classify_topics


def test_classify_article_can_return_multiple_registry_topics():
    out = classify_topics(
        [
            {"type": "heading", "text": "黄金的时刻折纸小鸟"},
            {"type": "paragraph", "text": "第1个【梦境迷钟】修复解密"},
        ]
    )
    keys = {item["topic_key"] for item in out["topics"]}
    assert "origami_bird" in keys
    assert "dream_ticker" in keys
    assert all(item["topic_key"] for item in out["topics"])
