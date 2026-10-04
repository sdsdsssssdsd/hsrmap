# Runbook · M7 Map Graph（a1-8-1 全量地图，必做项 + 发布门禁）

**M7.0 Contract Probe 已完成（2026-10-04，只读侦察）。**

> **M7.2 发现层已完成（2026-10-04，离线）**：五个 extractor + ID Reference Scanner + 离线回填 +
> 孤儿发现 + «python -m hsrmap graph backfill|audit|orphans»。本文件 §5 的 914 / 204 / 36 / 187
> 已在真实快照上逐项复现（**源快照 sha256 未变**），回填库在 «data/graph/core.db»。
> 交接文档：«docs/superpowers/sdd/2026-10-02-phase-7-map-graph/m72-discovery.md»；
> M7.1 schema：«…/m71-graph-schema.md»。
>
> **M7.3 深层同步已完成（2026-10-04，联网）**：新快照 **20261004T060003Z** 自带
> map_edges 1341 / point_transitions 187 / maps.display_name 624；四项计数一个没变
> （923 / 624 / 5330 / 1016），名字缺口 **341/624 + 142/187 → 0 + 0**，发布门禁 PASS，
> 冻结快照 20261001T105105Z 的 sha256 前后一致。详见 §6.4.1 与 «…/m73-deep-sync.md»。

## 0. 最重要的一条结论（先纠正前提）

> **那些「更深的小地图」本来就在我们的快照里 —— 我们缺的不是数据，是名字、边和入口。**

* live `/v1/map/tree` = **923** 个节点，与快照 `map_nodes` **逐 id 完全一致（双向差集为空）**；
* 187 个 POINT_JUMP 目标 **187/187 都在 `maps` 表、187/187 都有 map_fragments、本地切片文件缺失 0**；
* `maps − map_nodes` = 空集；`points.map_id ∉ map_nodes` 的点 = 0。

所以 M7 是**纯增量**：不会触发 sync 的 ±20% 基线门，不需要下载任何新资源。

## 1. 真实链路（canary 样本）

```text
943（千星城 / 千星城中心城区 / 2层）
  └─ point 5637  label 836「二次元JUMP!」  related_jump_id = "979"
        └─ map 979  真名「1」  父容器 955（tree 名「特殊房间」，真名「千星城中心区7」，related_id=939）
              └─ 有 detail（213 B，1 个 slice）→ 可渲染；point/list 有 2 个点
```

*「前往对应地图」= `related_jump_id`（一个 **map id**）；「跳转标点层」= `point/info.map_id`（点自己所属的图），**不是边**。*

## 2. 字段清单（M7.2 extractor 的输入）

| 来源 | 字段 | 产出边 | 本快照产量 |
| --- | --- | --- | --- |
| `/v1/map/tree`（13 键） | `parent_id` / `children` | TREE_CHILD | 914 |
| 同上 | `related_id`（隐藏容器指回可见区域）、`is_hide`（205） | RELATED_MAP | 204 |
| 同上 | `related_group_map`（18 对）+ `map_group_type`（1 昼/2 夜/3 异域/4 异世） | MAP_GROUP | 36 |
| `/v1/map/point/list` | **`related_jump_id`** | POINT_JUMP | 187（6 个 label：836×137、680×18、463×13、545×10、548×5、973×4） |
| `/v1/map/info`（**含 299 个 node_type=1 容器**） | `children[].name` = **真名**、`children[].preview`、`detail`（渲染判据） | TREE_CHILD（带名）+ 可渲染证据 | 改名的 535 条 |
| `/v1/map/point/info` | 同值 `related_jump_id` + `map_id` | 交叉校验 | 187 |
| bundle | `openRedirectConfirmation` / `changeLayer` / `origin_map_id` 栈 / `resolveVisibleRelatedItem` / `changeMapGroup` | RETURN / 交互契约 | — |

**陷阱**：`label.jump_type` / `jump_target_id` 当前 **全 0**（1016/1016），不是 JUMP 来源，但要进 contract registry；id 空间重叠（187 个跳转目标里 180 个也是 label id、150 个也是 point id）→ **禁止按数值区间猜语义，必须探测**。

