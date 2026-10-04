# Runbook · S3 仓库分家（a1-8 八 / 九 / 十）

**状态：完成（2026-10-04）**。判定语义未改：全量 `pytest -q` = 575 passed / 77 skipped；
`repo-hygiene` 对新树 PASS；`release` 由 allowlist 流水线生成。

## 这一阶段改了什么

| 项 | 之前 | 现在 |
| --- | --- | --- |
| 生成图与历史产物 | `phase1/raster/*.png`（4 MB）、`phase1/calibration/alignment-preview.png`（2.95 MB）、`archive/*`、根目录 3 个 `submit.zip.bak-*`（各 10 MB） | 全部移出源码树到 `artifacts/`（`artifacts/phase1/`、`artifacts/legacy/`），源码树里的 `phase1/` 只剩参考数据（registry / calibration JSON / samples，最大 705 KB） |
| 交付包 | 手工镜像目录（脚本逐个 copy + 打 zip） | `python -m hsrmap release`：**allowlist 流水线**，规则 = 源码树减去卫生 forbidden 集合，唯一例外 `web/dist`；输出 `submit/` + `submit.zip` + `release-manifest.json` + `.sha256` |
| 交付包体积 | 500 项 / 14.2 MB（含 9.9 MB 生成图） | **528 项 / 4.7 MB**（zip 1.49 MB） |
| 依赖声明 | 只有 `requirements.txt`（且只在镜像里） | `pyproject.toml`（元数据 + 依赖 + pytest + ruff 配置）、`requirements.txt`（下限）、`requirements.lock.txt`（本次 closure 的精确版本） |
| pytest 配置 | `pytest.ini` | 并入 `pyproject.toml`（`[tool.pytest.ini_options]`），`pytest.ini` 删除 |
| .gitignore | 零散几条 | 边界规则（`/data/ /logs/ /submit/ /artifacts/ /reports/ /web/dist/`、`*.db`、`*.bak`、`.env` + `!.env.example`） |
| 仓库卫生 | 无 | `hsrmap/hygiene.py` + `hsrmap repo-hygiene`（基线 `tools/hygiene_baseline.json`，**只对新增违规失败**）+ `tests/test_repo_hygiene.py` |
| CI | 无 | `.github/workflows/ci.yml`：安装锁文件 → `pytest -q` → `ruff check` → `repo-hygiene` |
| README | 仍是 phase1「抓 HoYoLAB 数据」的说明 | 项目入口（是什么 / 架构 / 快速开始 / 运行目录 / 判定模型 / 测试与门禁 / 发布 / 仓库结构 / 安全纪律 / 开发者工作流）；旧内容移到 `docs/history/phase1-hoyolab-capture.md` |
| 阶段文档 | `PHASE5C-vision-region-architecture.md` 只在交付包根目录 | 回填到 `docs/architecture/phase5c-vision-region-architecture.md`（随源码树一起进交付包） |
| 根目录快捷页 | `打开审核台.html`（中文名、根目录） | `tools/windows/open-review.html`（指向 8767 的审核台） |

## 基线现状（`tools/hygiene_baseline.json`，8 条）

| 路径 | 规则 | 为什么留着 |
| --- | --- | --- |
| `.env` | secrets | 本机运行时需要的密钥文件；已 gitignore，不进交付包 |
| `data/` `logs/` `reports/` | runtime-data / generated-reports | 运行态目录（兼容窗口：老 checkout 的数据仍在 `<repo>/data`） |
| `submit/` `submit.zip` | release-mirror | 交付产物（由 `hsrmap release` 生成） |
| `artifacts/` | runtime-data | 生成图与历史备份的新家 |
| `web/dist/` | web-build | 本地跑离线地图需要它；交付包也带（唯一 allowlist 例外） |

清零方向：把 `data/` 迁到 `HSRMAP_DATA_DIR`（或用户数据目录）后，前四条可以逐条删掉；
`submit/`/`submit.zip` 已经是纯产物，等 CI 直接产出 artifact 后也可以不再落树。

## 证据

* `tests/test_release_build.py`（5 条）：allowlist 收录/排除、zip 与清单、默认 clean、dry-run 不写盘、
  真实仓库的收集结果不含 `data/`/`.env`/`artifacts/`；
* `tests/test_repo_hygiene.py`（6 条）：边界规则逐条命中、干净树 PASS、基线只挡历史、oversized、
  真实仓库对基线必须干净、`.gitignore` 必须声明边界；
* 命令实测：`hsrmap release --dry-run` → 525 项 / 4.68 MB；`hsrmap release` → 528 项 / 4.70 MB、zip 1.49 MB、
  sha256 写在 `submit.zip.sha256`；
* `ruff check hsrmap tests tools` → All checks passed；`repo-hygiene` → findings 8（基线内 8 / 新增 0）。
