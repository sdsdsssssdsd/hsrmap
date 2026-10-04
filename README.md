# hsrmap · Guide Atlas V3

把《崩坏：星穹铁道》官方互动地图的点位，变成**可判定「玩家照着攻略能不能拿到」**的语料库：

* 官方点位是第 1 阶段的唯一标准（「到了就能拿」的点位，官方地图本身就是完整攻略）；
* 第 2 阶段（「到了还要做事」的点位）必须有攻略写出**怎么做**——方向序列、模块步骤、触发方式；
* 每条结论都要能回答「凭什么」：来源页、逐字片段、图解转录、推断依据，全部落账。

当前状态：官方点位 **1006 / 1006** 可达（829 到点即得 + 177 含解法），`closure-check` 十项全 PASS，
`published-audit` 的 HALLUCINATED / UNGROUNDED 都是 0。

---

## 快速开始

```bash
python -m pip install -r requirements.lock.txt      # 或 pip install -e ".[dev]"

python -m hsrmap runtime                            # 当前运行目录/来源（第一次先看这个）
python -m hsrmap guides completeness --markdown      # 完成度总表（默认全主题）
python -m hsrmap guides closure-check                # 十项门禁（PASS/FAIL + 记分板）
python -m hsrmap repo-hygiene                        # 仓库卫生（只对新增违规失败）
python -m pytest -q                                  # 全量测试（默认不需要真实 data/）
```

两个本地服务（**审核台与离线地图是分开的**，互不依赖）：

```bash
start.bat          # 离线地图  http://127.0.0.1:8766/
start_review.bat   # 审核台    http://127.0.0.1:8767/review
```

## 架构（一条链）

```text
官方互动地图 ──sync──▶ 快照 (core.db / detail.db)          [运行时目录]
                        │
                        ├── official-seed ──▶ 官方点位条目（LOCATE 证据）
社区攻略页 ──fetch/parse/import──▶ guide.db（页/条目/步骤/图/评审）
                        │
                        ├── audit（grounding：EXACT/FRAGMENT/ASSEMBLED/图解法转录）
                        ├── stages（要求 × 证据 → 六状态：LOCATE_COMPLETE / SOLVE_MISSING / COMPLETE …）
                        ├── evidence ledger（每次检索、每个候选、每条否决理由）
                        ▼
                  publish-snapshot（diff → hard/review gate → staging → audit+E2E → manifest → 原子切换）
                        ▼
                  published.db（不可变发布快照）──▶ 审核台 / 离线地图 / 报告
```

关键模块（都在 `hsrmap/`）：

| 模块 | 管什么 |
| --- | --- |
| `guides/stages.py` | 完成度判定层（要求 × 证据 → 六状态），不依赖模型 |
| `guides/audit.py` | 「这一步真的在来源里吗」：grounding 四档 + `[图解法转录 sha]` 通道 |
| `guides/evidence.py` | 检索账本：run / result / verdict（含 `NO_PUBLIC_SOURCE_FOUND`） |
| `guides/publishing/` | 发布：diff、gate、staging、原子切换、offline E2E |
| `guides/closure.py` | 十项闭合门禁与记分板 |
| `runtime.py` / `paths.py` | 运行目录解析（显式 → 环境变量 → 仓库 data/ → 用户目录）与惰性路径 |
| `hygiene.py` / `release.py` | 仓库卫生规则、按 allowlist 生成交付包 |

## 运行目录（数据放在哪）

Git checkout **不是**运行时根目录。解析顺序（先命中先赢）：

```text
--data-dir  →  HSRMAP_DATA_DIR  →  <repo>/data（兼容窗口）  →  OS 用户数据目录
```

* `python -m hsrmap runtime` 打印当前用的是哪个、以及来源（explicit/env/repo/user）；
* 只读命令（`completeness` / `closure-check` / `published-audit` / `ledger` …）**不会**建库：
  库不存在就 rc=1 并说明原因；
* 数据库打开方式显式三态：`GuideDatabase.open_readonly / open_readwrite / create`，
  只有 `create` 允许建目录/建表/迁移；
* 测试默认把运行目录指到临时目录（`HSRMAP_DATA_DIR`），所以 `pytest` 不会碰你的 `data/`；
  需要真实快照的用例加 `--run-data-e2e`。

## Guide Atlas 判定模型

| 维度 | 取值 |
| --- | --- |
| 完成要求 | `LOCATE_ONLY`（到点即得） / `LOCATE_AND_SOLVE`（到了还要做事） |
| 证据能力 | `SCOPE`（总共多少个） / `LOCATE`（能不能到点） / `SOLVE`（到了怎么做） |
| 状态 | `NO_EVIDENCE` / `SCOPE_ONLY` / `LOCATE_MISSING` / `LOCATE_COMPLETE` / `SOLVE_MISSING` / `COMPLETE` |

