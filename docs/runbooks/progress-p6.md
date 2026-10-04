# Runbook · Phase 6 个人进度层（a1-9 P6.0–P6.7）

**状态（2026-10-04）**：P6.0–P6.5、P6.7 已落地并通过全部现有硬门；
P6.6（Viewer 集成）随 Web 构建一并交付；
**Gate 0（2×2 语义实验）尚未执行** —— 它需要一次真实账号的只读探针，
所以「远端状态」这一半目前刻意保持不可推导（见 §3）。

| 步骤 | 交付物 | 证据 |
| --- | --- | --- |
| P6.0 合同发现 | `phase1/endpoint_registry.json` v2：20 个端点带 method/auth/mutating/verified，`realms` 分离 cn / global | `python -m hsrmap progress endpoints` |
| P6.1 凭据边界 | `hsrmap/progress/{realm,endpoints,cookie,adapter}.py` | `tests/test_progress_boundary.py` |
| P6.2 语义探针 | `hsrmap/progress/{hoyolab,probe}.py`：role → treasure → point status，只出报告 | `tests/test_progress_probe.py` |
| P6.3 观察存储 | `progress_profile` / `progress_observation`（user.db schema v2） | `tests/test_progress_store.py` |
| P6.4 进度差异 | `local_only / remote_only / both / unknown`，默认 dry-run | `tests/test_progress_store.py` |
| P6.5 剩余清单 | `remaining = 官方可收集 − 有效完成`，join 坐标 / LOCATE / SOLVE / 攻略 | `tests/test_progress_atlas.py` |
| P6.6 Viewer 集成 | `/api/v1/progress/{status,points,atlas}`（**只读**）+ 地图过滤器 + 同步状态 | `hsrmap/viewer_app.py`；Web 构建产物 |
| P6.7 路线规划 | `hsrmap/progress/routes.py`：区域聚类 + 坐标就近排序 | `tests/test_progress_routes.py` |

## 1. 三条命令

```bash
# 端点合同（离线，不需要凭据）
python -m hsrmap progress endpoints

# 语义探针：只读、打码、不写 user.db（把 cookie 放进环境变量，别贴进任何聊天/日志）
python -m hsrmap progress probe --realm cn --out artifacts/p6/probe-cn.json

# 进度现状 / 合并（默认 dry-run）/ 剩余清单 / 路线
python -m hsrmap progress status
python -m hsrmap progress merge --semantics map_mark          # 只出计划
python -m hsrmap progress merge --semantics map_mark --confirm # 真的写（仍只写 completed=1）
python -m hsrmap progress remaining --limit 12
python -m hsrmap progress routes --max-routes 6 --actions 8
```

## 2. 凭据纪律（不可协商）

* 唯一入口是环境变量 `HSRMAP_HOYOLAB_COOKIE`；**代码里没有第二个读法**。
* 绝不打印、绝不落盘、绝不进异常文本：`Credential` 的 `repr/str/format` 一律 `<redacted>`，
  `__reduce__` 直接抛 `TypeError`（pickle 不了），异常里只有脱敏后的 retcode / 端点名。
* 观察表里只有打码 UID（`12***89`），完整 UID 不落库；`store.summary()` 里 `stores_cookie: false`。
* 发布前跑 `python tools/privacy_scan.py submit`：**未复核命中必须是 0**（退出码 0）。
  扫描器分三层：模式 → 形态（全大写常量名 / 公共安装路径 / 保留域名邮箱都算噪音）→ 复核白名单
  （`tools/privacy_allowlist.json`，逐条写理由）。**凭据类命中永远不许进白名单** ——
  白名单是为了少噪音，不是为了给真凭据放行。

> 若不小心把 cookie 贴进了任何聊天窗口 / issue / 日志：**先去官网登出所有设备（并改密码）**，
> 让那串 token 失效，再重新登录取一份新的。凭据一旦离开你的机器就按已泄漏处理。

## 3. Gate 0：为什么「远端状态」现在不参与完成判定

三种「完成」不是一回事：

```text
manual          玩家在本程序里勾的          → 无条件算完成
map_mark        官方互动地图上的标记        → 默认不算完成
game_obtained   游戏内真实开箱              → 只有实验证明后才算完成
unknown         来源说不清                  → 永远不算完成
```

代码里的开关是 `hsrmap/progress/models.py` 的 `VERIFIED_SEMANTICS`，**当前是空集**。
它为空时，`resolver` 只承认 `manual`；远端观察照常入库、照常展示（`unclear` 桶），
但一个 `completed` 都不会因此产生。这条的机器证据：
`test_unverified_remote_never_derives_completed`、`test_unknown_semantic_never_derives_completed`。

实验（a1-9 §12 的 2×2）拿到结论后，改法只有一行：把结论那一项加进 `VERIFIED_SEMANTICS`，
并在提交信息里附上证据（哪个账号、哪张图、游戏内计数 vs 地图计数）。

## 4. 合并为什么必须显式

* `progress merge` 缺省 dry-run：连 user.db 都不建（看一眼计划不该在磁盘上留东西）。
* `--confirm` 必须同时 `--semantics <名字>`：不点名语义就当用法错误（rc=2），**一个字节都不写**。
* 合并只写 `completed=1`，从不写 0：报告里 `wrote_completed_false` 永远是 0。
* 远端失败 / 断网时，本地 `completed` 一个都不变（`test_remote_failure_leaves_local_progress_untouched`）。

