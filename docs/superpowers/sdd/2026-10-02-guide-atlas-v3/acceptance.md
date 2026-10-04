# a1-6 验收对照表

逐条列出 `a1-6.md` 的验收/退出条件与证据来源。机器验收命令是 `python -m hsrmap guides closure-check`。

## 路线图 P0–P6（§2 表 / §3–§33）

| Phase | 退出条件 | 证据 |
| --- | --- | --- |
| P0 Baseline | 全量测试基线明确 | `python -m pytest` → **410 passed / 65 skipped**；`--run-data-e2e` → **473 passed / 2 skipped**（2 = live）；`pytest.ini` `norecursedirs=submit`；`tests/conftest.py` 三层语义（unit/data/live） |
| P1 Asset Pipeline | 3DM/9game 可稳定判定 | 冒烟矩阵三站 **Case A**、`BLOCKED=0`、`NOT_IMAGE=0`；类型化状态（FETCHED/CACHE_HIT/HTTP_BLOCKED/NOT_IMAGE/…）；本轮 `pytest --run-live tests/test_guide_live_smoke.py` → **2 passed**（真实网络） |
| P2 Source Quality | Review 只接收合格 source | `guides/qa.py`：`ADMISSION_STATUS=QA_PASS` + `admits_review()` 把关；`qa-rescan` 64 页 → **62 QA_PASS**、2 `JS_RENDER_REQUIRED`；`tests/test_guide_qa_gate.py`（9） |
| P3 Review Unit Signature | backlog 显著收敛 | 本轮 `guides signature-merge`：`review_items_before=316 → after=316`、`unique_signatures=316`、`unique_target_candidates=45`、`duplicate_groups=0`、`duplicate_ratio=0.0`、`merge_ratio=0.0`；历史 524→310、0.4084→0；`data/guides/reports/signature-merge.json` |
| P4 Snapshot Regression | regression 自动阻断 | 三级闸门（HARD/COVERAGE/REVIEW）+ `assert_publishable()`（FAIL → exit 2）+ `allowed_regressions.yaml` 豁免；`tests/test_guide_snapshot_diff.py`（12）/`test_guide_snapshot_manifest.py`（12） |
| P5 Publish V3 | staging→validate→switch | `publishing/atomic.py` + 真实原子发布（发布库 153→158→**160 条**、备份 `published.db.bak-*`、`PRAGMA integrity_check=ok`、无 staging 残留）；`tests/test_guide_atomic_publish.py`（6） |
| P6 Closure | Closure PASS | `guides closure-check` → **CLOSURE RESULT PASS**，10/10 检查（Engine Gate A/B/C、Broken bindings 0、Broken assets 0、Missing core targets 0、Hallucinated steps 0、Golden、Offline E2E、Snapshot regression） |

## Crawler Sprint 1–4（§二十四 退出条件）

| Sprint | 退出条件 | 证据 |
| --- | --- | --- |
| 1 Reliability | 失败原因全部可解释 | 类型化资产状态 + `RETRY_STATUSES` 按失败类型重试 + `HostScheduler` + 断路器；冒烟矩阵 `BLOCKED=0` |
| 2 Identity | 文章/图片不因分页、移动版、转载反复污染 | `ArticleFamilyResolver`（镜像/分页/追踪参数收敛）、`guides corpus` 记 `SKIPPED_MIRROR`、SHA256 + pHash 两层去重（1242 资产 → 314 组近重复，抽样核对 hamming=0） |
| 3 Intelligence | 知道"下一页最值得抓什么" | 证据账本（`source_search_*`）+ `discovery_plan`（浮脂 8 缺口）+ `frontier` 规则打分 + `source_yield`/`target_yield`；真实一轮：2 页入库 → 5 条 PAGE_ANCHOR 草稿 → 发布 +2 条（覆盖率 155→157/611） |
| 4 Productionization | job 可暂停/恢复/复盘/重跑且幂等 | `guide_job` + `JobBudget`（5 类硬上限）+ `--resume` + `crawl-report.json/md`；同一 job 跑两遍 `guide_page` 行数不变（测试断言） |

## 专项要求

| 章节 | 要求 | 证据 |
| --- | --- | --- |
| §10 | 规则 + pHash 的图片相关性过滤 | `assets/relevance.py`（尺寸/长宽比/URL/alt/DOM/SHA/pHash），`ingest.observe_images()` 先过滤再调模型；`guides image-filter --page 140` → 15 图全保留；测试（4） |
| §14 | 固定 1s → Host Scheduler | `crawler/hosts.py::HostScheduler`（分 host 节流），测试（7 中的 3） |
| §15 | Circuit Breaker | 连续失败 → `HOST_BLOCKED`/`HOST_DEGRADED` + 冷却半开 + `suppressed` 计数，报告进 `crawl-report.json`；`AssetFetcher(scheduler=…)` |
| §16/§17 | 预算 + 幂等续跑 | `JobBudget` + cursor + `completed/failed` 清单 |
| §18/§19 | Observability + Host Health | `crawl-report.json/md`（requests/bytes/http 分档/retries/cache hits/pages/assets/qa/yield）；`host_health()`（QA 未检 ≠ 失败，CDN 资产不拖累页面 host） |
| §20 | Fixture corpus + live smoke | `tests/fixtures/crawler/{3dm,17173,9game}/` 七类 case + manifest；`tests/test_guide_crawler_fixtures.py`（8）离线；`tests/test_guide_live_smoke.py`（2）默认跳过 |
| §21 | Robots 缓存 + `ROBOTS_DENIED` | `RobotsCache`（TTL 1h）；robots 拒绝是正式状态，不再混入 fetch failure |
| §22 | SSRF 安全边界 | `crawler/guard.py`：非 http(s)、内嵌凭据、localhost/*.local/metadata、全部非公网段；重定向复检；DNS 解析显式可选；测试（6） |
| §27 | Admin 指标 + 推导式 next action | `guides/admin.py` + `/api/v1/atlas/{topics,coverage,review,sources,snapshots}`；`derive_next_action()` 五条映射；测试（6） |
| §28 | Job Resume | 见 Sprint 4 |
| §29 | 统一 AI 缓存（带 prompt_version） | `guides/ai_cache.py`：`sha256(provider+model+prompt_version+normalized_input)`、DB+磁盘+内存、`CachedProvider`；`guides ai-cache`；测试（8） |
| §30 | Hallucinated steps = 0 的证明 | `audit.grounding()` 分级（EXACT/FRAGMENT/ASSEMBLED/NONE）+ `closure_check` 审计**发布库**；现网 hallucinated **0** |
| §31 | Golden A/B/C/D 变成测试语义 | `golden.py::GOLDEN_LEVELS` + `level_status()`；closure 打印 `Golden regression....... 15/15 + A/B/C/D PASS` |
| §32 | 机器可执行 closure checklist | `guides closure-check`（退出码：PASS=0 / BLOCKED=2），另出 Corpus health 单独段落 |
| §4 章 ADR | 五项明确 implemented/deferred | ADR-001/003/004/005 **IMPLEMENTED**；ADR-002 content_block **DEFERRED**（含重访条件） |

## 明确不属于验收的部分

文档 §32 写明 **Corpus coverage 不出现在硬 PASS 条件里**，因此：

```text
Closure PASS                                ✓ 10/10 检查
Corpus incomplete                           ✗ 仍成立：157 / 611 覆盖、25 条隔离项待新源
```

两者同时成立正是文档要求澄清的概念边界；Corpus 的下一步是继续跑目标驱动采集（`search-plan` → 证据账本 → `corpus --resume` → `review-approve`）。
