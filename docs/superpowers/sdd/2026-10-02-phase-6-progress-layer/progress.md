# Phase 6 · Personal Progress Layer —— 进度日志

规格：`docs/specs/a1-9.md`。运行手册：`docs/runbooks/progress-p6.md`。

## 已完成

| 步骤 | 内容 | 落地位置 |
| --- | --- | --- |
| P6.0 | bundle 合同再解析：20 端点带 mutating/verified，realms 分离 cn/global | `phase1/endpoint_registry.json`、`hsrmap/progress/endpoints.py` |
| P6.1 | 凭据边界：opaque Credential、脱敏、写接口框架层拒绝、CN/global realm | `hsrmap/progress/{cookie,realm,adapter}.py` |
| P6.2 | 语义探针：role → treasure → point status，只输出诊断报告 | `hsrmap/progress/{hoyolab,probe}.py` |
| P6.3 | 观察存储：`progress_profile` / `progress_observation`（user.db v2），不动 `point_progress` 语义 | `hsrmap/user_db.py`、`hsrmap/progress/store.py` |
| P6.4 | 进度差异四桶 + 显式合并（默认 dry-run，只写 True） | `hsrmap/progress/diff.py`、`resolver.py` |
| P6.5 | 剩余清单：`collectible − effective_completed`，join 坐标/证据/攻略 | `hsrmap/progress/atlas.py` |
| P6.6 | Viewer 只读接口 + 地图过滤器 + 官方地图同步状态 | `hsrmap/viewer_app.py`、`web/src` |
| P6.7 | 路线规划：区域聚类 + 坐标就近排序（不估耗时） | `hsrmap/progress/routes.py` |
| §18.11 | 接口结构漂移：探针记录「形状指纹」（只有字段与类型），`progress drift` 与基线比对 | `hsrmap/progress/shapes.py` |

## 待办 / 阻塞

* **Gate 0 未执行**：需要一次真实账号的只读探针（`progress probe`），
  在此之前 `VERIFIED_SEMANTICS` 保持空集，远端状态只展示、不推导完成。
* 国服 B 站账号（b 服）是否能走官方米游社 cookie 读进度**尚未验证**；
  若不能，这一层就按「手动勾选 + 剩余清单 + 路线」交付，远端同步如实报「不适用」，不假装能同步。

## 顺手修掉的两处「门」的漏洞

* **隐私扫描**：以前「命中数是不是 0」全靠人看，而且把 `C:\Program Files`、示例邮箱、
  全大写常量名这类噪音也算命中。现在分三层（模式 → 形态 → 复核白名单），
  **凭据类命中永远不许进白名单**，有未复核命中就 rc=2；`tools/privacy_allowlist.json`
  里每条都要写理由。发布包实测：0 条未复核。
* **数据套件旧账**：`--run-data-e2e` 下有 16 个测试还在依赖 S9 已经取消的「隐式建库」行为，
  属于旧账而非本轮引入；已按「测试显式建库、产品语义不动」的方向修掉（见该轮提交）。

## 本轮数字

* `pytest -q` → 700 passed / 89 skipped；`ruff` 全绿；`dod` 12/12 PASS；
* 新增测试：`test_progress_store.py`(28) / `test_progress_atlas.py`(10) / `test_progress_routes.py`(9)；
* 剩余清单实测：可收集 1006 · 无进度时剩余 1006 · 278 张图。
