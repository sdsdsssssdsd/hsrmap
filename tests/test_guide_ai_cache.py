"""Unified AI cache: one key per answer, prompt_version included (a1-6 §29)."""

import json

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ai_cache import AICache, CachedProvider, cache_key, normalize_input


def test_key_covers_provider_model_prompt_version_and_input():
    payload = {"blocks": ["第一步 转"], "page_id": 7}
    base = cache_key("fake", "m1", "v1", payload)
    assert base == cache_key("fake", "m1", "v1", {"page_id": 7, "blocks": ["第一步 转"]})
    assert base != cache_key("deepseek", "m1", "v1", payload)
    assert base != cache_key("fake", "m2", "v1", payload)
    assert base != cache_key("fake", "m1", "v2", payload)  # prompt changes the key
    assert base != cache_key("fake", "m1", "v1", {"blocks": ["第一步 转"], "page_id": 8})
    assert len(base) == 64


def test_bytes_are_keyed_by_hash_and_json_is_canonical():
    left = cache_key("p", "m", "v1", {"image": b"\x89PNG\r\n", "hint": "x"})
    right = cache_key("p", "m", "v1", {"hint": "x", "image": b"\x89PNG\r\n"})
    other = cache_key("p", "m", "v1", {"image": b"\x89PNG\r\n\x00", "hint": "x"})
    assert left == right and left != other
    assert normalize_input({"a": 1, "b": [2, 3]}) == normalize_input({"b": [2, 3], "a": 1})


def test_cache_persists_in_the_database_across_instances(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    first = AICache(db=db)
    first.put("fake", "m1", "v1", {"text": "a"}, {"sections": []})
    second = AICache(db=db)  # a new object, as a new process would build
    assert second.get("fake", "m1", "v1", {"text": "a"}) == {"sections": []}
    assert second.get("fake", "m1", "v2", {"text": "a"}) is None
    assert second.get("fake", "m1", "v1", {"text": "b"}) is None
    stats = second.stats()
    assert stats["entries"] == 1 and stats["hits"] == 1
    assert stats["by_prompt_version"] == {"v1": 1}
    db.close()


def test_disk_cache_works_without_a_database(tmp_path):
    root = tmp_path / "ai-cache"
    AICache(root=root).put("fake", "m1", "v1", {"text": "a"}, [1, 2, 3])
    assert list(root.rglob("*.json"))
    assert AICache(root=root).get("fake", "m1", "v1", {"text": "a"}) == [1, 2, 3]
    assert AICache(root=root).stats()["storage"] == "memory"


def test_invalidate_drops_one_prompt_version(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    cache = AICache(db=db)
    cache.put("fake", "m1", "v1", {"text": "a"}, {"v": 1})
    cache.put("fake", "m1", "v2", {"text": "a"}, {"v": 2})
    assert cache.invalidate(prompt_version="v1") == 1
    assert cache.get("fake", "m1", "v1", {"text": "a"}) is None
    assert cache.get("fake", "m1", "v2", {"text": "a"}) == {"v": 2}
    assert cache.invalidate() == 1
    assert cache.stats()["entries"] == 0
    db.close()


class _CountingProvider:
    """A provider that counts how often the model was really asked."""

    model = "counting-1"
    prompt_version = "reader_v3"

    def __init__(self):
        self.calls = 0

    def read_region(self, **kwargs):
        self.calls += 1
        return {"map_name_raw": "1层", "call": self.calls}

    def extract_sections(self, blocks, page_id, topic):
        self.calls += 1
        return {"sections": [{"page_id": page_id, "topic": topic}]}


def test_cached_provider_asks_the_model_once(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    inner = _CountingProvider()
    provider = CachedProvider(inner, AICache(db=db))
    first = provider.read_region(image_b64="AAAA", hint="读图")
    second = provider.read_region(image_b64="AAAA", hint="读图")
    assert first == second == {"map_name_raw": "1层", "call": 1}
    assert inner.calls == 1
    assert provider.calls == 1 and provider.hits == 1
    assert provider.describe()["prompt_version"] == "reader_v3"

    # a different prompt version must not reuse the old answer
    provider.read_region(image_b64="AAAA", hint="读图", prompt_version="reader_v4")
    assert inner.calls == 2
    # a different image is a different key as well
    provider.read_region(image_b64="BBBB", hint="读图")
    assert inner.calls == 3
    assert provider.calls == 3 and provider.hits == 1
    db.close()


def test_build_provider_wraps_when_a_cache_target_is_given(tmp_path):
    from hsrmap.guides.llm.provider import build_provider

    db = GuideDatabase(tmp_path / "guide.db")
    provider = build_provider("deterministic", db=db)
    assert isinstance(provider, CachedProvider)
    blocks = [{"id": "b1", "type": "heading", "text": "第1个目标"}]
    first = provider.extract_sections(blocks, page_id=1, topic="origami_bird")
    second = provider.extract_sections(blocks, page_id=1, topic="origami_bird")
    assert first == second
    assert provider.calls == 1 and provider.hits == 1
    assert build_provider("deterministic") is not None
    db.close()


def test_cli_ai_cache_reports_and_writes_json(tmp_path, capsys):
    db_path = tmp_path / "guide.db"
    db = GuideDatabase(db_path)
    AICache(db=db).put("deepseek", "m1", "article_extract_v1", {"text": "a"}, {"sections": []})
    db.close()
    out = tmp_path / "ai-cache.json"
    assert main(["guides", "ai-cache", "--db", str(db_path), "--json", str(out)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["stats"]["entries"] == 1
    assert body["stats"]["by_prompt_version"] == {"article_extract_v1": 1}
    assert json.loads(out.read_text(encoding="utf-8"))["stats"]["entries"] == 1
    assert main(["guides", "ai-cache", "--db", str(db_path), "--invalidate-prompt", "article_extract_v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["invalidated"] == 1 and payload["stats"]["entries"] == 0
