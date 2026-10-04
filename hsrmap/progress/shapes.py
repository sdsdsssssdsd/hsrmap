"""结构指纹（a1-9 §18「API schema drift 可检测」）。

只记**形状**，不记**值**：

```text
data.point_status.list[].point_id:str
data.point_status.list[].status:int
```

这样一份基线（`phase1/progress-shapes.json`）可以安全进仓库：它能回答「官方接口有没有悄悄改结构」，
但里面一个 UID、一个点位状态值都没有。`progress drift` 拿新探针报告和基线比，缺字段/多字段都算漂移。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

#: 最多展开几层；再深就是噪声，也比不过「多了个字段」这件事本身。
MAX_DEPTH = 3
#: 每层最多记多少个键（数组按第一个元素的形状记，标记为 []）。
MAX_KEYS = 200

#: 仓库里的基线（探针跑过之后用 `--record` 写入）。
DEFAULT_BASELINE = Path("phase1") / "progress-shapes.json"


def shape_of(payload: Any, *, prefix: str = "", depth: int = MAX_DEPTH) -> list[str]:
    out: list[str] = []
    if isinstance(payload, Mapping):
        for index, key in enumerate(sorted(payload, key=str)):
            if index >= MAX_KEYS:
                out.append(f"{prefix}…（键太多，已截断）")
                break
            value = payload[key]
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(f"{path}:{type(value).__name__}")
            if depth > 0:
                out.extend(shape_of(value, prefix=path, depth=depth - 1))
    elif isinstance(payload, (list, tuple)):
        #: 只写「这里有数组」，不写长度 —— 长度会随地图/角色数变化，写进去就成了假漂移。
        out.append(f"{prefix}[]")
        #: 数组对深度是「透明」的：不然 `list[].point_id` 这种最该看见的字段反而会被截掉。
        if payload and depth >= 0:
            out.extend(shape_of(payload[0], prefix=f"{prefix}[]", depth=depth))
    return out


def fingerprint(payload: Any) -> str:
    return hashlib.sha256("\n".join(shape_of(payload)).encode("utf-8")).hexdigest()[:16]


def shape_entry(payload: Any) -> dict[str, Any]:
    """一个端点的形状条目：指纹 + 字段清单（报告与基线里存的就是它）。"""
    return {"fingerprint": fingerprint(payload), "fields": shape_of(payload)}


def compare_shape(expected: Iterable[str], actual: Iterable[str]) -> dict[str, Any]:
    """基线形状 vs 现在的形状：少字段（接口改坏了）和多字段（接口悄悄扩了）都要报出来。"""
    want, got = set(expected), set(actual)
    missing = sorted(want - got)
    added = sorted(got - want)
    return {"drift": bool(missing or added), "missing": missing[:20], "added": added[:20],
            "missing_count": len(missing), "added_count": len(added)}


def shapes_from_report(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """从探针报告里取出每个端点的形状（报告里只存形状与指纹）。"""
    return {
        str(name): {"fingerprint": str(item.get("fingerprint") or ""),
                    "fields": list(item.get("fields") or [])}
        for name, item in (report.get("shapes") or {}).items()
    }


def load_baseline(path: Path | str = DEFAULT_BASELINE) -> dict[str, dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in (data.get("endpoints") or {}).items()}


def save_baseline(path: Path | str, shapes: Mapping[str, Mapping[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "version": 1,
        "note": "只记字段与类型，不记任何返回值；由 python -m hsrmap progress drift --record 写入",
        "endpoints": {name: {"fingerprint": str(item.get("fingerprint") or ""),
                             "fields": sorted(str(f) for f in (item.get("fields") or []))}
                      for name, item in sorted(shapes.items())},
    }
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def drift_report(
    report: Mapping[str, Any],
    *,
    baseline: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """新报告的每个端点 vs 基线：没见过的端点也算漂移（接口表变了）。"""
    base = dict(baseline or {})
    current = shapes_from_report(report)
    rows: list[dict[str, Any]] = []
    for name in sorted(set(base) | set(current)):
        want = base.get(name)
        now = current.get(name)
        if want is None:
            rows.append({"endpoint": name, "drift": True, "reason": "基线里没有这个端点",
                         "fingerprint": (now or {}).get("fingerprint", "")})
            continue
        if now is None:
            rows.append({"endpoint": name, "drift": True, "reason": "这次探针没有这个端点",
                         "fingerprint": str(want.get("fingerprint") or "")})
            continue
        diff = compare_shape(want.get("fields") or [], now.get("fields") or [])
        rows.append({"endpoint": name, "drift": bool(diff["drift"]),
                     "fingerprint": str(now.get("fingerprint") or ""),
                     "baseline_fingerprint": str(want.get("fingerprint") or ""),
                     "reason": "" if not diff["drift"] else "字段形状变了", **diff})
    drifted = [row for row in rows if row["drift"]]
    return {
        "baseline": len(base),
        "checked": len(current),
        "drift": bool(drifted) if base else False,
        "drifted": drifted,
        "endpoints": rows,
        "note": "" if base else "没有基线：先跑到真实账号的探针报告，再 --record 一次",
    }
