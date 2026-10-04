from hsrmap.guides.layout.planner import plan_layout
from hsrmap.guides.layout.render import render_layout


def test_puzzle_renderer_numbers_steps():
    layout = plan_layout(
        {
            "topic_key": "dream_ticker",
            "target_type": "POINT",
            "steps": [{"text": "调黄块", "images": ["a"]}, {"text": "拨指针", "images": []}],
        }
    )
    html = render_layout(layout)
    assert layout["profile"] == "puzzle_steps_v1"
    assert "步骤 1" in html
    assert "调黄块" in html
    assert "拨指针" in html


def test_collection_renderer_is_route_not_per_point():
    layout = plan_layout(
        {
            "topic_key": "origami_bird",
            "target_type": "MAP_LABEL",
            "target_key": "map:508:topic:origami_bird",
            "steps": [{"text": "第1只在喷泉"}, {"text": "第2只在台阶"}],
        }
    )
    html = render_layout(layout)
    assert layout["profile"] == "collection_route_v1"
    assert "路线" in html
    assert "第1只" in html
    assert "POINT" not in html


def test_challenge_renderer_uses_challenge_profile():
    layout = plan_layout(
        {
            "topic_key": "jump",
            "steps": [{"text": "起跳后二段跳"}],
        }
    )
    html = render_layout(layout)
    assert layout["profile"] == "challenge_v1"
    assert "挑战" in html
    assert "起跳后二段跳" in html