## 5. 剩余清单与路线：不编数据

* `remaining = 官方可收集集合（1006）− 有效完成集合`；
* 坐标、LOCATE/SOLVE 证据、攻略标题都来自现有 Guide Atlas 的同一份判定行，不另造真相；
* 路线只做「聚类 + 坐标就近排序」，距离写成 `distance_units`（地图坐标单位），
  `eta` 恒为 `null` 并注明「没有真实移动速度数据，不给分钟数」。

## 6. Viewer 侧

`GET /api/v1/progress/status`、`/points`、`/atlas` 三个**只读**接口；
地图进程不持有 cookie、不发外网请求（响应里带 `viewer_network: 0`）。
合并与同步只走 CLI —— 界面上只显示命令行文本，**没有按钮**。

界面上的三个控件（全部默认不改变原视图）：

| 控件 | 作用 | 边界 |
| --- | --- | --- |
| 过滤器 全部 / 剩余 / 已完成 / 冲突 | 只影响地图标记 | `unclear` 与 `conflict` 同档展示：两者都不等于完成 |
| 剩余清单抽屉 | 按 大区 → 地图 → 点位 列剩余点位；点一下跳转并开详情 | 它不过滤地图，也不改任何状态 |
| 地图设置 · 隐藏已标记完成的点位 | **只隐藏** `completed` 的标记 | `remaining` / `conflict` / `unclear` 一律不受影响；底部提示写的是**可见**数量 |

## 7. §18 十五条硬门 → 机器证据

| # | 硬门 | 证据（可执行） |
| ---: | --- | --- |
| 1 | 无 Cookie 时所有现有功能完全不受影响 | `doctor` PASS（地图/审核台首屏 18 个请求全 200）；`test_without_a_cookie_the_probe_says_so_and_stops`；`test_no_network_at_import_time` |
| 2 | Phase 1 仍然 credentials-free | `test_progress_boundary.py`：`hsrmap_phase1/client.py` 里既没有 cookie 也没有 `api_post` |
| 3 | Cookie 不进 repo / db / logs / reports | `test_observation_store_has_no_secrets`（库里搜不到 ltoken/完整 UID）；`privacy_scan.py submit` → 0 未复核命中 |
| 4 | HTTP 异常不包含 Cookie | `test_progress_probe.py`：`ProgressApiError` 文本里有 retcode、没有凭据 |
| 5 | release/privacy scan 可识别凭据泄漏 | `test_privacy_scanner_flags_a_leaked_cookie`：临时目录里放一串真形状的凭据 → 扫描器 rc=2 |
| 6 | 所有 mutating endpoint 框架层禁止 | `test_progress_reads_are_allowed_and_writes_are_refused`（`read_contract` 抛 `WriteEndpointForbidden`） |
| 7 | remote sync 默认 read-only + dry-run | `test_merge_is_dry_run_by_default`（一个字节都不写）；`test_cli_merge_confirm_without_semantics_is_a_usage_error`（rc=2 且不建库） |
| 8 | remote status 不静默覆盖 manual progress | `test_merge_never_flips_completed_back_to_false`；`test_diff_does_not_write_anything` |
| 9 | unknown semantic 不允许推导 completed | `test_unverified_remote_never_derives_completed`、`test_unknown_semantic_never_derives_completed`、`test_unknown_observations_are_never_mergeable`、`test_unknown_semantics_never_reduce_the_remaining_count` |
| 10 | app_version 自动发现失败 fail closed | `test_progress_probe.py`：`AppVersionUnavailable` → `reason=app_version_unavailable`，不继续请求 |
| 11 | API schema drift 可检测 | `hsrmap/progress/shapes.py`（只记字段与类型）＋ `progress drift`（0 = 无漂移 / 2 = 有漂移）＋ `tests/test_progress_drift.py` |
| 12 | CN / Global contract 分离 | `test_progress_boundary.py`：两个 realm 的 game_biz / binding_host / map_host 各自独立，不互相借用 |
| 13 | progress import 可重复执行且 idempotent | `test_observation_upsert_is_idempotent`；`test_merge_applies_only_true_and_is_idempotent`（第二次 planned=0） |
| 14 | 断网不破坏已有 user.db | `test_remote_failure_leaves_local_progress_untouched`；`test_progress_endpoints_make_no_outbound_connection`（堵掉出网传输后三个接口照常 200） |
| 15 | remote failure 不改变任何 completed | 同上两条 ＋ 合并只写 `completed=1`（报告里 `wrote_completed_false` 恒为 0） |
| 16 | Guide Atlas 现有 closure / evidence / publish gates 零退化 | `dod` = PASS（12/12）；`closure-check` PASS；六状态矩阵 sha256 == S0 基线 |

## 8. 本轮验证

| 项目 | 结果 |
| --- | --- |
| `pytest -q` | 700 passed / 89 skipped |
| `ruff check hsrmap tests tools` | All checks passed |
| `python -m hsrmap dod` | PASS（12 通过 / 0 失败 / 0 跳过） |
| 六状态矩阵 sha256 | 仍等于 S0 基线（closure PASS，11 栏） |
| 发布包隐私扫描 | `python tools/privacy_scan.py submit` → 0 命中 |
