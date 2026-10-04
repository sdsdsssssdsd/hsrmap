from __future__ import annotations

_ALIASES = {
    "location_map": "LOCATION_MAP",
    "route_map": "LOCATION_MAP",
    "puzzle_step": "PUZZLE_STEP",
    "result": "RESULT",
    "reward": "RESULT",
    "cover": "COVER",
    "advertisement": "ADVERTISEMENT",
    "unrelated": "UNRELATED",
    "unknown": "UNRELATED",
}
WASTE = {"COVER", "ADVERTISEMENT", "UNRELATED"}
BIND_EVIDENCE = {"LOCATION_MAP", "PUZZLE_STEP", "RESULT"}


def normalize_role(role: str | None) -> str:
    text = str(role or "").strip()
    if text in _ALIASES.values():
        return text
    return _ALIASES.get(text.lower(), "UNRELATED")


def is_bind_evidence(role: str | None) -> bool:
    return normalize_role(role) in BIND_EVIDENCE
