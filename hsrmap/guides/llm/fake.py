from __future__ import annotations

from typing import Any


from hsrmap.guides.semantic.classifier import classify_topics


class FakeGuideLLMProvider:
    def classify_article(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(b.get("text") or "" for b in blocks)
        classified = classify_topics(blocks)
        topics = list(classified.get("topics") or [])
        if topics:
            return {"relevant": True, "guide_type": topics[0]["topic_key"], "topics": topics}
        relevant = any(token in text for token in ("浮脂", "海原", "渡画", "珠星", "指针", "妖都", "坠星", "二维"))
        return {"relevant": relevant, "guide_type": "floating_grease" if relevant else "other"}

    def extract_sections(self, blocks: list[dict[str, Any]], page_id: int, topic: str) -> dict[str, Any]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        ordinal = 0
        for block in blocks:
            if block.get("type") == "heading":
                text = block.get("text") or ""
                if current:
                    sections.append(current)
                ordinal += 1
                current = {
                    "map_name": text,
                    "ordinal": ordinal,
                    "location_text": "",
                    "steps": [],
                    "image_block_ids": [],
                }
                continue
            if current is None:
                continue
            if block.get("type") in {"paragraph", "list"} and block.get("text"):
                if not current["location_text"]:
                    current["location_text"] = block["text"]
                current["steps"].append({"text_original": block["text"]})
            if block.get("type") == "image" and block.get("id"):
                current["image_block_ids"].append(block["id"])
        if current:
            sections.append(current)
        return {"page_id": page_id, "topic": topic, "version": None, "sections": sections}

    def classify_image(self, hint: str = "", image_b64: str | None = None, **kwargs) -> dict[str, Any]:
        text = " ".join([hint, str(kwargs.get("alt") or ""), str(kwargs.get("src") or "")])
        if any(token in text for token in ("广告", "二维码", "APP", "wx.webp", "自拍")):
            return {"role": "unrelated", "confidence": 1.0}
        if "定位" in text or "location" in text or "地图" in text:
            return {"role": "location_map", "confidence": 0.9}
        if "步骤" in text or "机关" in text or "解密" in text:
            return {"role": "puzzle_step", "confidence": 0.9}
        if "海原" in text or "浮脂" in text:
            return {"role": "puzzle_step", "confidence": 0.8}
        return {"role": "unknown", "confidence": 0.2}

    def read_region(self, **kwargs) -> dict[str, Any]:
        text = " ".join(str(kwargs.get(key) or "") for key in ("hint", "alt", "src", "nearby"))
        name = None
        for token in ("海原电视塔", "海原市"):
            if token in text:
                name = token
                break
        if "4.2" in text and name is None:
            name = None
        from hsrmap.guides.matching.spatial import parse_spatial_anchor

        return {
            "map_name_raw": name,
            "visible_text": [name] if name else [],
            "article_ordinal": 1 if name else None,
            "spatial_anchor": parse_spatial_anchor(text),
            "instruction_text": [],
            "grounded": bool(name),
        }

    def compare_scenes(self, guide_bytes: bytes, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {"candidate": None, "confidence": 0.0}

    def compare_candidates(self, unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return self.assist_point_match(unit, candidates)

    def assist_point_match(self, section: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        if not candidates:
            return {"candidate": None, "confidence": 0.0}
        pick = candidates[0]
        return {"candidate": pick.get("source_point_id"), "confidence": 0.75, "allowed": [c.get("source_point_id") for c in candidates]}
