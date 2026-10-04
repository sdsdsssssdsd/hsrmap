# 官方点位详情作为一种来源（official point details）

用户 2026-10 的指示：**官方给的图片可能就是最正确的、最完善的攻略**。社区攻略对位置型主题
（3D 隐藏点、点对点挑战）只能给「该地图共 N 个」，但官方地图里每个点位都自带一行位置说明
和一张官方截图——那正是玩家需要的东西。

## 数据在哪

`data/enrichments/<run>/detail.db`（由 `enrich-details` 抓取）：

| 表 | 内容 |
| --- | --- |
| `point_details` | 每个点位一行：`plain_text`（如「位于门旁的凳子上。」）、`detail_state` |
| `point_detail_assets` | 每个详情一张官方截图：`remote_url` + `asset_sha256` + `state` |
| `point_detail_bindings` | 核心点位 id ↔ 详情 id |

## 怎么用

```text
python -m hsrmap guides official-seed --topic nymph            # 干跑：会发布哪些、跳过哪些
python -m hsrmap guides official-seed --topic nymph --apply    # 下载官方图并发布
```

`hsrmap/guides/official.py` 做四件事：

1. 取该主题所有缺源点位（点级目标，或 MAP_LABEL 目标展开成它的点位）；
2. 读官方详情，按证据分三层（2026-10 修订）：

   | 官方详情 | 发出来的条目 | 依据 |
   | --- | --- | --- |
   | 一行说明 + 一张截图 | 说明 + 截图（原来的层级） | `LOCATE`：图 + 字 |
   | **只有一行说明**（没有截图） | **文字条目**：步骤就是官方那句话，`source_url` 指向官方互动地图本身 | `LOCATE`：官方地图上「一个标记 + 一行说明」就是它的全部内容 |
   | 说明为空 | 跳过，记 `OFFICIAL_DETAIL_EMPTY` | 没有任何可读内容 |

3. 下载官方截图一次，字节放进 `data/guide-assets/sha256/`（已发布攻略读图的地方），
   同时在 `guide_asset_cache` 记一行，重复运行不再请求；
4. 建一条正常 entry：`source_kind=Official`、`source_name=HoYoLAB 官方地图`、
   一行官方说明（+ 官方截图，如果有）。**不绕过任何门槛**：与社区攻略走同一条快照门与审计
   （文字条目没有图，审计就拿官方说明当正文做 grounding 检查，见 `guide-completeness.md` §六之二）。

## 边界

- 官方详情只有一行「在哪里」，没有解法步骤；对输入序列型解谜（黄金替罪羊）它只是位置补充，
  解法仍来自社区文字攻略；
- 二次元 JUMP 的官方详情是 `EMPTY` 且没有截图（102 个点位全部如此），所以那条路仍然只能等
  范围证据，官方图片帮不上；
- 官方截图是**点位图**（一个点一张），不是一个区域的完整地图；观众看到的是「这个点在哪儿」。

## 效果

首次运行（只有「说明 + 截图」那一层）：nameless_dust_spirit 16、nymph 16、golden_scapegoat 10、
hanu 3、king_bucket 2，jump 0（官方详情全空）。

2026-10 补齐「只有说明」那一层之后（`guides official-seed --all --apply`，15 个主题）：

| 主题 | 本轮补发 | 其中文字条目 | 跳过 |
| --- | ---: | ---: | --- |
| nameless_dust_spirit | 130 + **11** | 11 | 11（官方说明也为空） |
| king_bucket | 12 + **1** | 1 | 1 |
| nymph | 0 + **2** | 2 | 1 |
| zagreus_hand | 0 + **2** | 2 | 0 |
| golden_scapegoat | 0 | 0 | 8（官方详情为空） |
| jump | 0 | 0 | 128（官方详情为空） |
| miracle_orb | 0 | 0 | 9（官方详情为空） |

覆盖点位 494 → **696 / 1006**（其中 16 条是文字条目）；账本 `NEEDS_SOURCE` 110 → **102**，
只剩 jump（102 个点位的官方详情本来就是空的）。
