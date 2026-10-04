# M7.3 — Deep Sync（补名字 + 新快照带边）

范围：**联网同步**。M7.2（发现层）离线算边，M7.4（Graph API）/ M7.5（Viewer）对外暴露，
M7.6（Coverage Gate）定稿。本步把 §十五 的**两阶段 Discovery** 与 §六 的**递归闭包**真正接进
`python -m hsrmap sync`，产出一个自带 `map_edges` / `point_transitions` / `maps.display_name` 的新快照。

**结果：新快照 `20261004T060003Z`，四项计数一个没变（923 / 624 / 5330 / 1016），1341 条边 + 187 条跳转，
名字缺口 341/624 + 142/187 → 0 + 0，发布门禁 PASS。冻结快照 `20261001T105105Z` 的 sha256 前后一致。**

## 1. 交付物

| 位置 | 内容 |
| --- | --- |
| `hsrmap/sync.py` | Pass A 扩到 **全部 923 个树节点**的 `map/info`；Pass B（187 个可疑点位的 `point/info`）；`_run_graph_closure`（Scanner + `closure(seeds, expand)` + `save_edges`/`save_point_transitions`）；`_apply_display_names`；`_map_graph_gate`；`_decide_publication` |
| `hsrmap/database.py` | `maps.display_name` / `maps.name_source`（加性迁移）；四个 `INSERT OR REPLACE` → `ON CONFLICT DO UPDATE`（rowid 稳定，`--resume` 才可能工作）；`SCHEMA_VERSION 2 → 3` |
| `hsrmap/schema.py` | `map_info_container` 形状（容器合法地没有 raster detail），node_type=2 仍强制 `detail` |
| `hsrmap/render_probe.py` | `extra_evidence` 口子：把「map/info 成功但无 raster」的容器判成 **INVALID**（有证据），不是 UNKNOWN（没问过） |
| `hsrmap/graph_audit.py` | 名字缺口拆两个口径（树名 / 真名后）；发布门禁的 unresolved 用 `navigable_only=True` |
| `hsrmap/jobs.py` | `save()`：`unlink()+rename()` → `os.replace()` + 退避重试（Windows 上会抛 WinError 32） |
| `hsrmap/reports.py` / `hsrmap/paths.py` | 统计里报名字覆盖；`BASELINE` 增补 `tree_nodes` / `points` / `map_edges` / `point_transitions`（**老值一个没动**） |
| `tests/test_map_graph_deep_sync.py`、`tests/test_jobs.py` | 12 + 3 条新测试（门禁挡发布、真名、老库回退、闭包诚实、resume rowid 稳定、jobs 原子写） |

## 2. 两阶段 Discovery（§十五）

| 阶段 | 抓什么 | 数量 | 为什么 |
| --- | --- | --- | --- |
| Pass A | `map/tree` + `map/label/tree` | 2 | 树事实与 label 树 |
| Pass A | `/v1/map/info` | **923**（含 299 个 `node_type=1` 容器） | 真名只在容器的 `children[].name` 里 |
| Pass A | `/v1/map/point/list` | **624**（`node_type=2`） | 容器没有自己的标点层；多抓会污染 5330 这个不变量 |
| Pass B | `/v1/map/point/info` | **187**（有 `related_jump_id` 的点位） | 交叉校验 + 跳转证据；关键词只用于调度 |

Pass B 的嫌疑判据是**存在相关字段**（`!is_absent(related_jump_id)`），不是名字里有没有 JUMP ——
实测「二次元JUMP!」这类 label 只是 6 个 label 之一（836×137、680×18、463×13、545×10、548×5、973×4）。

## 3. 递归闭包（§六 / §八）

