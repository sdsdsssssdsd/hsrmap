# Runbook · S1 安全化（a1-8 四.1 / 四.2 / 四.3）

**状态：完成（2026-10-03）**。判定语义未改：closure PASS、`Ready to grab 1006 / 1006`、`Missing solve 0`，
状态矩阵 sha256 与 S0 基线（`docs/runbooks/baseline-hygiene.json`）一致。

## 这一阶段改了什么

| 项 | 之前 | 现在 |
| --- | --- | --- |
| CLI 模块入口 | `python -m hsrmap.cli …` rc=0 且零输出零副作用（静默成功） | `hsrmap/cli.py` 末尾加 `__main__` 守卫；`-m hsrmap` 与 `-m hsrmap.cli` 行为一致 |
| 命令契约 | 成功/失败/用法混在一起 | 0=成功（含明确 NOOP）、1=执行/环境/数据错误、2=用法或 gate 阻断；只有显式 `--quiet` 允许无输出 |
| 完成度报告默认范围 | `guides completeness` 不带 `--topic` 时只报 floating-grease 的 48 个点位 | 默认**全部启用主题**（1006/1006）；`--topic X` 仍然只报该主题 |
| 运行目录 | 隐式写 `<repo>/data`，无解析规则 | `hsrmap/runtime.py`：显式 `--data-dir` → `HSRMAP_DATA_DIR` → 仓库 `data/`（兼容窗口）→ OS 用户数据目录；只解析不建目录 |
| 路径常量 | `hsrmap/paths.py` 在 import 时把 repo 路径算死 | 运行态常量惰性解析（PEP 562），换根即时生效；源码/参考数据仍跟仓库 |
| 数据库打开 | `GuideDatabase(path)` 隐式 mkdir + 建库 + 建表 + 迁移 | 显式三态：`open_readonly` / `open_readwrite` / `create`（只有 create 允许副作用）；viewer 用 readwrite（可写但不建库）+ published 只读 |
| 只读命令 | 库不存在时顺手建一个空库，报告看起来像 0/0 | `READ_ONLY_GUIDES_COMMANDS`（completeness / closure-check / published-audit / atlas / targets / ledger / coverage / topics / search-log / search-plan / job-* / evidence-forms / yield / frontier / asset-smoke / identity / review）在库缺失时 rc=1 并说明原因 |
| 测试运行目录 | 测试可能写仓库 `data/`，结束后由 conftest 事后清理并报警 | 普通测试注入 `HSRMAP_DATA_DIR=<临时目录>`（`--run-data-e2e` 保留真实 data/）；污染检测降级为最后一道保险 |
| 日志目录 | `<repo>/logs` | 运行时目录下的 `logs/`（`<runtime>/logs`） |

## 新增的入口与文件

* `hsrmap runtime`：打印当前运行时根目录、来源（explicit/env/repo/user）与关键路径。
* `hsrmap --data-dir <dir> …` / `hsrmap guides --data-dir <dir> …`：一次性指定运行目录。
* `hsrmap/runtime.py`、`tests/test_runtime_paths.py`、`tests/test_cli_entrypoints.py`、`tests/test_guide_db_modes.py`。

## DoD 证据（a1-8 十六 的第 1–3 条）

1. **`python -m hsrmap.cli` 不再静默成功** — `tests/test_cli_entrypoints.py::test_cli_module_entry_help_is_not_silent`：
   `-m hsrmap.cli --help` 必须 rc=0 且 stdout+stderr 非空并含 usage；`guides <不存在的命令>` 必须 rc=2。
2. **clean checkout 跑普通测试不会建出 `data/`** — `tests/conftest.py` 在 `pytest_configure` 里把
   `HSRMAP_DATA_DIR` 指到临时目录；`tests/test_runtime_paths.py::test_guides_completeness_does_not_touch_repo_data`
   逐文件比对仓库 `data/` 的大小与 mtime，跑完必须一模一样。
3. **viewer / 只读操作不会隐式创建 SQLite** — `tests/test_guide_db_modes.py`：
   `open_readonly` / `open_readwrite` 对不存在的库抛 `FileNotFoundError` 且**不建文件也不建目录**；
   `open_readonly` 的连接写库会 `sqlite3.OperationalError`；
   `hsrmap --data-dir <空目录> guides completeness` 必须 rc=1、提示 `guide database missing`、目录里零个 `*.db`；
   写入型命令（`guides backfill-topics`）在同样条件下会建库（create 是唯一允许建库的路径）。

## 怎么跑

```bash
python -m pytest -q                       # 559 passed / 77 skipped（本轮结束时）
python -m hsrmap runtime                  # 看当前运行目录与来源
python -m hsrmap --data-dir /tmp/rt guides completeness   # 指定运行目录
python -m hsrmap guides completeness --markdown | tail -3 # 全主题完成度（默认 1006）
```

## 已知残留（留给 S2/S3）

* `hsrmap/cli.py` 顶层仍 import `hsrmap.sync` / `hsrmap.detail_enrich` 等会在 import 期取运行态路径的模块；
  这些模块拿到的路径按设计是「入口确定后的根目录」，但 `python -m hsrmap.cli … --data-dir` 这种
  「先 import 模块再解析参数」的写法，那些模块里的常量仍可能是仓库路径。彻底解决在 S3（把运行态访问收进 context）。
* 仓库树里仍有 `data/`、`submit/`、`logs/`、`submit.zip*`、`web/dist/`、`phase1` 大图（S3 处理）；
  S2 会给出 repo hygiene checker 与 .gitignore 边界规则，先做到「不再新增污染」。