**返回**：官方**没有**服务端 return 字段，返回只存在前端 URL（`origin_map_id` 逗号栈 + `pushOriginMapId`/`topOriginMapId`）。

## 3. 可渲染探测（取代 `is_renderable = node_type==2 and not children`）

权威判据 = `/v1/map/info` retcode 0 **且** `detail` 非空 **且** slices 里至少一个带 url 的 cell（现成 `hsrmap_phase1/tree.py::has_raster_detail`、`raster.py::raster_spec_from_detail`）。

* `preview` **不能**当判据（979 自身 preview 为空，URL 只在父容器 info 里；这类有 435 例）；`node_type`、`children` 也不能。
* 建议存 `render_probe_state`：VALID / MAP_INFO_FAIL / NO_DETAIL / NO_SLICE / UNRESOLVED。
* 注意 `hsrmap_phase1/tree.py:80` 用 tree 的 `node["detail"]` 判 raster 是**死分支**（tree 923 个节点一个都不带 detail）。

## 4. Schema 计划（M7.1）

* `map_nodes` 增：`tree_leaf` / `tree_visible` / `render_probe_state` / `renderable` / `discovery_method` / `name_source` / `related_id` / `related_group_map` / `map_group_type` / `map_shape`；
* `maps` 增 `display_name`（**不覆盖** tree 原始 name）；
* 新增 `map_edges`（a1-8-1 §四 DDL）与 `point_transitions`（§十三）；
* **624 这个数字必须不变**；老库（没有新列）读取要有回退。

## 5. 离线回填（不用联网）

TREE_CHILD 914 / RELATED_MAP 204 / MAP_GROUP 36 / POINT_JUMP 187 全部可从**现有** `raw_json`（tree、point/list）算出。
唯一需要联网的是 **299 个容器 `map/info`**（约 +2 分钟，0 个新资源），用来补 535 条真名 + 容器 preview。

## 6. 门禁与 canary

* 发布 invariant：`∀ edge ∈ navigable_edges: edge.target_map_id ∈ synced_renderable_maps`，否则 **PUBLISH = FAIL**；本快照今天天然满足（187/187），但 `map_edges` 不存在 → **这条门禁目前根本算不出来**，M7.1 之后才可执行。
* canary `canary_deep_map_transition`：入口图 943 → point 5637 → edge → target 979 → 979 可渲染 → 979 有 point_list → Viewer 能进入 → 能返回入口。
* **口径变更必须同步改门禁**（report/validate/BASELINE/dod/release），否则会出现「数据没变、门禁变红」的假信号。

## 6.2 边存在哪里（M7.4/M7.5 的前置决定）

* **新快照**（下一次 `hsrmap sync` 之后）：`map_edges` / `point_transitions` 就在 `core.db` 里（M7.1 已进 schema）；
* **现有冻结快照** `20261001T105105Z`：**不许写**（字节 sha 已因一次误写变过，逻辑内容已还原）。这一份的边走**旁挂派生库**
  —— M7.2 已生成 `data/graph/core.db`（从快照拷一份再迁移到 schema v2，624/923 原样保留）；
* Viewer 读取优先级：**`core.db` 里有 `map_edges` 就用它 → 否则用 `data/graph/core.db` → 都没有就如实报「没有跳转信息」**
  （老快照 + 新前端必须降级而不是崩）。

## 6.3 发布 invariant 的口径（M7.6 定稿）

```text
∀ edge ∈ NAVIGABLE_EDGE_TYPES: edge.target_map_id ∈ synced_renderable_maps
```

* `NAVIGABLE_EDGE_TYPES` = `EDGE_TYPES − STRUCTURAL_EDGE_TYPES` = **POINT_JUMP / PORTAL / RETURN / UNKNOWN_TRANSITION**；
* `STRUCTURAL_EDGE_TYPES` = TREE_CHILD / FLOOR / RELATED_MAP / MAP_GROUP —— 它们的目标本来就是树里的容器节点，
  一把套 invariant 会误报（290/35/36 个目标没有 raster），所以**单独统计、不算违规**；
