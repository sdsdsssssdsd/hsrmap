from __future__ import annotations

import hashlib
import json
from typing import Any


def request_key(endpoint_name: str, params: dict[str, Any], app_version: str) -> str:
    canonical = json.dumps(
        {str(k): params[k] for k in sorted(params)},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    payload = f"{endpoint_name}\n{canonical}\n{app_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
