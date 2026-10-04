# Runbook · 审核台与离线地图是两个服务（2026-10，用户要求「不要耦合」）

## 一句话
审核网页与离线地图各自独立启动、独立端口、独立数据依赖：地图进程不需要攻略库，审核进程不需要快照。

## 怎么用

| 想干什么 | 命令 | 地址 |
| --- | --- | --- |
| 离线地图 | `python -m hsrmap serve --app map`（或双击 `start.bat`，端口 8766） | http://127.0.0.1:8766/ |
| 审核台 | `python -m hsrmap serve --app review`（或双击 `start_review.bat`，端口 8767） | http://127.0.0.1:8767/review |
| 实时监控台（只读，另一个工具） | 双击 `monitor/start_monitor.bat`（端口 **8768**） | http://127.0.0.1:8768/ |
| 老用法（一个进程两个都挂） | `python -m hsrmap serve --app all --port 8766` | http://127.0.0.1:8766/review |

`打开审核台.html` 已指向 8767；两个 `.bat` 都会各自打印自己的地址与另一个的地址。

**三个端口互不冲突**：监控台以前写死 8767，会和审核台抢端口（`stop_monitor.bat` 还会把审核服务杀掉），
已改到 8768。启动前想确认这台机器上闭环是通的，跑 `python -m hsrmap doctor`
（入口 / 端口 / 起飞前检查 / 首屏请求与载荷预算，见 `docs/runbooks/entry-closed-loop.md`）。

`--no-browser` 只起服务不开浏览器（无头环境/自动化用）；缺库或缺前端产物时不再抛栈，
而是打印一条人话并返回退出码 2。

## 依赖边界（谁需要什么）

| 服务 | 需要 | **不需要**（缺了也能起来） |
| --- | --- | --- |
| 离线地图 `map` | 当前快照（`current.json` / `snapshots/*/core.db`）、`web/dist`、`user.db` | `guide.db`、`published.db`、`guide-assets` |
| 审核台 `review` | `guide.db`（读写，但不建库）、`published.db`（只读）、`guide-assets`、`user.db` | 快照、`core.db`、`web/dist` |

路由分组（`hsrmap/viewer_app.py` 里的三个 APIRouter）：

* `map_router`：`/api/v1/meta|debug|search|data-source|maps|points|live-assets`、`/assets/{sha}`、`/app`（SPA 静态）、`/`（SPA 首页）
* `review_router`：`/review`、`/review.js`、`/guide-assets/{sha}`、`/api/v1/guides|atlas|review|topics`
* `shared_router`（两边都挂）：`/health`、`/api/v1/user/*`、`/api/v1/settings`、`/api/v1/updates/check`

只跑审核台时根路径 `/` 会 307 跳 `/review`（不然一片 404 会让人以为服务没起来）。

## 证据

* `tests/test_serve_split.py`（5 条）：`resolve_kind`/`resolve_port` 的默认值；
  map-only 在**没有** guide.db/published.db 时能建起来且不会顺手建库、`/api/v1/review/items` 404；
  review-only 在**没有**快照时能建起来、`/api/v1/maps/tree` 404、`/` 307；
  `sections=("map","review")` 仍然两者都在（老用法回归）。
* 真机冒烟（本机，两个进程同时起）：
  `map 8791` → `/` 200、`/health` 200、`/api/v1/review/items` **404**；
  `review 8792` → `/review` 200、`/health` 200、`/api/v1/maps/tree` **404**、`/` **307**。

## 顺带修掉的两个「缺数据就 500」

* `/health` 不再因为快照还没同步而 500：没有绑定时报 `core: MISSING`、没有攻略库时报 `guide: NOT_MOUNTED`，状态码仍是 200。
* `bind_viewer()` 遇到 `current.json` 缺失时，地图端点返回 **503「快照未就绪：先跑 python -m hsrmap sync」**，而不是 500 堆栈。
* 同时把 `hsrmap/viewer_bind.py` 的路径改成**调用时解析**（`hsrmap.paths` 惰性属性）：
  长驻进程里 import 时算死的常量会锁住错误的运行目录（这正是这条 503 一开始测不出来的原因）。
