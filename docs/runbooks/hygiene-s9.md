# Runbook · S9 硬切换清理 + DoD 12 项验收（a1-8 十四 / 十六）

**状态：完成（2026-10-04）**。`python -m hsrmap dod` = **PASS（12 通过 / 0 失败 / 0 跳过）**，rc=0。

## 1. DoD 变成一条命令

```bash
python -m hsrmap dod                 # 逐项判定 + 退出码（0 = 全部成立，2 = 有条目失败）
python -m hsrmap dod --json-out      # 机器可读
python -m hsrmap dod --require-data  # 没有数据的项算失败（发布验收用）
python -m hsrmap dod --stamp         # 先给认得出 schema 的旧库补 user_version
```

| # | 项 | 判定方式 |
| ---: | --- | --- |
| 1 | `python -m hsrmap.cli` 不再静默成功 | 真的起子进程跑一次：必须**非零退出且有输出** |
| 2 | clean checkout 跑普通测试不建 `data/` | conftest 注入临时 `HSRMAP_DATA_DIR`；子进程里验证路径解析到仓库之外 |
| 3 | 只读操作不隐式建 SQLite | 缺库时 `open_readonly`/`open_readwrite` 抛 FileNotFoundError 且不落文件 |
| 4 | 仓库没有 runtime DB / cache / submit / bak / 嵌套 zip / 生成前端产物 | hygiene 扫描（基线内 8 / 新增 0）+ `.gitignore` 边界清单 |
| 5 | `phase1` 只留 reference / fixture | 扫 `.db/.zip/.bak/.pyc` 与 `__pycache__` |
| 6 | 发布包由 allowlist 生成 | `submit/` 的实际文件集合必须与 `release-manifest.json` **完全一致**（多一个就说明是手工镜像） |
| 7 | 所有数据库有明确 schema version | 每个 `*.db` 必须给出版本：迁移表 / user_version / metadata |
| 8 | TEMP 下的 `rNN*.py` 不再承担生产流程 | 生产代码零引用（正则匹配真实调用形态，排除检查器自己） |
| 9 | 每个完成点位都有 LOCATE / SOLVE provenance | 1006 个完成点位的证据等级逐个非空 |
| 10 | 1006/1006 不掩盖 transcription / inference | 分层之和 == 点位数 |
| 11 | completeness / closure 没有逐点 N+1 | 实测查询数不随点位数增长 |
| 12 | Web 能回答「为什么算完成」 | 证据总览有分层；点位详情每条步骤都有证据等级 |

## 2. 这一轮改了什么

### 2.1 版本位：所有库都说得清自己是哪一版（DoD 7）

`hub` 之外的三类库以前只有「表在里面」这一层保证：

* `hsrmap/database.py`（`core.db`）、`hsrmap/detail_db.py`（`detail.db`）、
  `hsrmap/user_db.py`（`user.db`）现在都写 `PRAGMA user_version = SCHEMA_VERSION`；
* `guide.db` / `published.db` 本来就有 `schema_migration`（6 条迁移，见 S5）；
* 旧库用 `dod --stamp` 一次性补齐：**只给认得出 schema 家族的库补**，
  认不出家族的库列成 legacy 由人处理——给旧库编一个版本号才是真的撒谎。
  本次补了 3 个：`snapshots/*/core.db`、`enrichments/*/detail.db`、`enrichments/*/staging/detail.staging.db`。

### 2.2 状态与证据不许互相打脸（DoD 9）

DoD 一跑就抓到 5 个点位「状态说 COMPLETE、证据等级却说没有解法」：
判定层把「到了之后的交互动作」（`按/点击/对话/调查/变身/击落/射击/击败`）算作解法证据，
而证据层只认 `has_solution_steps`。现在两边共用同一个 `stages.is_solve_text()`：

* 交互动作 → 该步是 COMMUNITY_TEXT 的解法证据；
* 官方说明自己把点位上调成「要解法」时 → 该点是 **OFFICIAL** 的解法证据；
* **六状态一个都没动**（shadow 逐点仍等于 S0 基线）。

### 2.3 删掉的东西

| 目标 | 为什么 |
| --- | --- |
| `data/guides/_canary_run/` | 2026-10-02 的金丝雀副本，生产代码零引用（旧临时脚本的遗留） |
| `data/enrichments/*/staging/detail.staging.db` | enrich 的 staging 中间产物，每次重建都会重新生成 |

**刻意保留并写进文档的兼容点**（不是遗留，是有意的窗口）：

* `GuideDatabase(path)` 仍是 `create` 的兼容写法——新代码用三个显式入口；
* 运行时解析仍保留 `<repo>/data` 这一档（`source: repo`），
  否则现有数据目录会被打断；clean checkout 解析到用户数据目录；
* `artifacts/legacy/temp-scripts/`（391 个历史一次性脚本）留在归档里，
  但它们不在发布 allowlist 内，生产流程也不再引用。

### 2.4 CI

`.github/workflows/ci.yml` 最后一步加了 `python -m hsrmap dod`：
源码树那几项在 CI 必须全过，需要真实数据的项在没有 `data/` 时如实跳过（`PASS (partial)`）。

## 3. 证据

* `tests/test_dod.py`（6 条）：12 项清单与规格一致、无数据时源码树各项全过、
  CLI 的 JSON 输出与退出码契约、渲染覆盖每一项、
  版本标记的四种读法（user_version / schema_migration / metadata / 空库）、
  `stamp_versions` 只补认得出的家族且幂等；
* 现场：`python -m hsrmap dod` = PASS（12/12，rc=0）；`closure-check` PASS（11 栏）；
  `repo-hygiene` PASS；`ruff` All checks passed；`pytest -q` 全绿；
  `pytest --run-data-e2e`（shadow / perf / Web 证据 / DoD）22 条全过。
