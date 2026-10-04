# Runbook · S8 Web：让用户看见「为什么算完成」（a1-8 十三）

**状态：完成（2026-10-04）**。判定语义不变：证据是**注解**，六状态与 1006/1006 都没有动。

## 1. 后端：证据 API

新增**只读**的攻略查询面 `guide_router`（地图页与审核台都挂，写端点仍然只在审核台）：

| 端点 | 作用 |
| --- | --- |
| `GET /api/v1/guides/by-point/{point}` | 这个点位的攻略条目（地图页画「有攻略」标记） |
| `GET /api/v1/guides/index` | 点 -> 攻略数 |
| `GET /api/v1/guides/atlas` | 主题覆盖 |
| `GET /api/v1/guides/evidence` | **证据总览**：完成度分层 + 声明等级 + digest |
| `GET /api/v1/guides/evidence/{point}` | **点位详情**：完成判定 + 每条攻略的逐步证据 |

点位证据逐条给：`claim_kind` / `evidence_level` / `grounding_tier` / 来源页 / 官方点位 /
图片 sha / `basis`（推断依据）/ `method_version`，外加两个**给界面用的布尔位**：
`inferred`（= CROSS_INFERENCE）与 `transcribed`（= TRANSCRIPTION）。
`CROSS_INFERENCE` 因此不可能和正文引证长成一个样子——它是接口层面就分开的两个字段。

判定行来自 `stages.point_report()`，而它和 `completeness_report()` 共用同一个 `_point_row()`：
**详情页说的「为什么算完成」和总览里的 1006/1006 是同一句话**，不是第二套实现。

## 2. 地图进程的只读面（顺带修掉一个真 bug）

以前 `/api/v1/guides/*` 全在审核路由组上，而 **map-only 进程返回 404**：
地图页的点位抽屉永远显示「暂无本地图文攻略」，「有攻略」标记也画不出来。
S8 把它们收进 `guide_router`，两边都挂：

* 地图进程：`published.db` **只读**打开，写端点一个都没有（`POST /api/v1/guides` → 404）；
* 没有发布快照时如实返回 `available: false`，地图照样起得来；
* 审计状态（逐条问题）只有审核进程算得出来（要读来源页正文），地图进程如实说「未附带审计状态」。

**同时修掉**：路由组以前是模块级 `APIRouter()`，第二次 `create_app()` 会把重复路由累积到同一份
路由表上，而先注册的那条闭包指向上一个 app 的 state——一个进程里建两个 app（测试、`offline_e2e`）
时，第二个 app 会拿着第一个 app 的数据库句柄。现在路由组在每个 app 里各建一份。

## 3. 前端：点位详情与 Atlas

点位抽屉新增：

```text
完成判定
  完成（定位 + 解法）        要求 LOCATE_AND_SOLVE · INPUT_SEQUENCE
  定位证据 官方　解法证据 图解转录
  主攻略 …（Community）
图文攻略
  Step 1 …                  [正文引证]
  Step 5 …                  [图解转录 dd3e3536]  -> 查看证据图 ↗
                            落地 指向图片 · 官方点位 3556 · 来源页 #12
```

* 证据徽章按等级分色：官方 / 正文引证 / 图解转录 / **交叉推断（红，且写「推断，不是原文」）**；
* 展开块给出落地方式、官方点位、来源页、方法版本、推断依据、证据图链接；
* 审核进程下还会显示该条目的审计问题。

Atlas（攻略中心）从「1006 / 1006」升级为分层：

```text
完成与证据
  已完成            1006 / 1006
  正文引证即可       960
  需要图解转录        46
  需要交叉推断         0
  未完成               0
  声明 5079 条 · 证据摘要 cd08466d868c…
```

## 4. 证据（测试与构建）

* `tests/test_serve_evidence.py`（7 条）：证据总览的分层与等级计数、逐步声明、
  CROSS_INFERENCE 必须带 `inferred: true` 与 `basis`、地图进程只有只读面（写端点 404）、
  没有快照时 `available: false` 而不是 500、审核台保留写面、
  真实点位 3556 的「解法靠图解转录」（数据标记）；
* 前端：`tsc --noEmit` 无错误，`vite build` 成功（`web/dist/app/index-CV9lyJAN.js`），
  产物里能查到「完成判定 / 交叉推断 / 需要图解转录」等界面文案；
* 现场复核：`closure-check` PASS、`repo-hygiene` PASS、`pytest -q` 全绿。

## 5. 与硬约束的关系

证据全部是**读**出来的注解：`evidence_level` 由 `claims.classify_step`（纯函数）给出，
Web 只是把它显示出来。所以 S8 不改变任何判定，也不产生第二条真相路径。