判定细节与当前数据见 `docs/superpowers/sdd/2026-10-02-guide-atlas-v3/guide-completeness.md`。

## 测试与门禁

```bash
python -m pytest -q                       # 单元 + 集成（不需要真实 data/）
python -m pytest --run-data-e2e           # 需要快照/detail/guide 库的用例
python -m ruff check hsrmap tests tools   # lint（语法/未定义名一类）
python -m hsrmap repo-hygiene             # 仓库卫生（基线见 tools/hygiene_baseline.json）
python -m hsrmap guides published-audit   # 已发布攻略的 grounding 审计
python -m hsrmap guides closure-check     # 闭合门禁（退出码：0 PASS / 2 FAIL）
python -m hsrmap dod                      # Definition of Done 12 项（a1-8 十六）
python -m hsrmap doctor                   # 闭环自检：启动入口/端口/首屏请求与载荷预算
```

`hsrmap dod` 是整套卫生化的收口验收：命令契约、测试隔离、只读不建库、仓库边界、
phase1 只留参考物、发布包由 allowlist 生成、每个库都有 schema 版本、临时脚本零依赖、
每个完成点位的证据来源、分层可见、无逐点 N+1、Web 能回答「为什么算完成」。
源码树那几项在任何 checkout 都必须过；需要真实数据的项在没有 `data/` 时如实报 SKIPPED
（`--require-data` 可把它们升级为失败，发布验收用这个）。

命令契约：**0 = 成功**（含明确的 NOOP）、**1 = 执行/环境/数据错误**、**2 = 用法或 gate 阻断**；
只有显式 `--quiet` 允许「rc=0 且无输出」。

## 发布

```bash
python -m hsrmap release --dry-run     # 先看会收录哪些文件
python -m hsrmap release               # 生成 submit/ + submit.zip + release-manifest.json
```

收录规则 = 源码树减去卫生规则的 forbidden 集合（运行态 `data/`、镜像 `submit/`、生成报告、
运行态 SQLite、缓存、密钥、备份、超大生成物），**唯一例外**是 `web/dist`（交付包必须自带前端构建产物）。
zip 的 sha256 写在同名的 `.sha256` 文件里。

## 仓库结构

```text
hsrmap/                 程序代码（判定层、语料层、发布层、服务层）
hsrmap_phase1/          phase1 抓取/校准包（参考实现）
phase1/                 不可变参考数据：endpoint_registry、calibration（golden/校准点位）、samples
tests/                  测试（fixtures 很小、可人工核对）
web/src/                前端源码（dist 是构建产物，由 release 带上）
docs/
  specs/                规格（a1 ~ a1-8）
  runbooks/             运维手册（S1 安全化、服务拆分、基线）
  architecture/         架构说明
  history/              历史文档（phase1 抓取等）
  superpowers/sdd/      分阶段实施记录（progress.md 是主线）
tools/                  开发工具（发布/卫生/抓取）
data/ submit/ artifacts/ logs/ reports/   ← 运行态与产物，不进源码树（.gitignore + 卫生检查）
```

## 安全与证据纪律

* **官方为准**：第 1 阶段只认官方点位；官方没有再补充这回事。
* **来源可查**：每条步骤要么在它自己声明的来源页里逐字找得到，要么写明是从**哪张图**转录的
  （`[图解法转录 <sha>]`，那张图必须挂在这条条目上）。
* **推断留痕**：交叉推断必须写明依据，摘要里说明边界；`HALLUCINATED_STEP` 是发布硬失败。
* **查过没有也是结论**：`source_search_run/result/verdict` 记录每次检索与否决理由。
* **不静默**：命令要么有输出要么有退出码；只读操作不建库；测试不写仓库。

## 开发者工作流

1. 改判定/发布语义 → 先写 shadow 对比（新旧实现逐点比对，六状态必须完全一致）；
2. 新数据操作 → 走正式入口（`hsrmap guides …` / `hsrmap release` / `hsrmap repo-hygiene`），
   不要新增一次性脚本；
3. 提交前：`pytest -q` + `ruff check` + `repo-hygiene` + `hsrmap dod` 四条都绿；
4. 交付前：`hsrmap release` 重建交付包，再看 `release-manifest.json`（`dod` 第 6 项会核对
   submit/ 与 manifest 是否完全一致——手工往交付目录里塞文件会被抓出来）。
