# M7.2 — 发现层（extractor + ID Reference Scanner + 离线回填 + 孤儿发现）

范围：**发现**。M7.3（deep sync）负责联网把深层 map/info / raster / point 抓下来，M7.4（Graph API）
负责对外暴露，M7.5（Viewer）负责导航与返回。本步**不发任何网络请求、不写任何冻结快照**。

## 1. 交付物

| 位置 | 内容 |
| --- | --- |
| `hsrmap/discovery.py` | 五个 extractor（纯函数）+ ID Reference Scanner v1 + 键语义注册表（transition registry） |
| `hsrmap/graph_backfill.py` | 离线回填：快照 raw payload → 边 / 跳转；dry-run 默认，`--write` 才落库；冻结快照守卫 |
| `hsrmap/graph_audit.py` | 孤儿地图发现器 + Graph Audit 报告（§十七）+ canary 链校验（§二十三） |
| `hsrmap/cli.py` | `python -m hsrmap graph backfill / audit / orphans` |
| `tests/test_map_graph_discovery.py`、`tests/test_map_graph_backfill.py` | 58 个新测试 |

## 2. 五个 extractor（§七 的五层）

全部是纯函数：输入 payload，输出 `Extraction`（candidate edges / transitions / facts / contracts / diagnostics），
每条边带 `(key, json_path, discovery_source)` 出处。

| 函数 | 输入 | 产出 | 本快照产量 |
| --- | --- | --- | --- |
| `extract_tree` | `/v1/map/tree` | TREE_CHILD（`parent_id`）/ RELATED_MAP（`related_id`）/ MAP_GROUP（`related_group_map`）+ 树事实 | 914 / 204 / 36 |
| `extract_map_info` | `/v1/map/info` | 真名 / preview / detail 事实 + `parent_id` → TREE_CHILD + related / group | 624 条与 tree 同键（去重后 0 新增） |
| `extract_point_list` | `/v1/map/point/list` | POINT_JUMP（`related_jump_id`）+ point_transitions | 187 |
| `extract_point_info` | `/v1/map/point/info` | 同值 POINT_JUMP 交叉校验；`map_id` 是点所属标点层（**不是边**） | 0（本快照没有 point/info 落盘） |
| `extract_bundle_contract` | 前端 bundle 文本 | 符号契约 + `MAP_GROUP_TYPE` 枚举；不产边 | — |

## 3. ID Reference Scanner v1（§八 + M7.0 Q4）

* 键形态：`*_id` / `*_map` / `target*` / `related*` / `jump*` / `group*` / `*_ids` / `origin_map_id`；
* 值形态：数字 / 数字串 / 数字数组 / 逗号数字串；**`0` / `"0"` / `""` / None / False / 空数组一律当「无」**，
  过滤掉并计入 `skipped_absent`（本快照 10672 个）；
* 判定四类：`UNKNOWN_RENDERABLE_TARGET` / `MAP_LIKE_NON_RASTER` / `NOT_A_MAP` / `UNRESOLVED`；
* **必须探测不能猜**：结论 = 键语义（`KEY_CONTRACTS`）+ 探测（`MapProbeResult`）。
  id 空间重叠的现场证据留在 `probe.overlaps` 里（本快照：`related_jump_id=979` 的 979 同时是 label id）。
  离线探测通道 = `graph_backfill.local_map_probe`（maps + map_fragments）；**本地没证据 ≠ 不是地图**
  （那种情况是 UNRESOLVED / MAP_LIKE，绝不是 NOT_A_MAP）。
* 每条结论记 `(endpoint, json 路径, 键名, 原值, 结论, discovery_source)`；「本快照未认领的候选跳转 = 0」
  说明注册表覆盖了现有 payload——将来官方加新字段，它会以未认领候选的形式冒出来。

## 4. 离线回填（本轮核心）

~~~
python -m hsrmap graph backfill                       # dry-run：只打印将要写什么
python -m hsrmap graph backfill --write               # 写到 <data>/graph/core.db（从快照拷一份再写）
python -m hsrmap graph backfill --write --force       # 重新拷一份，丢弃已有图
~~~

实测（`data/snapshots/20261001T105105Z`，与 M7.0 侦察报告逐项一致）：

| edge_type | 实测 | M7.0 基线 | discovery_source |
| --- | --- | --- | --- |
| TREE_CHILD | 914 | 914 | map_tree |
| RELATED_MAP | 204 | 204 | map_tree |
| MAP_GROUP | 36 | 36 | map_tree |
| POINT_JUMP | 187 | 187 | point_list |
| 合计 | **1341** | — | — |

* `point_transitions` 187 条（`point_id` = `points.id` 主键，Viewer 只读 §十三 的形状）；
* 交叉校验：`flatten_map_nodes` 的 TREE_CHILD 与 extractor 完全一致；raw/point_list 与 `points.raw_json`
  的 187 条跳转逐条一致（`jump_targets_agree = true`）；
* 幂等：第二次 `--write` 是 in-place upsert，条数仍是 1341；
* 源库 sha256 前后一致（`source_untouched = true`）——冻结快照 `BB3B5569…D7629AA` 没被动过。

## 5. 孤儿 + Audit 实测（回填后的图谱库）

~~~
python -m hsrmap graph audit [--gate] [--db ...] [--out reports/map_graph_audit.json]
python -m hsrmap graph orphans [--db ...]
~~~

