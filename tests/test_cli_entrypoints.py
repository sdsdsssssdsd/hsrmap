"""CLI 入口必须是真入口（a1-8 四.1）：rc + 输出，不允许「静默成功」。

这里的断言全部走 subprocess：只有真的起一个进程，「命令契约」才被验证到
（直接调用 main() 会绕过 argparse 的退出码与 stdout 行为）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*argv: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def test_package_entry_help_is_not_silent() -> None:
    """python -m hsrmap --help：rc 0 且一定有输出。"""
    result = run_cli("-m", "hsrmap", "--help")
    assert result.returncode == 0, result.stderr
    assert (result.stdout + result.stderr).strip(), "help 必须打印内容"


def test_cli_module_entry_help_is_not_silent() -> None:
    """python -m hsrmap.cli --help：同一个入口，不许 rc=0 且 stdout/stderr 全空。"""
    result = run_cli("-m", "hsrmap.cli", "--help")
    assert result.returncode == 0, result.stderr
    combined = (result.stdout + result.stderr).strip()
    assert combined, "静默成功：CLI 模块入口没有输出（缺 __main__ 守卫）"
    assert "usage" in combined.lower()


def test_unknown_guides_command_is_a_usage_error() -> None:
    """命令契约：用法错误 = rc 2，不是 0。"""
    result = run_cli("-m", "hsrmap", "guides", "definitely-not-a-command")
    assert result.returncode == 2, (result.returncode, result.stdout[:200], result.stderr[:200])
