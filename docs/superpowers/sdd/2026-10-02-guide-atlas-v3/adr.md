# Architecture Decisions（a1-6 §33 步 7）

五项必须在 closure 前明确 implemented 或正式 deferred 的架构选择。

## ADR-001 published V3 target 模型 —— **IMPLEMENTED（Option B：synthetic key）**

**决定**：`published.db` 不携带 `guide_topic` / `guide_target` / `guide_entry_target`；
Scope 由 `guide_entry.source_point_id` 的合成键表达（`map:<id>:topic:<key>`、`set:<id>:topic:<key>`、`global:topic:<key>`），
查询期由 `publish/lookup.py::expand_guide_index` 展开回官方点。

**理由**：Viewer 只读发布库，synthetic key 已能表达 POINT/MAP_LABEL/POINT_SET/GLOBAL 四种 Scope，
且 MAP_LABEL 路线在 Viewer 里能正确展开到点（`by-point` 实测可用）；
把 target 表复制进发布库会引入第二份真相，而 `sync_published` 必须保持逐行可审计。

**代价与重访条件**：Viewer 无法直接按 target 做 SQL 查询（需经 lookup 展开）；
若将来出现「一个 target 多篇 guide、需要按 target 排序/分页」的需求，则升级为真正的 V3 落地（Option A）。

## ADR-002 content_block —— **DEFERRED**

**决定**：不引入 `guide_content_block` / `guide_block_asset`；
内容模型保持 `guide_steps`（text）+ `guide_assets`（step_index → sha256），
排布由 `guides/layout/{planner,render}.py` 按 `layout.profile` 生成（puzzle_steps_v1 / collection_route_v1 / challenge_v1）。

**理由**：现有 183 篇发布内容的语义（步骤 + 配图）用 steps/assets 已完整表达；
引入 block 表意味着迁移全部已发布数据 + 改动 Viewer/审核台/发布链三处，收益不足以支撑 closure 前的风险。

**重访条件**：当出现「同一 guide 需要多种排布（路线图 + 步骤 + 奖励块）」或「block 级审核」时。

## ADR-003 ArticleFamilyResolver —— **IMPLEMENTED（Crawler Sprint 2，2026-10-03）**

**决定**：identity 规则只有一份实现（`guides/signature.py` 的 `article_family` / `canonical_url` / `registrable_domain` /
`query_signature`），爬虫侧由 `guides/crawler/identity.py::ArticleFamilyResolver` 提供类接口：
`resolve()` → `ArticleRef(host, raw_host, site, article_id, page_index, family)`、`same_article()`、
`group()` / `unique()` / `duplicates()`、`mirror_of_known()`、以及页面自述的 `declared_canonical()` / `reprint_of()`。

**镜像与分页**：`m.`/`app.`/`a.`/`mip.`/`3g.`/`mob.`/`wap.` 前缀 + `KNOWN_MIRROR_HOSTS` 显式表（站点适配器可再声明）；
分页 `_N.shtml` 与 `?page=N` 收敛到同一 family，`unique()` 优先第一页。
`guides corpus` 现在按 family+host 跳过镜像视图（`SKIPPED_MIRROR`），不再把同一篇文章的移动版当成新语料。

**图片身份**：`assets/phash.py`（DCT pHash，63 bit，阈值 6）→ `AssetCache.store()` 落库 `guide_asset_cache.phash`，
`AssetCache.visual_duplicates()` 给出"同一张图的多种编码"。精确去重仍走 SHA256，pHash 只补"重新编码/缩放"这一类漏网。

**跨站转载**：`reprint_of(url, html)` 读 `<link rel=canonical>` / `og:url`，当自述 canonical 属于**别的站点**时判定为转载。

## ADR-004 Job Resume —— **IMPLEMENTED（Crawler Sprint 4，2026-10-03）**

**决定**：引入最小 job model（`guide_job`：job_type / topic / state / cursor / budget / counters / completed / failed / error / report_path），
状态 `PENDING → RUNNING → COMPLETED | PAUSED | FAILED`，由 `hsrmap/guides/jobs.py` 与 `guides corpus --resume JOB` 驱动。

**关键语义**：
- **预算是每次运行的硬上限**（max_pages / max_assets / max_bytes / max_runtime / max_failures），计数器是 job 级累计（报告用）；
  因此 resume 一定会取得进展，除非显式抬预算。
- **幂等**：canonical URL、资产 SHA256、article family、review signature 都是唯一键，同一 job 跑两遍不产生两份数据。
- 每次运行写 `crawl-report.json` + `crawl-report.md`（§十八字段，含 Source Yield / Target Yield）。

**代价与重访条件**：cursor 是 URL 列表而非可中断的生成器；若 job 规模到万级 URL，改为 frontier cursor（分片游标 + 持久化优先级队列）。

## ADR-005 Unified AI Cache —— **IMPLEMENTED（2026-10-03）**

**决定**：所有模型答案进同一张 `ai_cache` 表，键固定为
`sha256(provider + model + prompt_version + normalized_input)`（`hsrmap/guides/ai_cache.py`）。
`prompt_version` 必须在键里——否则改了 prompt 却命中旧结果，是最隐蔽的数据污染。

**形态**：
- `AICache(db=…, root=…)`：DB 为主、磁盘为可选镜像、内存为 L1；没有 DB 时只用磁盘/内存（跨进程仍可命中）。
- `CachedProvider` / `wrap_provider()`：包住**任何** provider，逐方法缓存（`extract_sections` / `classify_article` / `read_region` …），
  不按 Topic 各建一套；图片等 bytes 入参按 sha256 参与键，键保持短小稳定。
- `build_provider(..., db=…)` 直接返回带缓存的 provider；`guides ai-cache [--invalidate-prompt V]` 查看与失效。

**代价与重访条件**：缓存值按方法参数整体 JSON 化，超大输入（整页 base64 图）会以哈希参与键、原始入参不落库；
若将来需要审计"这次回答用了哪张图"，在 `ai_cache` 上加 `input_ref` 列存资产 sha。
