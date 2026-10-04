# Phase 7 · Map Graph（a1-8-1 全量地图）—— 进度日志

规格：`docs/specs/a1-8-1.md`。运行手册：`docs/runbooks/map-graph-m7.md`（含 §8 的 §24 逐条对照）。
交接文档：`m71-graph-schema.md`（schema 与接口）、`m72-discovery.md`（五个 extractor / Scanner / 回填 / 审计）。

## 一句话结论（M7.0 侦察的反转）

> **深层地图本来就在快照里（923 节点逐 id 全等），我们缺的是「名字、边、入口」三样。**

187 个跳转目标 187/187 都在 `maps` 表、都有切片、本地文件缺失 0 —— 所以 M7 对这份快照是**纯增量**，
不触发 sync 的 ±20% 基线门，也不需要下载任何新资源。

## 已完成

| 阶段 | 内容 | 证据 |
| --- | --- | --- |
| M7.0 Contract Probe | 真实链路 `943 → point 5637 → related_jump_id 979`；五个 extractor 的字段清单；bundle callsite；可渲染判据；ID 空间重叠的陷阱 | `docs/runbooks/map-graph-m7.md` §1/§2 |
| M7.1 Graph Schema | `map_edges` + `point_transitions`；`tree_leaf` / `is_renderable` 拆开（取消 `node_type==2 and not children`）；`render_probe`（VALID/INVALID/UNKNOWN + 理由）；`graph.closure()` | `tests/test_map_graph.py`（21） |
| M7.2 Discovery | 五个 extractor + ID Reference Scanner v1 + 离线回填 + 孤儿发现 + Graph Audit + `graph backfill\|audit\|orphans` | 回填实测 914/204/36/187；`tests/test_map_graph_discovery.py`(32) / `_backfill.py`(26) |
| M7.6 口径 + 门禁 | `NAVIGABLE_EDGE_TYPES`（可导航）/ `STRUCTURAL_EDGE_TYPES`（结构，目标可以是容器）；`unresolved_targets(navigable_only=)` 区分 frontier 与门禁 | `docs/runbooks/map-graph-m7.md` §6.2–§6.35 |
| §二十三 Canary | `canary_deep_map_transition` 钉成永久回归（七步链 + gate 输入 + 结构边单列） | `tests/test_map_graph_canary.py`（4） |
| §十一/§十九 导航上下文 | `navigation_context` / `canonical_path` / `map_display_name` / `attach_navigation_context` / `navigation_blob` / `open_graph_connection` | `tests/test_map_graph_nav.py`（8） |
| §十九 matcher 钩子 | `query_candidates(..., navigation_conn=)`：缺省**原样返回同一列表**，给图库才加字段 | `tests/test_guide_candidates_navigation.py`（4，含「命中集合不变」证明） |
| §二十 进度图口径 | `progress/atlas.py::graph_coverage`（624 可渲染 / 187 可导航 / 5330 点位 / unresolved 0）+ `remaining_atlas(..., core_conn=, graph_conn=)` | `tests/test_progress_graph_coverage.py`（4） |

## 进行中（本轮的两个子代理）

* **M7.3 深层同步**：`map/info` 抓取范围 624 → **923 节点（含 299 容器）**，从 `children[].name` 补 `maps.display_name` + `name_source`（`SCHEMA_VERSION 2→3`，加性迁移）；两阶段同步（Pass A cheap / Pass B suspicious point expansion）+ 闭包 frontier；把 `graph audit` 门禁接进发布校验（gate 不过**不许切 current.json**）；跑真实 sync 出新快照。
  名字缺口硬数字：**341/624 可渲染地图在树里没名字、142/187 跳转目标没名字**。
* **M7.4/M7.5 Graph API + Viewer**：`/api/v1/map/graph`、`/api/v1/maps/{id}/transitions`、点位 `transition`；读取优先级 `core.db → <data>/graph/core.db → 如实说没有`；前端「前往对应地图」+ `origin_map_id` 导航栈实现**精确返回来源点位**；深层图**不塞回 tree**。

## 关键决定（都写进 runbook，别改回去）

1. **边存哪里**：新快照 → `core.db`；现有冻结快照**不许写** → 旁挂 `<data>/graph/core.db`；Viewer 按上面的优先级读，读不到就如实降级（§6.2）。
2. **发布 invariant 的范围**：只对 `NAVIGABLE_EDGE_TYPES`（POINT_JUMP/PORTAL/RETURN/UNKNOWN_TRANSITION）生效；结构边（TREE_CHILD/FLOOR/RELATED_MAP/MAP_GROUP）的目标本来就可以是容器节点，单独统计（§6.3）。
3. **UNKNOWN_TRANSITION 必须保留**：不知道语义 ≠ 可以丢 target（§五）。
4. **禁止用 id 区间猜语义**：187 个跳转目标里 180 个同时也是 label id、150 个也是 point id；判「是不是地图」必须**探测**（`map/info` + detail/slices）。
5. **名字不许编**：`maps.display_name → maps.name → 树名 → 空串`，缺名字就如实空着（`graph_nav` 三处都这么写）。
6. **cycle 合法**：`A→B→A` 是导航常态，遍历靠 visited 收敛，不要求 DAG（§十八）。

