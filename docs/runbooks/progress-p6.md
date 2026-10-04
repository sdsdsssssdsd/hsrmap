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
* 发布前跑 `python tools/privacy_scan.py submit`：命中数必须是 0。

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

## 7. 本轮验证

| 项目 | 结果 |
| --- | --- |
| `pytest -q` | 700 passed / 89 skipped |
| `ruff check hsrmap tests tools` | All checks passed |
| `python -m hsrmap dod` | PASS（12 通过 / 0 失败 / 0 跳过） |
| 六状态矩阵 sha256 | 仍等于 S0 基线（closure PASS，11 栏） |
| 发布包隐私扫描 | `python tools/privacy_scan.py submit` → 0 命中 |