* 实测定稿前的数字：render 目标缺口 **0**、unresolved **0**、孤儿可渲染图 **0**，canary `943 → 5637 → 979` PASS。

## 6.3.2 §十九 的导航上下文（已具备，待接进 matcher）

`hsrmap/graph_nav.py` 提供三件只读工具：

* `navigation_context(conn, map_id)` → `{map_name, navigation_kind, entry_map_id, entry_point_id, entry_edge_type, entries[], navigation_path[], tree_path[]}`；
* `attach_navigation_context(points, conn)` → **只加字段**（同图缓存、不改原 dict、没有图库时如实 None），供候选点位批量补上下文；
* `navigation_blob(context)` → 压成可搜索文本（形如 `千星城 千星城中心城区 2层 979 point:5637`），给 token 命中用。

真实数据：`979 → entry 943 / point 5637`，`navigation_path` 前三段 = 千星城 → 千星城中心城区 → 2层。

**接入点（还没接，下一轮做，必须带闭包回归）**：`hsrmap/guides/matching/candidates.py` 的候选构造处补 `navigation_context`；
接入后必须重跑六状态闭包（1006/1006）与 Guide 匹配回归，确认**判定结果零变化**（只多字段、不改命中）。

## 6.35 M7.6 门禁落在哪几个地方（可执行，不是口号）

| 位置 | 做什么 | 证据 |
| --- | --- | --- |
| `python -m hsrmap graph audit --gate` | 只读审计：孤儿 / unresolved / 闭包 / canary + 四项 gate 输入 | 退出码 0（过）/ 2（不过） |
| `hsrmap sync` 的发布前校验 | 新快照跑 `audit_graph()`，gate 不过就**不许切 `data/current.json`** | 单测：构造不可渲染 target 的 POINT_JUMP 边 → 校验失败且 current.json 未切 |
| `tests/test_map_graph_canary.py`（永久回归，4 条） | §二十三 的七步链 + gate 输入为空 + 结构边单列 | `pytest tests/test_map_graph_canary.py --run-data-e2e` 全绿 |

## 6.35.1 现场证据（第 12 轮实测，服务在 8776）

```text
GET /api/v1/maps/943/transitions   → 200
  available true，reason「旁挂图库（M7.2 离线回填产物，只读打开）」，source.origin = graph/core.db
  transitions[0] = {type POINT_JUMP, target_map_id 979, action 前往对应地图,
                    source_point_id 5637, renderable true, navigable true, discovery_source point_list}
  counts = {total 1, navigable 1, renderable_targets 1}
  navigation = {map_name 2层, navigation_kind tree, navigation_path [千星城, 千星城中心城区, 2层]}
GET /api/v1/map/graph              → 200（440 495 B）
```

即：**用户最初截图里那条「二次元界JUMP → 前往对应地图」的链路，现在 API 层面已经通了**（点 5637 → 图 979），
剩下的只是前端按钮与返回栈（M7.5）。

## 6.4 M7.2 实测（真实快照，2026-10-04）

TREE_CHILD **914** / RELATED_MAP **204** / MAP_GROUP **36** / POINT_JUMP **187** = 1341 条边 + 187 条 point_transitions，
与 M7.0 侦察逐项一致；连写两次条数不变（幂等）；回填前后**源快照 sha256 未变**；
Scanner 扫 8370 条引用（过滤「0/空串」10672 个），**未认领候选 = 0**；
`closure` 收敛（visited 923 / frontier 0），预期内互指 18（= related_group_map 的 18 对），意外 cycle 0。
**名字缺口（M7.3 的硬数字）**：341/624 可渲染地图在树里没名字、142/187 跳转目标没名字。

## 6.4.1 M7.3 实测（新快照 20261004T060003Z，2026-10-04）