* tree_nodes 923 / renderable_maps 624 / edges_total 1341 / point_transitions 187；
* `deep_maps`（Renderable − Tree）= **0** —— 本快照 624 张可渲染地图全在树里（M7.0 结论复现）；
* `orphan_renderables` = **0**（没有入边 0 / 从树根不可达 0）；
* `unresolved_targets` = **0**（187/187 跳转目标都在 maps 表）；
* cycles：预期内互指 18（就是 18 对 `related_group_map`）/ 意外 0 / 自环 0 / 多父节点 0；
* closure：`converged=true`，visited 923，frontier 0（§六 的「frontier 收敛」在离线数据上成立）；
* render 目标缺口：POINT_JUMP / RETURN / UNKNOWN_TRANSITION / PORTAL 目标全部可渲染 = **0 缺口**；
  结构类边（TREE_CHILD 290 / RELATED_MAP 35 / MAP_GROUP 36 个不同目标是容器节点）**不算缺口**——
  §二十四 的 invariant 必须按边类型分开写（M7.6 定稿，见 §7 末尾）；
* `canary_deep_map_transition`：**PASS**（点名样本 943 → point 5637 → 979，7 步全绿）；
* 跳转链校验 187/187 通过（47 条 target 自身没有点位——可读但为空，不算断链）。

## 6. 名字缺口（给 M7.3 的硬数字）

本快照 **341/624** 张可渲染地图在树里没有名字，**142/187** 个跳转目标也没有。名字的**唯一**来源是
容器（`node_type=1`）的 `map/info` → `children[].name`；本快照 624 个已落库的 map/info **一个 children 都没有**
（容器 info 只在 live 上拿得到）。所以 M7.3 的第一件事就是补 299 个容器 map/info，然后：

~~~
from hsrmap.discovery import extract_map_info          # facts[].key == "children[].name"
from hsrmap.discovery import scan_id_references        # 兜底：官方加新字段也不会丢
~~~

## 7. 给 M7.3 / M7.4 的接口

**M7.3（deep sync）**

1. 名字：拉容器 `/v1/map/info` → `extract_map_info(payload).facts`，取 `key == "children[].name"` 的
   `MapFact.name` 写 `maps.display_name`（M7.1 加的列；**不覆盖** tree 原始 name）；
2. 边：把新 payload 喂 `extract_point_list` / `extract_map_info`，落库仍用
   `hsrmap.graph.save_edges` / `save_point_transitions`（幂等，重复发现不会翻倍）；
3. 闭包：`hsrmap.graph.closure(seeds=…, expand=…)`，expand 返回 `extraction.edges_from(map_id)`
   （注意：它只返回 source == map_id 的边；`map/info` 的 `parent_id → 自己` 是反方向，要单独落库）；
   返回 `None` 表示「这次拿不到证据」，遍历不会假装收敛；
4. 探测：落库后 `hsrmap.render_probe.refresh_render_probes(conn, new_map_ids)`；
   **别把 UNKNOWN 写成 0**（NULL = 没问过）。
5. 新字段兜底：任何新 payload 先过 `scan_id_references(payload, probe=…)`；`unclaimed_candidates()`
   非空就是「官方加了新机制」，不要静默丢掉（§五 / §八）。

**M7.4（Graph API）**

* 边：`hsrmap.graph.load_edges(conn, source_map_id=…)` / `load_edges(conn, target_map_id=…)`；
* 点位跳转：`load_point_transitions(conn, point_ids)` → `PointTransition.as_viewer()` 就是 §十三 的形状
  （`{type, target_map_id, action}`），前端不需要认识 `related_jump_id`；
* 路径/可达：`closure(edges, seeds=[map_id])` 给 navigation path（§十一 的 Navigation Path ≠ 树路径）；
* 渲染集合：M7.1 的 `known_map_ids(conn, renderable_only=True)`；**不要**再用 tree leaf 当全集（§二十）。

**M7.6（Coverage Gate）必须定稿的一件事**

`graph.NAVIGABLE_EDGE_TYPES` 现在等于全部边类型，而 §二十四 的 invariant 直接套上去会**误报**：
TREE_CHILD / RELATED_MAP / MAP_GROUP 的目标是树里的**容器节点**（290 / 35 / 36 个），本来就没有 raster。
本步在 `graph_audit.RENDER_TARGET_EDGE_TYPES = (POINT_JUMP, PORTAL, RETURN, UNKNOWN_TRANSITION)` 里给出建议口径：
invariant 只对「目标必须是一张可渲染地图」的边类型成立；结构类边单独统计。定稿后把非导航类型加进
`graph.NON_NAVIGABLE_EDGE_TYPES`（M7.1 留的钩子）。

## 8. 命令与退出码

| 命令 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `graph backfill` | dry-run 打印 / `--write` 成功 | 没有快照、写不进去、源库 sha 变了 | `--out` 落在 `snapshots/` 里（拒绝写冻结快照） |
| `graph audit` | 报告生成 | 库打不开 | `--gate` 且 unresolved / 孤儿 / 闭包未收敛 |
| `graph orphans` | 打印集合运算 | 库打不开 | — |

冻结快照守卫是**双保险**：`--out` 等于源库、或落在任何 `snapshots/` 目录（运行目录的 + 仓库 `data/snapshots`）里，
一律 `FrozenSnapshotError` → 退 2。回填报告里 `source_untouched` 字段是回填前后各算一次 sha256 的结果。

## 9. 已知未做（如实）

1. `/v1/map/point_group` 没接（M7.0 拿不到数据）：point→point 的 PORTAL 边还没发现来源；
2. `FLOOR` / `PORTAL` 边类型目前产量 0：楼层关系现在靠 TREE_CHILD 表达，等 M7.3 有更多 payload 再提炼；
3. `RETURN` 边只有 bundle 契约（`origin_map_id` 栈），服务端没有字段，离线造不出边；
4. `point/info` extractor 有实现有测试，但本快照没有 point/info payload 可回填；
5. 名字回填（341 条）没有做：它需要联网，属于 M7.3。