```python
edges  = build_backfill_plan(SnapshotSource(staging_root), scan=False)   # 与 M7.2 同一套算法
reports = [scan_id_references(payload, probe=local_map_probe(conn)) for payload in 每个新 payload]
unclaimed = [item for r in reports for item in r.unclaimed_candidates()]  # §八 的兜底
seeds = sorted(known_map_ids(conn) | {item.map_id for item in unclaimed})
result = closure([e.as_edge() for e in edges.edges], seeds=seeds, expand=self._expand_map)
```

* `expand(map_id)`：本地有 raw → 跑 `extract_map_info` / `extract_point_list` 的 `edges_from(map_id)`；
  本地没有就**下探官方一次**（树外的新地图靠这条被发现）；还是拿不到证据 → 返回 `None`，
  `closure` 记进 `unevidenced`、`converged=False`，**不假装收敛**；
* 实测：`seeds 923 / visited 923 / steps 1768 / cycles 514 / frontier [] / unevidenced [] / converged true`，
  Scanner 的**未认领候选 = 0**（官方还没加新机制）；
* 边与跳转的落库走 M7.1 的 `save_edges` / `save_point_transitions`（UNIQUE 幂等），
  算法与 M7.2 的离线回填**完全同一套**，所以新快照的 1341 / 187 与 `data/graph/core.db` 逐条对得上。

## 4. 真名（M7.3 的第一交付）

* 真名唯一来源 = **容器**的 `map/info → data.info.children[].name`；写入 `maps.display_name` + `maps.name_source`；
* **不覆盖**两样东西：`map_nodes.name`（官方树的原始名）与 `maps.name`（map/info 自己那一份）；
* 优先级：`children[].name` > 自己 `info.name`。实测 624 条**全部**来自 `children[].name`；
* 有 **185 条**「children 名 ≠ 自己 info 名」，自己那份基本是占位符「特殊房间」——
  例如 `955 → 千星城中心区7`、`1000 → 千星城中心区10`、`230 → 匹诺康尼大剧院-立体1`。

| 指标 | before | after |
| --- | ---: | ---: |
| 可渲染地图无名（真名 ∪ maps.name ∪ 树名） | **341 / 624** | **0 / 624** |
| 跳转目标无名 | **142 / 187** | **0 / 187** |
| 树节点无名（**没动**，属于显示层 §6.11） | 350 | 350 |

## 5. 口径重标定（做了什么、没做什么）

* `is_renderable = node_type==2 and not children` 在 M7.1 就已经取消；本步**没有再放宽任何检查**，
  只是把新口径也守起来：
  * `BASELINE` **只增不改**（`renderable_maps 624` / `labels 1016` 原样），新增 `tree_nodes 923` /
    `points 5330` / `map_edges 1341` / `point_transitions 187`，一起进 ±20% 门；
  * `sync._validate` 的 `missing_map_info` **仍然逐条要求每个 `node_type=2` 候选有 maps 行**；
    容器的「没有 raster detail」是 §三 的合法状态，靠 `map_info_container` 形状 + render probe 的
    `extra_evidence` 判成 **INVALID**（有证据）而不是 UNKNOWN（没问过）；
  * `graph_audit` 的名字缺口拆成「树名」与「真名后」两个口径，人话摘要两个都打印；
  * 发布门禁的 unresolved 用 `navigable_only=True`（结构边单独统计，§6.3），**全集口径仍留在报告里**；
  * `reports.build_statistics` 增 `maps_with_display_name` / `maps_without_any_name`（老库没有该列时如实回退）。
* **±20% 基线门一次都没触发**：624 / 1016 / 923 / 5330 / 1341 / 187 全部等于基线，
  `failures [] / warnings [] / golden mean=0 max=0 / smoke 7/7 PASS`。

## 6. 发布门禁（§二十四）真的挡住了发布

```text
forall edge ∈ NAVIGABLE_EDGE_TYPES: edge.target_map_id ∈ synced_renderable_maps
```