**四项计数一个都没变**：map_nodes **923** / is_renderable=1 **624** / points **5330** /
label_nodes **1016**；maps 624、map_fragments 624、assets 879。

| 图与名字 | 实测 |
| --- | --- |
| map_edges | **1341** = TREE_CHILD 914 / RELATED_MAP 204 / MAP_GROUP 36 / POINT_JUMP 187 |
| point_transitions | **187** |
| maps.display_name | **624** 条，name_source 全部 = map_info:children[].name（185 条的 children 名 ≠ 自己 info 名，后者基本是占位符「特殊房间」） |
| 可渲染地图无名 | **341/624 → 0/624** |
| 跳转目标无名 | **142/187 → 0/187** |
| 树节点无名（没动，属 §6.11 显示层） | 350 → 350 |
| render probe | **VALID 624 / INVALID 299（容器无 raster，有证据） / UNKNOWN 0** |
| 闭包 | seeds 923 / visited 923 / steps 1768 / cycles 514 / frontier [] / unevidenced [] / converged true |
| Scanner | 未认领候选 **0**（覆盖全部新 payload：923 map/info + 624 point/list + 187 point/info + 树） |
| graph audit gate | **PASS**（unresolved 0 / 孤儿 0 / render 缺口 0 / 闭包收敛） |
| canary | **PASS**（点名样本 943 → point 5637 → 979） |
| 跳转链 | 187/187 通过（47 条 target 自身没有点位） |
| graph_coverage.unresolved_navigable_targets | **0** |
| 冻结快照 sha256 | bb3b5569…d7629aa **前后一致** |

**抓取**：API 请求 = map/info **923** + point/list **624** + point/info **187** + 树/preflight 约 9
（≈ **1743**）；资源 = **1630** 个 unique URL（624 切片 + 1006 图标）→ 合计 **≈ 3373** 次 HTTP。
**耗时**：首轮 14:00:03 → 14:37（崩在资源阶段，见下）≈ 37 min；--resume 20.1 s（全部命中 staging 缓存）。

**±20% 基线门一次都没触发**：failures [] / warnings [] / golden mean=0 max=0 / smoke 7/7 PASS。
新增的基线常量只增不改（tree_nodes 923 / points 5330 / map_edges 1341 / point_transitions 187）。

**顺手修掉两个比 M7 更老的缺陷**（详见 «m73-deep-sync.md» §8）：

1. JobStore.save() 的 unlink()+rename() 在 Windows 上会抛 WinError 32 —— 实测让首轮 sync 在资源阶段崩掉
   （崩点在 _validate 与门禁**之前** → 指针自动没动，这是 §24 #15 的真实世界证据）；现在 os.replace() + 退避重试。
2. 四处 INSERT OR REPLACE 让 rowid 漂移 → maps.node_id 外键炸 —— **这就是 sync --resume 一直是坏的原因**；
   现在改成 ON CONFLICT(...) DO UPDATE。

## 6.5 已知问题（与 M7 相邻，别丢）

**Guide 抽取丢楼层**（`tests/test_guide_ingest.py::test_ingest_cli_attaches_official_points_for_topic` 仍红）：

* 现象：`匹诺康尼 / 「白日梦」酒店-梦境 / 1层` 的 heading 经 `regions/sections.py` 的 `_names_region` 归一后，`map_name` 变成 `「白日梦」酒店-梦境`，楼层只剩在 `section["texts"]` 里；
* 已做的一半：`regions/units.py` 新增 `section_floor_label()`，楼层从「区域名 + heading 原文」两处一起解析（`filter_by_floor` 因此有数可用）；
* 还没做的一半：测试要的是 **`map_name` 本身带楼层**（`"1层" in map_name`）。这属于 `sections.py` 的**命名策略**问题（heading 原文 vs 官方 region 名），会牵动 `_GENERIC_REGIONS`、ingest 的区域判定与 Guide 匹配，必须连回归一起做，不能顺手改。


## 8. §二十四 最终 Definition of Done — 逐条对照（2026-10-04 第 9 轮）

