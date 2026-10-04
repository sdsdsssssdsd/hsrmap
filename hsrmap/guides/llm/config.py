from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def load_env_file(path: Path | None = None) -> None:
    target = Path(path) if path else ROOT / ".env"
    if not target.exists():
        return
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_llm_config(path: Path | None = None) -> dict[str, Any]:
    load_env_file()
    target = path or Path(__file__).resolve().parents[1] / "config.yaml"
    data: dict[str, Any] = {"provider": "fake", "text_model": "deepseek-chat", "vision_model": "deepseek-vision", "endpoint": "https://api.deepseek.com/chat/completions"}
    section = None
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "llm:":
            section = "llm"
            continue
        if section == "llm" and ":" in line:
            key, value = [part.strip() for part in line.split(":", 1)]
            if value:
                data[key] = value
    return data
