# M7.1 — Graph Schema（a1-8-1 §三 / §四 / §六 / §九 / §十三 / §十八）

范围：**只做数据结构与遍历**。发现（M7.2 extractor）、抓取（M7.3 deep sync）、API（M7.4）、
Viewer（M7.5）、覆盖率门禁（M7.6）都不在这一步。本步不发任何网络请求、不写 `data/`。

## 1. 交付物

| 位置 | 内容 |
| --- | --- |
| `hsrmap/database.py` | `map_edges` / `point_transitions` 两张新表；`map_nodes` 三个可空新列；`SCHEMA_VERSION = 2`；加性迁移 `CoreDatabase._migrate()` |
| `hsrmap/graph.py` | `edge_type` 常量（唯一出处）、`Edge`、边的幂等装载/写入、`navigable_edges` / `unresolved_targets` / `closure`、`point_transitions` I/O、老库回退读取 `load_map_nodes` |
| `hsrmap/render_probe.py` | 可渲染探测（VALID / INVALID / **UNKNOWN**）+ 证据收集 + 写回；`tree_map_candidates` |
| `hsrmap/normalize.py` | `flatten_map_nodes` 拆成 `tree_leaf` 与 `is_renderable` 两个独立维度 |
| `hsrmap/sync.py` | 初始 frontier = 结构候选；map/info 落库后跑 probe 并写回；校验改成「同步数 == 探测为可渲染数」 |
| `hsrmap/rebuild.py` | 离线重建后同样跑 probe（离线路径不许留下 NULL） |
| `hsrmap/providers/live.py` | live 树没有落库证据：显示用结构提示，判定仍留给 `get_map()` |
| `hsrmap/reports.py` | `folder_nodes` 用 `COALESCE(is_renderable, 0)`，三态下不会漏数 |
| `tests/test_map_graph.py` | schema / 幂等 / UNKNOWN_TRANSITION / 闭包与 cycle / unresolved / 旧库兼容 / 624 |

## 2. Schema（照抄 a1-8-1 §四 / §十三）

```sql
CREATE TABLE IF NOT EXISTS map_edges (
    id INTEGER PRIMARY KEY,
    source_map_id TEXT NOT NULL,
    target_map_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    source_point_id TEXT,
    source_label_id TEXT,
    discovery_source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    bidirectional INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT,
    discovered_at TEXT,
    UNIQUE (source_map_id, target_map_id, edge_type, source_point_id)
);
-- SQLite 的 UNIQUE 认为 NULL 互不相等：没有点位来源的边靠这个部分唯一索引保证幂等。
CREATE UNIQUE INDEX IF NOT EXISTS idx_map_edges_unsourced
    ON map_edges(source_map_id, target_map_id, edge_type) WHERE source_point_id IS NULL;

CREATE TABLE IF NOT EXISTS point_transitions (
    point_id INTEGER NOT NULL,              -- points.id（core 主键）
    target_map_source_id TEXT NOT NULL,
    transition_type TEXT NOT NULL,
    action_label TEXT,
    raw_json TEXT,
    PRIMARY KEY(point_id, target_map_source_id)
);
```

`map_nodes` 加三个可空列（老库用 `ALTER TABLE ADD COLUMN` 补）：

- `tree_leaf`：树事实，「有没有 children」；
- `render_probe_state`：`VALID` / `INVALID` / `UNKNOWN`；
- `discovery_method`：`TREE` / 将来的 `POINT_JUMP` / `RELATED_MAP` / …（§九）。

`is_renderable` 的取值域因此变成 `1 / 0 / NULL`，**NULL = 还没探测过**。`SCHEMA_VERSION: 1 → 2`，
迁移只增不改：老库打开后新列是 NULL，老值原样保留。

## 3. 两个维度（§三）

```text
tree_leaf      树事实：这个节点有没有 children
is_renderable  可渲染探测的结论：True / False / None(UNKNOWN)
```

`node_type == 2` 不再是可渲染判定，只是「去问哪些 map/info」的**结构提示**
（`render_probe.tree_map_candidates`）。有 children 的节点一样可能是可渲染地图。

## 4. 可渲染探测（§九）

```text
Renderable(map_id) = map/info 成功 AND detail 存在 AND raster spec 有效
```

M7.1 的证据来自**已经落库**的 `maps` + `map_fragments`（两条 SQL，不做逐点查询）：

- `VALID`：map/info 落库 + 有 fragment + fragment 带 remote_url + canvas 有效；
- `INVALID`：有证据说它不可渲染（map/info 失败 / raster spec 没有 fragment / 没有 url）；
- `UNKNOWN`：还没证据 —— 落库时写 NULL，**不写 0**（把「没问过」写成 False 也是猜）。

M7.3 只需要把更深层的证据喂进 `RenderEvidence` / 给 `refresh_render_probes` 传更多 map id。

## 5. 624 不变量

`data/snapshots/20261001T105105Z/core.db` 是 v1 老库：923 个 `map_nodes`、624 行
`is_renderable = 1`、624 行 `maps`。M7.1 对它的读法是**只读 + 回退**：

- 没有 `map_edges` / `point_transitions` 表 → `load_edges() == []`（= 还没有发现过边），不抛；
- 没有 `tree_leaf` → 按「有没有孩子」现算；
- 没有 `render_probe_state` → `LEGACY_TREE_HINT`，`is_renderable` **沿用落库值，绝不回算**。

任何「读取老库时重算 `is_renderable`」的实现都会让 624 变成猜测，这是禁止的。

## 6. 闭包遍历（§六 / §十八）

```python
from hsrmap.graph import Edge, closure, save_edges, unresolved_targets, known_map_ids

def expand(map_id: str):
    """M7.2 的 extractor：拉 map/info + point/list、跑 extractor、返回以 map_id 为 source 的边。
    返回 None 表示这次拿不到证据（遍历不会假装收敛）。"""
    ...

result = closure(edges, seeds=["943"], expand=expand)
assert result.converged          # frontier 为空且每个地图都拿到了证据
```

- `seen` 集合保证每个地图只展开一次：`A → B → A`、自环都只是被记进 `result.cycles`，
  cycle 不是 bug，也不要求 DAG（§十八）；
- `frontier` 是「发现到但还没展开」的地图，正常终止时为空；
- `unevidenced` 是明确拿不到证据的地图 —— 有它就 `converged = False`。

## 7. M7.2 的接口（extractor 往哪塞边）

```python
# 1) 建边：语义不明一律 UNKNOWN_TRANSITION，绝不丢 target（§五）
save_edges(conn, [Edge(source_map_id=..., target_map_id=..., edge_type=POINT_JUMP,
                       source_point_id=..., discovery_source="point_payload", raw_json=...)])
# 2) 点位跳转：Viewer 只读这个形状（§十三）
save_point_transitions(conn, [PointTransition(point_id=5171, target_map_source_id="1287",
                                              transition_type=POINT_JUMP, action_label="前往对应地图")])
# 3) 发现 frontier：闭包 + unresolved_targets（target 不在已知集合里 = 还要继续发现）
closure(seeds=known_map_ids(conn), expand=extract)
unresolved_targets(load_edges(conn), known_map_ids(conn))
# 4) 新地图落库之后：探测 + 写回（is_renderable / render_probe_state）
refresh_render_probes(conn, new_map_ids)
```