图例：✅ 已满足并有机器证据 ｜ ⏳ 在做（谁在做）｜ ❌ 未开始

| # | §24 条目 | 状态 | 证据 / 位置 |
| ---: | --- | :---: | --- |
| 1 | renderable 不再等价于 tree leaf | ✅ | `map_nodes.tree_leaf` 与 `is_renderable` 两个维度（M7.1）；`tests/test_map_graph.py`、`tests/test_normalize.py` |
| 2 | map/tree 所有节点保留 | ✅ | `map_nodes` 923（含 299 容器）；`graph audit` → `tree_nodes 923` |
| 3 | 所有已知 tree renderable map 已同步 | ✅ | 624 张；`sync._validate` 比「maps 行数 == 探测 VALID 数」；切片缺失 0 |
| 4 | point → map transition 已建模 | ✅ | `point_transitions` 187 条；`PointTransition.as_viewer()`（§十三 形状） |
| 5 | related map / map group 已建模 | ✅ | RELATED_MAP 204 / MAP_GROUP 36（回填实测） |
| 6 | transition target 递归进入 discovery queue | ✅ | `discovery.scan_id_references` + `graph.closure`：visited 923 / frontier 0 / 未认领候选 0 |
| 7 | deep map 有 map/info | ✅ | 新快照 `20261004T060003Z`：**923/923 个节点都抓了 map/info**（`render_probe VALID 624 / INVALID 299（容器无 raster）`），验收工具实测「每张地图都有 map/info 指纹：缺 0 张」 |
| 8 | deep map raster 完整 | ✅ | 187/187 跳转目标都有 `map_fragments` 且本地切片文件存在（missing 0） |
| 9 | deep map point/list 完整 | ✅ | canary：`target_points_readable`（979 有 2 个点）；187/187 可读 |
| 10 | deep map 可进入 Viewer | ✅ | API：`GET /api/v1/maps/943/transitions` → 200（`POINT_JUMP → 979「前往对应地图」`，source_point 5637，来源只读旁挂库）；前端：**CDP 真机实测** 943 → point 5637 → 进入 979（`__HSRMAP_NET.blocked === []`） |
| 11 | deep map 可以返回入口位置 | ✅ | 前端 CDP 实测：返回后回到 943 且抽屉停在 5637；后端 `graph_nav.navigation_context` 提供 entry_map/entry_point/navigation_path |
| 12 | Guide Matcher 能识别 navigation path | ✅ | 两条真实路径都接了：① 地图 API 的 `navigation_context`；② 审核队列候选点的 `navigation` 紧凑块（`review/service.py::navigation_for_candidates`，只对深层地图加、普通图逐字段不变）。`candidates.py` 的 `navigation_conn` 钩子缺省**原样返回同一列表**，「命中集合不变」有专项测试 |
| 13 | Progress 查询使用 graph map set | ✅ | `progress/atlas.py::graph_coverage`（624 可渲染 / 187 可导航 / 5330 点位 / unresolved 0），`remaining_atlas(..., core_conn=, graph_conn=)` |
| 14 | 所有 unresolved target 都进入报告 | ✅ | `reports/map_graph_audit.json` 的 `unresolved_targets_total`；Scanner 的 UNRESOLVED 分类 |
| 15 | unresolved renderable target > 0 时禁止发布 | ✅ | 已接进 `sync._decide_publication`（gate 不过 → `SystemExit(2)`、`_publish` 一次都不调用、staging 保留可 resume）；**真实世界证据**：14:37 那次 sync 在资源阶段崩掉（`JobStore.save()` 的 `unlink+rename`），崩点在 `_validate` 之前 → 指针自动没动；修复后 resume 通过门禁才切到 `20261004T060003Z` |
| 16 | JUMP deep-map canary 永久通过 | ✅ | `tests/test_map_graph_canary.py`（4 条，七步链 + gate 输入 + 结构边单列） |

