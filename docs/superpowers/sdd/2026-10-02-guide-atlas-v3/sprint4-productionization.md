# Crawler Sprint 4 — Productionization

Spec: `a1-6.md` §十六（Job Budget）/ §十七（Crawl Resume / Idempotency）/ §十八（Observability）/ §二十（Fixture Corpus）/ §二十四 Sprint 4。
退出条件：**一次 corpus job 可以暂停、恢复、复盘、重跑，而且结果幂等。**

## 1. Job model（`hsrmap/guides/jobs.py`）

```text
guide_job: job_type / topic / state / cursor / budget / counters / completed / failed / error / report_path
PENDING -> RUNNING -> COMPLETED
                   -> PAUSED   (预算用尽，之后 resume)
                   -> FAILED   (失败数超限或异常)
```

```bash
python -m hsrmap guides corpus --topic jump --max-pages 100 --max-assets 500 --max-runtime 1800
python -m hsrmap guides corpus --resume 7 --max-pages 200        # 抬预算继续
python -m hsrmap guides job-status --job 7
python -m hsrmap guides job-list --topic all
```

**预算语义**：`max_pages / max_assets / max_bytes / max_runtime / max_failures` 是**每次运行**的硬上限，
计数器是 job 级累计（报告用）。所以 resume 一定会取得进展，除非显式抬预算——
否则「恢复一个因预算暂停的 job」会在第一次循环就再次停住。

**幂等**（§十七）：canonical URL 唯一、资产 SHA256 唯一、article family 唯一、review signature 唯一。
测试里同一 job 跑两遍，`guide_page` 行数不变。

## 2. Crawl report（§十八）

每次 corpus 运行写 `crawl-report.json` + `crawl-report.md`：

```text
requests / bytes / elapsed_seconds / hosts / http{2xx,3xx,4xx,5xx} / retries / cache_hits
pages{discovered, skipped, imported, failed}
assets{discovered, fetched, deduped, failed}
qa{pass, fail} / targets_matched / review_units
yield{source_yield = QA_PASS / fetched, target_yield = new targets / fetched}
```

没有 job 的运行也会写同样的报告（`_write_plain_report`），所以「每次 corpus 都有报告」成立。

## 3. Fixture corpus（§二十，全离线）

```text
tests/fixtures/crawler/
  3dm/single_page.html      单页 + lazy image（data-original）+ 坏图 + 重复图 + 广告图
  17173/pagination.html     _2.shtml 续页链接（相对路径）+ 侧栏串图链接
  17173/pagination_2.html   末页（无续页）
  17173/mirror.html         移动镜像 + 自述 canonical
  9game/status_403.html     403 fixture
  9game/status_404.html     404 fixture
  9game/html_as_image.html  HTML-as-image（防盗链）
  manifest.json             case → 文件 → 覆盖点
```

`tests/test_guide_crawler_fixtures.py` 全离线跑：blocks 抽取（data-original 优先、空 `<img>` 丢弃、广告丢弃、重复图保留两次）、
`sibling_pages` 只跟同文章续页、镜像与 canonical 判定、403/404/HTML-as-image 变成类型化失败（`HTTP_BLOCKED` / `HTTP_NOT_FOUND` / `NOT_IMAGE`）。

顺带修掉一个真问题：`sibling_pages` 原来只认**绝对** href，相对续页链接（gamersky 风格 `/handbook/.../1729233_2.shtml`）
会被静默漏掉——现在所有 href 先 `urljoin` 再判定。

## 4. Live smoke（§二十后半）

```bash
python -m pytest --run-live tests/test_guide_live_smoke.py   # 手工/受控运行
```

`tests/test_guide_live_smoke.py` 打 `live` marker，默认跳过；内容刻意很小：
3dmgame 两个页面的资产冒烟矩阵（断言不是 Case C 全封锁）+ robots.txt 可读 + 一个真实文章页返回 HTML。

## 5. 测试