* `sync._map_graph_gate()` 在**新快照**上跑 `audit_graph()` 的四项 + 上面这条 invariant；
* 不 ok → `_decide_publication` **不调用 `_publish`**、`current.json` 一个字节不动、退出码 **2**、staging 保留可 resume；
* 单测：`tests/test_map_graph_deep_sync.py::test_release_gate_failure_does_not_switch_current_json`
  （指针 monkeypatch 到 tmp_path，**绝不碰真实 `data/current.json`**）；
* **真实世界证据（§24 #15）**：14:37 那次 sync 在资源阶段崩掉（`JobStore.save()` 的 Windows 文件锁），
  崩点在 `_validate` 与门禁**之前** → 指针自动没动；修复后 resume 走完整校验 + 门禁才切到 `20261004T060003Z`。
  resume **不跳过任何一条校验**（同一个 `_run`，只是 job 命中缓存）。

## 7. 实测（新快照 `20261004T060003Z`）

| 项 | 值 |
| --- | --- |
| `map_nodes` / `is_renderable=1` | **923 / 624** |
| `points` / `label_nodes` | **5330 / 1016** |
| `map_edges` | **1341**（TREE_CHILD 914 / RELATED_MAP 204 / MAP_GROUP 36 / POINT_JUMP 187） |
| `point_transitions` | **187** |
| `maps.display_name` | **624** 条，`name_source` 全部 = `map_info:children[].name` |
| render probe | **VALID 624 / INVALID 299 / UNKNOWN 0** |
| graph audit gate | **PASS**（unresolved 0 / 孤儿 0 / render 缺口 0 / 闭包收敛） |
| canary | **PASS**（点名样本 943 → point 5637 → 979） |
| 跳转链 | 187/187 通过（47 条 target 自身没有点位） |
| `graph_coverage.unresolved_navigable_targets` | **0** |
| 冻结快照 `20261001T105105Z` sha256 | `bb3b5569…d7629aa` **前后一致** |

## 8. 顺手修掉的两个「比 M7 更老」的缺陷

1. **`JobStore.save()` 在 Windows 上是错的**：`unlink()` + `rename()` 中间有「文件不存在」的窗口，
   而且会被并发读者/杀软顶成 `PermissionError [WinError 32]` —— 实测让整个 sync 在资源阶段崩掉。
   现在 `os.replace()` + 5 次退避重试；一直占用就**大声失败**，绝不静默留旧状态。
2. **`INSERT OR REPLACE` 让 rowid 漂移**：`map_nodes` / `maps` / `label_nodes` / `points` 四处都是这个写法，
   UNIQUE 冲突靠「删旧行 + 插新行」实现 → `maps.node_id` / `points.map_id` / `point_labels.*` 的外键指向失效，
   `PRAGMA foreign_keys=ON` 直接 `IntegrityError`。**这解释了为什么 `sync --resume` 一直是坏的**。
   现在改成 `ON CONFLICT(...) DO UPDATE`（rowid 不变），顺带不再抹掉 `icon_asset_sha256` 与 `maps.display_name`。

## 9. 已知未做（如实）

1. `FLOOR` / `PORTAL` / `RETURN` / `UNKNOWN_TRANSITION` 产量仍是 0：官方 payload 里没有对应字段
   （RETURN 只存在前端 URL 的 `origin_map_id` 栈，M7.5）；
2. `/v1/map/point_group` 仍拿不到（M7.0 匿名 GET 全 `-502001`），point→point 的 PORTAL 边没有来源；
3. `tree_nodes_without_name = 350` 没动：那是**显示层**缺口（树读 `map_nodes.name`），
   已由派生层 `graph_names.py` + `node_display_names` 解决（runbook §6.11 第 1 步），本步不碰树名；
4. 资产阶段**重下**了 1630 个 unique URL（内容寻址，落盘 sha 不变）。「按 `remote_url` 复用本地 sha」
   是一次行为变更（放弃远端是否变过的核对），值得单独立项 + 单独测试，本轮没做。
