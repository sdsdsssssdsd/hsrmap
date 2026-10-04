"""Crashed RUNNING jobs must become PENDING on resume, SUCCESS jobs stay done."""

import json
import os
from pathlib import Path

import pytest

from hsrmap.jobs import JobStore


def test_resume_resets_running_and_keeps_success(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.json")
    store.enqueue("map_info", "842")
    store.enqueue("map_info", "1")
    store.mark_running("map_info", "842")
    store.mark_success("map_info", "1", response_path="raw/1.json", response_sha256="aa")

    store.close()
    resumed = JobStore(tmp_path / "jobs.json")
    resumed.reset_running_to_pending()

    assert resumed.get("map_info", "842").state == "PENDING"
    assert resumed.get("map_info", "1").state == "SUCCESS"
    assert resumed.pending_count() == 1


def test_save_survives_a_transient_windows_file_lock(tmp_path, monkeypatch):
    """jobs.json 的写入必须是原子的，而且要被瞬时占用顶住。

    真实事故：资源阶段最后一次 save() 走的是 unlink()+rename()，在 Windows 上被并发读者/
    杀软顶成 PermissionError [WinError 32]，整个 sync 崩掉、一个快照都没发布。
    现在走 os.replace + 退避重试 —— 这条测试让前两次 replace 抛 PermissionError，
    断言最终仍然写完，而且文件内容是**完整可解析**的（不是半个文件）。
    """
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    store.enqueue("asset", "a")
    store.enqueue("asset", "b")

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(32, "another process is using the file")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    store.mark_success("asset", "a", response_path="assets/a", response_sha256="aa")
    assert calls["n"] == 3  # 两次被顶回来，第三次成功
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert {item["resource_id"]: item["state"] for item in payload["jobs"]} == {"a": "SUCCESS", "b": "PENDING"}
    #: 没有留下半个临时文件
    assert not (tmp_path / "jobs.json.tmp").exists()


def test_save_gives_up_loudly_when_the_file_is_really_stuck(tmp_path, monkeypatch):
    """一直占用就必须**大声失败**，绝不吞掉 —— 静默写不进去会让 resume 读到旧状态。"""
    store = JobStore(tmp_path / "jobs.json")
    store.enqueue("asset", "a")

    def always_locked(src, dst):
        raise PermissionError(32, "still locked")

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr("hsrmap.jobs.time.sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="无法原子写入"):
        store.mark_success("asset", "a", response_path="x", response_sha256="y")
