from __future__ import annotations


def path_allowed(robots_txt: str, path: str, user_agent: str = "*") -> bool:
    allowed = True
    applies = False
    current_agents: list[str] = []
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        key_l = key.lower()
        if key_l == "user-agent":
            current_agents = [value.lower()]
            applies = value == "*" or value.lower() == user_agent.lower()
            continue
        if not applies and "*" not in current_agents:
            continue
        if key_l == "disallow":
            if value and path.startswith(value):
                allowed = False
        elif key_l == "allow":
            if value and path.startswith(value):
                allowed = True
    return allowed
