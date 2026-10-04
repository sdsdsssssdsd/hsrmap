# Runbook · 启动入口与闭环自检（2026-10，用户「启动入口打不开」）

## 症状与根因

用户双击 `start.bat`「打不开」。全盘审查后确认了四个真实原因（都不是「没装 Python」）：

| # | 根因 | 现象 |
| --- | --- | --- |
| 1 | `.bat` 结尾没有 `pause` | 只要服务退出（端口被占、缺库、Ctrl+C），窗口**一闪而过**，用户看不到任何报错 |
| 2 | 端口被上一个进程占着时 `run_server` 直接 return | 第二次双击等于「什么都没发生」 |
| 3 | **监控台和审核台抢同一个端口 8767**，`stop_monitor.bat` 还会 `taskkill` 掉正在用的审核服务 | 先开监控台就再也打不开审核台 |
| 4 | 审核台首屏 `/api/v1/review/items` 一次返回 **142 MB / 17 秒** | 页面长时间空白，浏览器看起来像卡死 |

顺带一处：Windows 控制台默认 GBK，CLI 的中文提示会变成乱码——所以「报错」本身也读不懂。

## 修了什么

### 1. 入口本身（`start.bat` / `start_review.bat` / `monitor/start_monitor.bat` / `framework/start.bat`）

* 先 `where python`：找不到就打印人话 + `pause`，不再一闪而过；
* 结尾一律 `pause`，并打印退出码——报错留在屏幕上；
* 注释里写明端口与「关掉窗口就停止服务」；
* 服务仍然自动打开浏览器（`hsrmap serve` 的默认行为）；新增 `--no-browser` 给无头/CI 用。

### 2. 端口分工（三个服务互不抢）

| 服务 | 端口 | 入口 |
| --- | --- | --- |
| 离线地图 | 8766 | `start.bat` |
| 审核台 | 8767 | `start_review.bat` |
| 实时监控台（只读） | **8768**（原 8767） | `monitor/start_monitor.bat`（`stop_monitor.bat` 同步改成 8768） |

监控台以前写死 `ROOT/data`：运行目录外置之后它会指着一个空目录说「没有数据」。
现在走 `hsrmap.runtime` 解析（解析不出来才退回 `<repo>/data`）。

### 3. 起飞前检查（`hsrmap/serve.py::preflight`）

* 地图：缺 `web/dist` → **致命**（起来也是白屏）；缺快照 → 提醒；
* 审核台：缺 `guide.db` / `published.db` → **致命**（以前是 uvicorn 抛一段栈）；
* 端口被占 → 打印「端口 N 上已经有一个服务在跑：<url>（刚给你打开了它的页面）」。

### 4. 审核队列：列表是索引，详情按行取

`/api/v1/review/items` 的返回从 **142 MB** 降到 **0.84 MB**：

* `items`（2551 条逐页原始记录，127 MB）默认不返回——列表页从来不看它；
  要就 `?include_items=1`；
* 每行默认只带索引（`map_name` / 计数 / 候选点 / `draft_brief`），
  `draft`/`layout`/`layout_html`/`evidence`/图片数组由 **新增的
  `GET /api/v1/review/maps/{item_id}`**（21 KB，85 ms）在选中时补齐；
  要看完整行用 `?full=1`；
* 控制台 `console.js` 的 `openRow()` 负责这次补取，取不到就退回索引行（点得动，只是没详情）。

### 5. 逐页图片清单进缓存（迁移 7 `page_image_cache`）

`page_images()` 每次都要读并解析 `extracted/<page>.json` 与
`derived/<page>/image-roles.json`；2551 条队列 = 每次请求 ~7 秒的纯 I/O。
现在按「文件指纹（mtime+size）」缓存进库，并且**批量取**（页面路径、缓存行都是一次查询）：

| 阶段 | 首屏耗时 | 载荷 |
| --- | ---: | ---: |
| 修之前 | 17.3 s | 142.6 MB |
| 只去掉重复的 `items` | 9.2 s | 9.8 MB |
| 图片清单缓存 + 批量取 + slim 行 | **2.4 s** | **0.84 MB** |

只读打开（published 库）**不写**这张缓存表：算得出就返回，不偷偷建表。

### 6. UTF-8 输出（`cli.use_utf8_output()`）

入口先把 stdout/stderr 切 UTF-8 并设置 `PYTHONIOENCODING`：中文报错不再变成乱码，
子进程（审计、pytest、framework/build.py）跟着一致。

## 闭环自检：`python -m hsrmap doctor`

```bash
python -m hsrmap doctor            # 逐项判定 + 退出码（0 = 闭环成立，2 = 有失败）
python -m hsrmap doctor --json-out
```

四项检查：

1. **入口文件与它们引用的脚本**：四个 `.bat` + 快捷页都在，`framework/build.py`、
   `monitor/monitor_server.py` 真的存在，快捷页指向 8767；
2. **端口**：三个端口互不冲突，且与 `hsrmap.serve` 的常量一致，`stop_monitor.bat` 停的是同一个；
3. **起飞前检查**：地图与审核台都没有致命项；
4. **首屏闭环**（进程内 TestClient，不占端口）：把浏览器第一次打开两个页面要发的请求
   **全部发一遍**（首页 → `/app/*.js|css` → tree/index/evidence → 落点地图 → 点位 → 审核队列 → 单行详情），
   任何一步不是 200 就是断了；同时卡**载荷预算**（队列 ≤ 4 MB、tree ≤ 2 MB、单行 ≤ 1 MB），
   这类「页面打不开」的根因从此会变成失败的检查。

现场：**DOCTOR RESULT = PASS**，18 个首屏请求全 200，最大载荷 861 KB。

## 证据

* `tests/test_doctor.py`（9 条）：入口齐全、端口不冲突且与服务常量一致、
  起飞前检查通过、四个 `.bat` 都有 `where python` 与 `pause`（防「一闪而过」回归）、
  首屏闭环与载荷预算、doctor 报告结构、CLI 退出码、
  审核队列「索引 + 按行详情」的形状（`slim` 行不含重字段、`maps/{id}` 补齐、
  `full=1` 保持老形状、未知 id 404）、迁移 7 的图片缓存命中且返回拷贝；
* `tests/test_workflows.py`：CLI 子进程的中文报错在**不设 PYTHONIOENCODING** 的环境下也能被断言到
  （这条以前靠外部环境变量才能过）。