发布 invariant 的可执行形式（§6.3）：`∀ edge ∈ NAVIGABLE_EDGE_TYPES: target ∈ synced_renderable_maps`；
当前实测：可导航边 187 条，**unresolved 0**（`graph_coverage` 与 `graph audit` 两个口径一致）。

## 6.6 为什么补名字不会动 Guide 判定（已核查）

* 官方点位 payload 的 `map_path` / `region` 来自 `viewer_repo._map_path()`，它读 **`map_nodes.name`**（树里的原始名）；
  M7.3 写的是 `maps.display_name`，**不覆盖** `map_nodes.name`；
* `hsrmap/guides/**` 里出现的 `display_name` 全是**主题 spec** 的显示名（`spec.get("display_name")`），与 `maps.display_name` 无关；
* 所以六状态闭包与证据层**不应该**因补名字而变化 —— 记录基线 digest `cd08466d868cc25d`；
  **一旦它变了，就是有别的路径把新名字渗进了判定层，必须停下来查**，不能当成正常波动。

## 6.7 跑真实 sync 前的安全网与回滚（M7.3 之前抄一份）

**跑 sync 前**：把当时的指针抄一份（这次抄到了 `artifacts/p6/current-before-m73.json`）：

```powershell
Copy-Item data/current.json artifacts/p6/current-before-m73.json -Force
```

**回滚**（sync 出的新快照有问题时，把指针改回去就行 —— 旧快照目录一直在，`snapshots/` 从不原地改写）：

```powershell
Copy-Item artifacts/p6/current-before-m73.json data/current.json -Force
```

回滚后请复核三件事：`python -m hsrmap status`（计数回到 923/624/5330/1016）、
`python -m hsrmap guides closure-check`（PASS，digest `cd08466d868cc25d`）、
旧快照 sha256 仍是 `bb3b5569…D7629AA`。

## 6.8 新快照落地后的整合验收（一条条照跑，别凭印象）

```powershell
# 0) 指针切过去了没 + 旧快照有没有被动过
Get-Content data/current.json -Raw
(Get-FileHash data/snapshots/20261001T105105Z/core.db -Algorithm SHA256).Hash   # 必须仍是 bb3b5569...d7629aa

# 1) 六项计数一个都不许变
python -m hsrmap status                       # 923 / 624 / 5330 / 1016
python -m hsrmap validate                     # golden 误差 mean=0 max=0

# 2) 图：边与名字缺口
python -m hsrmap graph audit --gate           # GATE PASS；看「名字缺口」从 341/624 + 142/187 变成多少
python -m hsrmap progress remaining --limit 1 # 末尾「图口径」一行：可渲染 624 / 边 1341 / 可导航 187 / 未解析 0

# 3) 永久回归（现在参数化：旁挂库与新快照**都要**过）
python -m pytest -q tests/test_map_graph_canary.py --run-data-e2e

# 4) 判定层零退化
python -m hsrmap guides closure-check         # PASS，digest 必须仍等于 cd08466d868cc25d

# 5) 全量门禁
python -m pytest -q                           # 0 failed
python -m ruff check hsrmap tests tools
python -m hsrmap dod                          # 12/12 PASS
python -m hsrmap doctor                       # 首屏闭环 PASS
```

**切完指针记得重启服务进程**：`viewer_app.get_ctx()` 把 `ViewerContext` 缓存在 `app.state.ctx` 里，
**没有失效机制** —— 正在跑的 8766/8767/8776 会继续用**旧快照**直到重启（否则你会以为「新名字没生效」）。

**判定标准**：1) 计数与 golden 不变；2) gate PASS 且 `unresolved_navigable_targets = 0`；
3) canary 两个来源都绿；4) closure digest 不变（**变了就是有东西渗进判定层，停下来查**）；
5) 四个门禁全绿。任何一条不过 → 用 §6.7 的回滚把指针改回去，再查原因。

## 6.9 全量测试里的两类「并发假红」（别误判成回归）

跑 `pytest --run-data-e2e` 时，如果**同时**有别的进程在动仓库数据或前端产物，会出现两类假红：

