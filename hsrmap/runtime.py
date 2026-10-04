"""运行时目录（a1-8 四.2）：Git checkout 不再是可变状态的根目录。

项目里有四种东西，之前混在一个 data/ 里：

* 源码（hsrmap/、tests/、web/src/）—— 属于 Git；
* 不可变参考数据（phase1/calibration、endpoint_registry）—— 属于 Git，量小；
* 可变运行态（guide.db、snapshots、raw、assets、cache、logs、staging、reports）—— 不属于 Git；
* 发布物（published.db、release 包）—— 由发布流程生成。

这个模块只负责第三、四类的**解析**：给定显式参数 / 环境变量 / 仓库 / 用户数据目录，
算出唯一一个运行时根目录。它**不创建任何目录**，也不改环境；
真正写文件的动作由创建型 API（GuideDatabase.create、RawGuideStore…）负责。

解析顺序（先命中先赢）：

1. 显式传入（CLI 的 --data-dir）；
2. 环境变量 HSRMAP_DATA_DIR；
3. 仓库里的 repo/data（**兼容窗口**：老 checkout 已经把数据放这里，继续用，
   这样卫生化不会把现有项目打断）；
4. 操作系统用户数据目录（Windows: LOCALAPPDATA/hsrmap，否则 ~/.local/share/hsrmap）——
   clean checkout 走这条，因此默认不会在仓库里建出 data/。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

#: 指向运行时根目录的环境变量。
ENV_DATA_DIR = "HSRMAP_DATA_DIR"

#: 用户数据目录下的应用名。
APP_DIR_NAME = "hsrmap"


@dataclass(frozen=True)
class RuntimePaths:
    """运行时根目录 + 由它派生的目录（全部是纯计算，不建目录）。"""

    root: Path
    #: 这个根是怎么来的：explicit / env / repo / user（诊断与报告用）。
    source: str

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def enrichments(self) -> Path:
        return self.root / "enrichments"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def live_cache(self) -> Path:
        return self.root / "live-cache"

    @property
    def assets(self) -> Path:
        return self.root / "assets" / "sha256"

    @property
    def user_db(self) -> Path:
        return self.root / "user.db"

    @property
    def current_json(self) -> Path:
        return self.root / "current.json"

    @property
    def guides(self) -> Path:
        return self.root / "guides"

    @property
    def guide_db(self) -> Path:
        return self.guides / "guide.db"

    @property
    def published_db(self) -> Path:
        return self.guides / "published.db"

    @property
    def guide_raw(self) -> Path:
        return self.guides

    @property
    def guide_derived(self) -> Path:
        return self.guides / "derived"

    @property
    def guide_reports(self) -> Path:
        return self.guides / "reports"

    @property
    def guide_assets(self) -> Path:
        return self.root / "guide-assets" / "sha256"

    @property
    def guide_cache(self) -> Path:
        return self.root / "guide-cache"

    @property
    def sync_lock(self) -> Path:
        return self.staging / "sync.lock"

    def as_report(self) -> dict[str, str]:
        return {"root": str(self.root), "source": self.source, "guide_db": str(self.guide_db),
                "published_db": str(self.published_db), "logs": str(self.logs),
                "assets": str(self.assets)}


def user_data_dir(app: str = APP_DIR_NAME) -> Path:
    """操作系统层面的用户数据目录（不创建）。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / app
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / app
    return Path.home() / ".local" / "share" / app


def repo_data_dir(repo_root: Path | None = None) -> Path:
    """仓库里的 data/（兼容窗口用；也是 tests 默认想避开的那个目录）。"""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    return root / "data"


def resolve_runtime(
    explicit: str | os.PathLike[str] | None = None,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """按 显式 → 环境变量 → 仓库 data/ → 用户数据目录 的顺序解析（只解析，不建目录）。"""
    env_map: Mapping[str, str] = os.environ if env is None else env
    if explicit not in (None, ""):
        return RuntimePaths(Path(explicit).expanduser().resolve(), "explicit")
    from_env = str(env_map.get(ENV_DATA_DIR) or "").strip()
    if from_env:
        return RuntimePaths(Path(from_env).expanduser().resolve(), "env")
    legacy = repo_data_dir(repo_root)
    if legacy.is_dir():
        return RuntimePaths(legacy.resolve(), "repo")
    return RuntimePaths(user_data_dir().resolve(), "user")


def prescan_data_dir(argv: Sequence[str]) -> str:
    """在正式 parse 之前从 argv 里捞出 --data-dir。

    argparse 的子解析器默认值会覆盖顶层同名参数，所以运行目录不能只靠 args 传递：
    入口（hsrmap/__main__.py、cli.main）先用这个函数把环境变量定下来，
    保证任何运行态 import 看到的都是同一个根目录。
    """
    items = list(argv)
    for index, item in enumerate(items):
        if item == "--data-dir" and index + 1 < len(items):
            return items[index + 1]
        if item.startswith("--data-dir="):
            return item.split("=", 1)[1]
    return ""


#: 显式 pin 住的运行时根目录（set_runtime）；None 表示每次都按规则重新解析。
_PINNED: RuntimePaths | None = None


def get_runtime() -> RuntimePaths:
    """当前运行时根目录。

    没有显式 pin 时**每次重新解析**：入口可能在 import 之后才拿到 --data-dir，
    缓存会让「后设的根目录」失效（实测过：cli 顶层 import 把 repo/data 冻在了进程里）。
    解析本身只是几次 stat，代价可以忽略。
    """
    if _PINNED is not None:
        return _PINNED
    return resolve_runtime()


def set_runtime(runtime: RuntimePaths | None) -> RuntimePaths | None:
    """显式固定（或取消固定）本进程的运行时根目录。"""
    global _PINNED
    _PINNED = runtime
    return runtime


def reset_runtime() -> None:
    """取消 pin（测试用）。"""
    global _PINNED
    _PINNED = None
