from __future__ import annotations

import base64
import json
import threading
from typing import Any
from urllib.request import Request, urlopen


class DeepSeekGuideLLMProvider:
    def __init__(self, config: dict[str, Any], api_key: str):
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY missing")
        self.config = config
        self._key = api_key

    def classify_article(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return self._json(blocks, self.config.get("text_model"), "article_classifier_v1")

    def extract_sections(self, blocks: list[dict[str, Any]], page_id: int, topic: str) -> dict[str, Any]:
        payload = self._json(_compact_blocks(blocks), self.config.get("text_model"), "section_extractor_v1")
        payload["page_id"] = page_id
        payload["topic"] = topic
        return payload

    def classify_image(
        self,
        hint: str = "",
        image_b64: str | None = None,
        *,
        image_bytes: bytes | None = None,
        mime: str = "image/png",
        sha256: str = "",
        **_kwargs,
    ) -> dict[str, Any]:
        blob = image_bytes
        encoded = image_b64
        if blob is None and encoded:
            blob = base64.b64decode(encoded)
        if blob is None:
            return {"role": "unknown", "confidence": 0.0}
        raw = self._vision_json(
            prompt_name="image_role_v2",
            image_bytes=blob,
            mime=mime,
            sha256=sha256,
            hint=hint,
            image_b64=base64.b64encode(blob).decode("ascii"),
        )
        return {"role": str(raw.get("role") or raw.get("label") or "unknown"), "confidence": float(raw.get("confidence") or 0)}

    def read_region(
        self,
        *,
        image_bytes: bytes,
        mime: str = "image/png",
        sha256: str = "",
        whitelist: list[str] | None = None,
        **_kwargs,
    ) -> dict[str, Any]:
        return self._vision_json(
            prompt_name="image_region_v1",
            image_bytes=image_bytes,
            mime=mime,
            sha256=sha256,
            hint=json.dumps({"whitelist": whitelist or []}, ensure_ascii=False),
        )

    def compare_scenes(self, guide_bytes: bytes, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        from hsrmap.guides.matching.scenes import build_scene_payload

        payload = build_scene_payload(guide_bytes, candidates)
        allowed = list(payload.get("allowed") or [])
        if not allowed:
            return {"candidate": None, "confidence": 0.0}
        hint = json.dumps(
            {"allowed": allowed, "instruction": "只比较攻略图与官方图是否同一处梦境迷钟。candidate 必须来自 allowed，否则 null。"},
            ensure_ascii=False,
        )
        raw = self._vision_json_parts(prompt_name="dream_ticker_scene_v1", payload=payload, hint=hint)
        cand = str(raw.get("candidate") or raw.get("source_point_id") or "")
        if cand not in allowed:
            return {"candidate": None, "confidence": 0.0}
        return {"candidate": cand, "confidence": float(raw.get("confidence") or 0)}

    def compare_candidates(self, unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        allowed = [str(item.get("source_point_id")) for item in candidates]
        payload = self._json(
            [{"type": "paragraph", "text": json.dumps({"unit": unit, "candidates": allowed}, ensure_ascii=False)}],
            self.config.get("text_model"),
            "point_match_assist_v1",
        )
        cand = str(payload.get("candidate") or "")
        if cand not in allowed:
            payload["candidate"] = None
            payload["confidence"] = 0
        return payload

    def _vision_json(
        self,
        *,
        prompt_name: str,
        image_bytes: bytes,
        mime: str,
        sha256: str,
        hint: str = "",
        image_b64: str | None = None,
    ) -> dict[str, Any]:
        from pathlib import Path

        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / f"{prompt_name}.txt"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else prompt_name
        image_b64 = image_b64 or base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{image_b64}"
        body = json.dumps(
            {
                "model": (self.config.get("vision_model") if self.config.get("vision_model") not in {None, "", "deepseek-vision"} else None) or "deepseek-flash",
                "messages": [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": hint or sha256},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                        ],
                    },
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = Request(
            self.config["endpoint"],
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
            method="POST",
        )
        raw = _urlopen_json(req, timeout_sec=150)
        content = raw["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
        parsed["image_b64"] = image_b64
        return parsed

    def _vision_json_parts(self, *, prompt_name: str, payload: dict[str, Any], hint: str = "") -> dict[str, Any]:
        from pathlib import Path

        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / f"{prompt_name}.txt"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else prompt_name
        content: list[dict[str, Any]] = [{"type": "text", "text": hint}]
        for part in payload.get("parts") or []:
            blob = part.get("bytes") or b""
            if not blob:
                continue
            label = "GUIDE" if part.get("role") == "guide" else f"OFFICIAL:{part.get('source_point_id')}"
            data_url = f"data:image/jpeg;base64,{base64.b64encode(blob).decode('ascii')}"
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "low"}})
        body = json.dumps(
            {
                "model": (self.config.get("vision_model") if self.config.get("vision_model") not in {None, "", "deepseek-vision"} else None) or "deepseek-flash",
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = Request(
            self.config["endpoint"],
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
            method="POST",
        )
        raw = _urlopen_json(req, timeout_sec=150)
        content_text = raw["choices"][0]["message"]["content"]
        return json.loads(content_text) if isinstance(content_text, str) else content_text

    def assist_point_match(self, section: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        allowed = [str(item.get("source_point_id")) for item in candidates]
        payload = self._json(
            [{"type": "paragraph", "text": json.dumps({"section": section, "candidates": allowed}, ensure_ascii=False)}],
            self.config.get("text_model"),
            "point_match_assist_v1",
        )
        cand = str(payload.get("candidate") or "")
        if cand not in allowed:
            payload["candidate"] = None
            payload["confidence"] = 0
        return payload

    def _json(self, blocks: list[dict[str, Any]], model: str, prompt_name: str) -> dict[str, Any]:
        from pathlib import Path

        prompt = (Path(__file__).resolve().parents[1] / "prompts" / f"{prompt_name}.txt").read_text(encoding="utf-8")
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(blocks, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = Request(
            self.config["endpoint"],
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
            method="POST",
        )
        raw = _urlopen_json(req, timeout_sec=150)
        content = raw["choices"][0]["message"]["content"]
        return json.loads(content) if isinstance(content, str) else content


def _urlopen_json(req: Request, timeout_sec: int = 60) -> dict[str, Any]:
    holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            with urlopen(req, timeout=timeout_sec) as resp:
                holder["raw"] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — surface to caller
            holder["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout_sec + 2)
    if worker.is_alive():
        raise TimeoutError("deepseek_timeout")
    if "error" in holder:
        raise holder["error"]
    return holder["raw"]


def _compact_blocks(blocks: list[dict[str, Any]], limit: int = 3500) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    size = 0
    for block in blocks:
        if block.get("type") not in {"heading", "paragraph", "list", "image"}:
            continue
        item = {key: block[key] for key in ("id", "type", "text", "src") if key in block and block[key]}
        chunk = json.dumps(item, ensure_ascii=False)
        if size + len(chunk) > limit:
            break
        kept.append(item)
        size += len(chunk)
    return kept