## 已知问题 / 未完成

* §24 还有 5 条 ⏳：deep map 有 map/info、可进入、可返回入口、matcher 的**调用方**接线（钩子已就绪）、sync 发布门禁接线 —— 前四条等 M7.3/M7.5 落地，第五条 M7.3 在做。
* **Guide 抽取丢楼层**（既有）：`regions/units.py` 已加 `section_floor_label()`（楼层从「区域名 + heading 原文」两处解析，`filter_by_floor` 重新有数）；但 `tests/test_guide_ingest.py` 要的是 `map_name` 本身带楼层 —— 那属于 `sections.py` 的命名策略，必须连 Guide 回归一起改。
* **冻结快照字节 sha 变过**：一次既有测试用可写方式打开它，M7.1 的加性迁移当场执行（数据行未动）。已逻辑还原（`integrity_check=ok`、golden 误差 0、923/624/5330/1016 不变），并把该测试、`hsrmap status`、`hsrmap validate` 全部改成只读打开；但**字节层面无法还原**（多出 DROP 留下的 freelist 页），发布说明里要如实写。
* **matcher 调用方**：目前没有任何生产调用传 `navigation_conn` —— 缺省行为与以前完全一致（这也是「零退化」的证明方式）。

## 第 18–22 轮的补充（事故、加固、文档）

* **深层同步正在跑**（staging 观测）：`map_nodes 923 → maps 624 → label_nodes 1016 → points 4.8k/5.3k`，
  边与门禁在 Pass B；指针在同步期间保持指向旧快照，最后才原子切换。
* **事故：`data/current.json` 被写成哨兵值** `{"snapshot_id": "OLD"}`（14:13:58）。
  `sync.py` 里没有 "OLD" 字面量 → 判定是某条测试把**真实指针**当临时变量。已用
  `artifacts/p6/current-before-m73.json` 恢复；并要求相关测试改用 `tmp_path` / `HSRMAP_DATA_DIR`。
* **根治（失败长相）**：`cli._load_current()` 现在当场校验指针 —— 文件缺失 / 非法 JSON / 缺 `core_db` /
  指向的库不存在，四种坏法各给一条人话（rc=1，无 traceback）；回归测试
  `tests/test_runtime_paths.py::test_status_fails_loudly_when_the_pointer_is_bogus`。
* **测试加固**：`test_guides_completeness_does_not_touch_repo_data` 加前提检查（sync 锁存在、或相邻两次采样
  就不一致 → 如实 skip）；canary 参数化到**所有**图来源（旁挂库 + 新快照 core.db）。
* **§十九 接了两条真实路径**：地图 API 的 `navigation_context`（实测 200，943 → 979）；
  审核队列的候选点 `navigation` 紧凑块（`review/service.py::navigation_for_candidates`，只对深层地图加）。
* **§二十 进度图口径进 CLI**：`progress remaining` 末尾打印「可渲染地图 / 边 / 可导航 / 深层地图 / 未解析目标」，
  `--json` 里是 `graph` 块。
* **README** 新增英文 “Map graph (Phase 7)” 一节（为什么 / 三样缺口 / 硬规则 / 命令 / canary）。
* **验收清单** 落在 runbook §6.8（新快照落地后照着跑），**回滚安全网** 在 §6.7。
* 新快照落地后，旁挂 `data/graph/core.db` 就退化成**兜底**（Viewer 优先读 core.db 里的 `map_edges`）：
  可以保留（canary 会两个来源都跑）或删掉重新 `graph backfill --out` 生成。

## 同步实测（staging 快照，2026-10-04 14:2x，发布前的直接读数）

```text
map_nodes 923 · maps 624 · points 5330 · label_nodes 1016 · map_fragments 624
map_edges 1341（TREE_CHILD 914 / POINT_JUMP 187 / RELATED_MAP 204 / MAP_GROUP 36）
point_transitions 187
display_name 非空 624/624（来源全部是 map_info:children[].name）
可渲染地图「display_name 与 name 都空」= 0     ← 名字缺口归零
仅靠 display_name 才有名字的 = 341             ← 正是侦察时那 341 条
跳转目标无名 = 0                               ← 原来是 142
样本：943 → 「2层」；979 → 「1」（深层图终于有真名）
```

**口径提醒**：`map_nodes.name` 里仍是 341 条空名 —— 这是**故意**的（`maps.display_name` 不覆盖树名）。
判断「还有没有名字」必须用 `display_name ∪ maps.name ∪ 树名`，否则修好之后还会一直看到 341。

## 本轮数字（可复现）

* `pytest -q` → 792 passed / 106 skipped（0 failed）
* `pytest -q --run-data-e2e` → 885 passed / 1 failed（唯一红是「Guide 抽取丢楼层」，与 M7 无关）/ 2 skipped
* `hsrmap dod` → PASS（12/12）；`hsrmap doctor` → PASS（首屏 18 请求全 200，新 bundle 431 KB 在预算内）
* `hsrmap guides closure-check` → PASS，证据 digest `cd08466d868cc25d`（与改动前一致）
* 图审计：`tree_nodes 923 / renderable 624 / edges 1341 / point_transitions 187 / orphan 0 / unresolved 0 / closure converged`