| 测试 | 触发条件 | 处理 |
| --- | --- | --- |
| `test_runtime_paths::test_guides_completeness_does_not_touch_repo_data` | 有 sync（或任何写库进程）在写 `data/` | 已加前提检查：`sync_lock` 存在、或相邻两次采样就不一致 → 如实 skip |
| `test_serve_evidence::test_map_app_serves_the_read_only_guide_surface` | 同时跑 `vite build`（`web/dist` 正在被重写，`/` 取不到 `index.html`） | 单独跑必过；构建期间如实视为环境噪声，别当成代码回归 |

判定方法：**单独跑一遍**（`pytest -q tests/test_serve_evidence.py`）与**等构建/同步结束后再跑全量**，两次都过就不是回归。

## 6.10 生成的报告放在哪、会不会带出去

* `reports/*.json`（`map_graph_audit.json` / `m72-backfill*.json`）是**本地报告**：`.gitignore` 里有 `/reports/`，
  发布 allowlist 也不含它 —— 实测 `submit/reports` 与暂存仓库都不存在这两个目录。
* 报告里**会写绝对路径**（`snapshot` / `source_db` / `out`），这是给本机排查用的（要能立刻知道用的是哪个快照/库）。
  因为不进发布包，它不触发任何隐私门；但**手工外发报告前先看一眼**，别把本机目录结构带出去。
* 想让报告也能安全外发，最小改法是写 `root_name` + 相对路径（release-manifest 已经是这个口径）——本轮没做，记录在此。

## 6.11 待办：容器/深层节点的「真名」还没进树（显示层缺口）

实测（staging，2026-10-04）：**350 个树节点名字为空** —— 341 个是可渲染地图（已有 `maps.display_name`，如 979 → 「1」），
9 个是容器（1016/1018/1020…）；另有节点树名是占位符「特殊房间」，真名只在**父容器**的 `map/info.children` 里。

* **症状**：Viewer 左侧树读 `map_nodes.name`，这些节点显示成裸 id（截图里的 1016/1018/… 就是它们）。
* **第 1 步已做完（派生层，2026-10-04）**：`hsrmap/graph_names.py` 从 `<snapshot>/raw/map_info/*.json` 的
  `data.info.children[].name` 收真名 → 写进**派生库** `data/graph/core.db` 的 `node_display_names` 表
  （`graph backfill --write` 顺带写）；`viewer_repo.map_tree_payload` 合并显示名
  （`派生库真名 → 树名 → id`，并给出 `name_source`；**句柄连接与旁挂库两处都查**）。
  实测新快照 raw：**914 条真名**（`1016 → 生研院1`、`955 → 千星城中心区7`、`979 → 1`）。
  **没有**改 core schema，也不需要第二次全量 sync。
* **第 2 步（判定层，必须带回归）**：`viewer_repo._map_path()` 用 `map_nodes.name`，官方点位的 `map_path`/`region` 都来自它 ——
  换成真名会让「特殊房间」变「千星城中心区7」这类名字，**可能改变 Guide 的区域匹配**；要单独跑 `closure-check`
  （digest 基线 `cd08466d868cc25d`、1006/1006）与 Guide 测试，**不许放宽断言**。

## 7. 没查清的（如实）

1. `/v1/map/point_group` 匿名 GET 5 张图全部 `-502001`（可能藏 point→point 的 PORTAL 边）→ 需要带 cookie 的只读通道再试；
2. `map_anchor/list`、`spot_kind/*`、`mark_map_point_list`、`get_route_paths` 在 public-static host 404（认证 host），本轮按纪律未试；
3. 「跳转标点层 / 前往对应地图」这两个中文文案到字段的对应是按 bundle 代码语义推的，不是字面证据；
4. 用户说的「二次元界 JUMP 2」我找不到该字面串：live label 名是「二次元JUMP!」，"2" 来自地图名「2层」；
5. `related_id` 204 条只抽查 5 条；
6. 用的是 P6 阶段抓的 bundle（sha256 `e5cefc8f…`），未重新下载核对是否已变。