- `tests/test_guide_jobs.py`（6）：预算暂停 → resume 完成（幂等，`guide_page` 不增行）、max_failures → FAILED、
  五种预算各自触发、状态机与计数（http 分档 / retries / cache hits / bytes）、报告字段齐全、CLI `job-status` / `job-list`。
- `tests/test_guide_crawler_fixtures.py`（8）：见上。
- `tests/test_guide_live_smoke.py`（2，默认 skip）。
## 10. 僵尸 job 的收尾（`fail_stale_jobs`，2026-10-03）

`guide_job.state = RUNNING` 原本只能由 `finish()` 改写，于是进程崩溃（例：两个 `corpus` 并行写同一个 SQLite，
后到的那个抛 `database is locked`）会留下永远的 `RUNNING`。`job-list` 于是报告一批并不存在的「在跑任务」，
`--resume` 也无从判断该恢复谁。

现在 `guides corpus` 在**创建或恢复 job 之前**调用 `fail_stale_jobs(db, keep=<resume 的那个 id>)`：

- 除 `keep` 之外的所有 `RUNNING` → `FAILED`，错误写「interrupted: no live process owns this job」；
- 只在**确实**要启动新任务时执行，因此不会误伤别的东西；
- 由此得到一条可依赖的语义：**`RUNNING` 表示此刻有进程在跑**。

同一条经验也写进了操作守则：**同一时间只跑一个写 `guide.db` 的任务**（crawl / reingest / rebuild / review-approve）。
## 11. 薄草稿复活（`guides revive-thin`，2026-10-03）

抓取时图片没下来的页面，正文里的走法会被挤成一句「共N个」，于是被 `REGION_SET_TOO_THIN` 正确地拒掉——
但**拒掉的是这一刻的解析结果，不是这个来源**。`reingest-page` 补抓图片并重新解析后，同一页会长出几十条步骤：

```
p82  导入时 1 条实质步骤 / 9 张图缓存  →  补抓后 34 条 / 30 张  →  绑定 20 点
p66  导入时 1 条实质步骤 / 0 张图缓存  →  补抓后 29 条 / 12 张  →  绑定 60 点
```

所以判定一个页面「没有可用正文」之前，先跑一次复活：

```bash
python -m hsrmap guides revive-thin            # 只是列候选
python -m hsrmap guides revive-thin --apply    # 补抓 + 重新判定
```

判定用的是和审批同一条规则（`region_set_target_for`）：能算出整区绑定、有点位没覆盖、但步骤 < 3 条。
`--apply` 之后按 `revived` / `still_thin` 分类输出，**不会**因为补抓就放宽数量门槛。
## 13. 无头渲染：JS 站的兜底抓取（2026-10-03，第 15 轮）

`hsrmap/guides/crawler/render.py` 用**本机已装的 Chrome/Edge** 渲染：

```bash
python -m hsrmap guides corpus --topic T --url <js 站>     # 默认自动重试渲染
python -m hsrmap guides corpus --topic T --url <js 站> --no-render   # 关闭（或 render=False）
GUIDE_BROWSER=/path/to/chrome                             # 指定浏览器
```

触发条件很窄：**只有 QA 判 `JS_RENDER_REQUIRED` 时才渲染**，而且每个 URL 只渲染一次；
渲染后的 DOM 走完全相同的 import / QA / 审批管线，`rendered: true` 会出现在抓取报告里。

为什么不用 Playwright/Selenium：这台机器上已经有浏览器，而 `--dump-dom` 就够用——
**不引入新依赖、不常驻进程、不需要驱动协议**。代价是渲染是「加载完成后 dump」，
对靠接口二次拉正文的站（米游社）无效——那种站渲染出来仍是外壳。

参数选择有实测依据（见 `progress.md` 第 15 轮）：经典 `--headless` + `--timeout=N`；
`--headless=new`、`--headless=old`、`--virtual-time-budget` 在 Windows 上会因 updater 管道被拒而挂死。
