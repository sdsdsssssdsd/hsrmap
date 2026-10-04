"""路径常量。

分两类（a1-8 四.2）：

* **源码 / 不可变参考数据**：跟随仓库位置（ROOT、phase1/ 下的 registry 与 golden），
  它们在 Git 里、按程序版本固定；
* **可变运行态**：跟随 hsrmap.runtime 解析出来的运行时根目录（DATA 及其派生）。

运行态常量是**惰性**的（模块级 __getattr__）：在本进程第一次访问时才解析，
这样 CLI 可以在 import 之前用 --data-dir / HSRMAP_DATA_DIR 决定根目录。
"""

from __future__ import annotations

from pathlib import Path

from hsrmap.runtime import (
    RuntimePaths,
    get_runtime,
    reset_runtime,
    resolve_runtime,
    user_data_dir,
)

#: 仓库根目录：只用于定位源码与不可变参考数据，不再决定可变状态写在哪里。
ROOT = Path(__file__).resolve().parents[1]
PHASE1 = ROOT / "phase1"
REGISTRY_PATH = PHASE1 / "endpoint_registry.json"
GOLDEN_PATH = PHASE1 / "calibration" / "points_normalized.json"
GOLDEN_GUIDES_PATH = PHASE1 / "calibration" / "golden_guides.json"

BASELINE = {
    "renderable_maps": 624,
    "labels": 1016,
}

#: 运行态常量 → 运行时根目录上的属性名（惰性求值，不缓存）。
_LAZY: dict[str, str] = {
    "DATA": "root",
    "ASSETS": "assets",
    "SNAPSHOTS": "snapshots",
    "STAGING": "staging",
    "ENRICHMENTS": "enrichments",
    "LOGS": "logs",
    "CURRENT_PATH": "current_json",
    "LOCK_PATH": "sync_lock",
    "USER_DB": "user_db",
    "GUIDE_DB": "guide_db",
    "GUIDE_PUBLISHED_DB": "published_db",
    "GUIDE_RAW": "guide_raw",
    "GUIDE_ASSETS": "guide_assets",
    "GUIDE_CACHE": "guide_cache",
    "GUIDE_DERIVED": "guide_derived",
    "CACHE_DIR": "cache",
    "LIVE_CACHE": "live_cache",
}

#: 需要再拼一层的派生目录。
_LAZY_SUBDIR: dict[str, str] = {
    "THUMB_DIR": "thumbnails",
    "TILE_DIR": "tiles",
}


def __getattr__(name: str) -> Path:
    """运行态路径：按需从运行时根目录算出来（PEP 562；不写缓存，换根即时生效）。"""
    runtime = get_runtime()
    attr = _LAZY.get(name)
    if attr is not None:
        return getattr(runtime, attr)
    subdir = _LAZY_SUBDIR.get(name)
    if subdir is not None:
        return runtime.cache / subdir
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def runtime_report() -> dict[str, str]:
    """给 hsrmap runtime 与报告用：当前根目录与来源。"""
    return get_runtime().as_report()


__all__ = [
    # 静态：源码位置与不可变参考数据
    "ROOT", "PHASE1", "REGISTRY_PATH", "GOLDEN_PATH", "GOLDEN_GUIDES_PATH", "BASELINE",
    # 运行态：由 PEP 562 惰性提供（名字见 _LAZY / _LAZY_SUBDIR），所以这里展开而不是字面量
    *_LAZY.keys(),
    *_LAZY_SUBDIR.keys(),
    # 运行时解析 API
    "RuntimePaths", "get_runtime", "resolve_runtime", "reset_runtime", "user_data_dir",
    "runtime_report",
]
