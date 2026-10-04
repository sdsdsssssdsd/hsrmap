# Crawler Sprint 3 — Intelligence

Spec: `a1-6.md` §六（Priority Frontier）/ §七（Target-driven Discovery）/ §八（Search Evidence Ledger）/ §十八（Observability）。
退出条件：**crawler 知道"下一页最值得抓什么"，而不只是执行人工 seeds。**

## 1. Search Evidence Ledger（`hsrmap/guides/evidence.py`）

```text
source_search_run     一次搜索：topic / target / query / provider / status / result_count
source_search_result  一个候选：rank / url / canonical_url / family / host / score / decision / reason
```

决策词表（§八原文）：`ACCEPTED / DUPLICATE / MIRROR / IRRELEVANT / JS_ONLY / BLOCKED / ALREADY_IMPORTED`。
写错决策直接 `ValueError`——账本不接受没定义的结论。

| API | 作用 |
| --- | --- |
| `record_search(...)` | 一次搜索 + 全部结果（run 包住 results） |
| `classify_candidate(url, db=…)` | 抓取前就判定：新文章 / 镜像 / 已入库 / 重复 / JS / 封锁 / 无关 |
| `searches_for(topic=…, target_key=…)` | 搜索历史（含每条 run 的决策分布） |
| `searched_targets(...)` / `unsearched_targets(...)` | 哪些 target 有证据、哪些**从未搜过** |
| `summary(...)` | 搜索次数、结果数、有证据的 target 数、决策分布 |

**`guides corpus` 现在自己写账本**：每次运行开一条 `corpus:<topic>` run，每个 URL 落一条决策
（`ALREADY_IMPORTED` / `MIRROR` / `BLOCKED`（robots 或抓取失败）/ `ACCEPTED` / `JS_ONLY` / `IRRELEVANT`）。
于是 `NO_PUBLIC_SOURCE_FOUND` 从此可审计：能回答"这个 target 搜过什么、为什么停"。

## 2. Target-driven Discovery（`hsrmap/guides/planner.py`）

`discovery_plan(db, topic)`：从 ledger 里挑 `NEEDS_SOURCE` / `NO_PUBLIC_SOURCE_FOUND` 的 target，
按 §七 生成 query family（游戏全名 + 简称 × 地区/层 + 全收集/解谜），并附上该 target 已有的证据状态。

实测（现网 `floating_grease`）：8 个缺源 target、8 个从未搜过，输出示例

```text
point:5538  NEEDS_SOURCE  千星城中心城区 1层
  崩坏星穹铁道 浮脂溯源·二次元ROTATE！ 1层
  崩坏星穹铁道 1层 浮脂溯源·二次元ROTATE！
  浮脂溯源·二次元ROTATE！ 1层 全收集
  浮脂溯源·二次元ROTATE！ 1层 解谜
  崩铁 …（简称两式）
```

## 3. Priority Frontier（§六）

规则表原样落地，每条规则在结果里留下 `{rule, weight, detail}`，分数可逐条复算：

```text
+50 NEEDS_SOURCE target          +30 高产 host（已有 published）
+20 article-family 续页          +10 未见 canonical
-30 历史 QA_FAIL host            -50 duplicate family
-80 JS-only                      -100 known mirror
```

`frontier(db, candidates)` 把候选按分数排序；候选来自 `candidates_from_evidence()`（已 ACCEPTED 但未入库）
与 `candidates_from_seeds()`（topic 档案声明的 URL）。实测 `--include-seeds`：
`ol.3dmgame.com/gl/354807.html` 得 `+30 high_yield_host -50 duplicate_family = -20`，排在真正的新文章之后。

## 4. Yield / Host Health（§十八/§十九）

`host_health(db)` 逐 host 统计：pages、QA_PASS/QA_FAIL/**QA 未检**、JS-only、published、
资产 FETCHED+CACHE_HIT / HTTP_BLOCKED / 其他失败，以及 `qa_pass_rate`、`asset_success_rate`、`score`、`high_yield`。

两个刻意的诚实处理：

- **没检查过 ≠ 失败**：未 QA 的页面计入 `qa_unchecked`，不拉低通过率（现网大部分页面属于这一类）；
- **资产在 CDN 上**：页面 host 没有资产记录时 `asset_success_rate = null`（中性），不会把 gamersky 判成"资产全挂"。

实测（现网）：`www.gamersky.com` 72 页 / 114 条 published → `score 100`、`high_yield`；
CDN host（如 `cdn.test` 例子）各自统计资产成功率。`source_yield()` 按 published 排序给出采集价值榜。

`target_yield(db, topic)`：每个 target 现有多少条已发布 guide，配合 ledger 状态看缺口。

## 5. CLI
## 8. 审批已锚定的草稿（`guides review-approve`，2026-10-03）

```bash
python -m hsrmap guides review-approve --topic origami_bird            # 看哪些能过
python -m hsrmap guides review-approve --topic origami_bird --apply    # 每个地图目标只过最厚的那条
```

准入条件（写死在 `review/service.py::approve_anchored`）：

- `target_type = MAP_LABEL` 且 `target_key` 非空（合成键，ADR-001）；
- 草稿里至少有一张图的 `resolved_map.status == PAGE_ANCHOR`（页面自述的地图，不是猜的）；
- 同一个 target_key 只批准**步骤最多**的那条，其余留在队列；
- 草稿的图片 sha 会挂到最后一步上（没有步骤时生成一条 image-only 步骤），并记 `binding_method: PAGE_ANCHOR`。

本轮实测：approve 筑梦边境 150 的 3 步 / 3 图草稿 + 大剧院 255 的 50 步 / 23 图草稿 →
发布库 158 → **160 条**（3261 步），`TARGET_ADDED 1 / GUIDE_ADDED 2 / COVERAGE_INCREASED`，
闸门 REVIEW（不阻断），覆盖率 155 → **157 / 611**，审计仍是 hallucinated 0 / chrome 0 / 绑定资产 0 破损。
新发布内容示例：`1号小鸟位置 / 藏在知更鸟海报里 / 2号小鸟位置 / 藏在休息去后的风管里（拽三次）…`（大剧院 20 只，50 步 23 图）。

## 7. 第一次真实目标驱动采集（2026-10-03）

按 §七 的流程跑了一遍完整闭环（`web_search` 找候选 → 证据账本 → `guides corpus --url … --max-pages` job → Review）：

```text
query   崩坏星穹铁道 筑梦边境 折纸小鸟 位置 攻略
  → https://news.17173.com/z/xqtd/content/02082024/182919650.shtml   ACCEPTED
query   崩坏星穹铁道 折纸小鸟 匹诺康尼大剧院 攻略
  → https://news.17173.com/content/05092024/140603307.shtml          ACCEPTED
```

结果：两页 **QA_PASS 入库**（page 140 / 141），`corpus:origami_bird` job 因 `max_pages` 停在 PAUSED 并写了 crawl-report；
证据账本 6 条 ACCEPTED。发布库不受影响——语料进的是 Review（a1-5 的规矩：never auto-publish）。

### 这次暴露并修掉的真问题：页面级地图锚点

两页的图片都按「1号小鸟 / 2号小鸟」编号，单位解析出的 `map_name_raw` 是鸟的序号，
`resolve_map()` 自然 NO_MATCH，于是 12 个 Review 草稿全都绑不到官方地图。

修复：`regions/resolver.py::resolve_page_map()` + `apply_page_anchor()`——
**当页面标题/标题块里恰好出现一个官方地图名时，这一页的所有单位都属于那张图**（`status: PAGE_ANCHOR`，confidence 0.6，
`anchored_by: page_title` 留痕）；出现两个以上地图名就什么都不决定（多地图合集页不会被强行锚定）。
单位的 `resolved_map` 只有在自己 NO_MATCH 时才让位给页面锚点。

实测：`筑梦边境地图折纸小鸟全收集` → 150 筑梦边境 ✓（4 条 Review 项带 `PAGE_ANCHOR`），
`匹诺康尼大剧院-20只折纸鸟全收集` → 255 匹诺康尼大剧院 ✓。
两页上 22 条既无锚点又无候选的草稿被标 `REJECTED`（否则会永远躺在队列里）。

`guides corpus` 新增 `--url`：把 frontier/证据账本挑出来的候选直接交给采集，而不是只能跑 seeds。


```
python -m hsrmap guides search-log  [--topic X|all] [--target K] [--limit N] [--json PATH]
python -m hsrmap guides search-plan [--topic X] [--limit N] [--no-searched]
python -m hsrmap guides frontier    [--topic X] [--include-seeds] [--limit N]
python -m hsrmap guides yield       [--topic X]
```

`guides closure-check` 的 JSON 新增 `discovery` 证据段（搜索次数、结果数、有证据的 target 数、host 数、规则数）。

## 6. 测试

- `tests/test_guide_evidence.py`（5）：run/results 落库、未知决策被拒、七种决策判定、未搜 target、CLI 报告 + JSON。
- `tests/test_guide_planner.py`（6）：query family 形状、缺口计划与证据状态、规则表逐条打分、frontier 排序、host health/yield、CLI。
- `tests/test_guide_corpus.py`（+1）：一次 corpus 运行留下 `ACCEPTED → ALREADY_IMPORTED → MIRROR` 的完整证据链。
## 9. 纯文字草稿的**范围证据**规则（`review-approve --text-only`，2026-10-03）

`approve_grounded` 的第三条通路是「整区绑定」（`REGION_SET` → `set:<pid-…>:topic:<key>`）：
页面自己点名了官方区域，并说出该区域有多少个目标，且这个数字**与官方点位数完全相等**时，
才把该区域的全部点位作为一个 `POINT_SET` 目标发布。三轮实测后规则补齐为：

| 环节 | 规则 |
| --- | --- |
| 数量写法 | `一共/共/总计/合计` + 可选 `有/为/是` + 数字（阿拉伯或中文）+ `个/只/处/张/座/根` |
| 主题名 | 官方标签**及其去装饰头**（`浮脂溯源·二次元ROTATE！` → `浮脂溯源`）参与匹配 |
| 单一区域 | 数字可挂在区域名上，也可挂在主题名上（「共10只，有的若虫…」） |
| 多区域 | **每个**被点名区域都要自报数量，且只有它们的**和**算页面的范围 |
| 证据文本 | 范围声明从**整篇文章**读（草稿步骤只是抽取片段）；步骤仍须逐条可溯源 |
| 正文门槛 | `REGION_SET` 至少 3 条实质步骤，否则 `REGION_SET_TOO_THIN` |

为什么这么严：一个页面若说「替罪羊共3个」而该区域官方有 10 个点，绑定就会把 7 个它从没提过的点位算成「有来源」。
宁可 `count=0` 不绑定，也不让覆盖率说谎。
### 8. 整区绑定的两处收窄（2026-10-03，第 7 轮）

**(a) 成员来自草稿，数字来自页面。** 一个 review item 只是页面的一片（「3、匹诺康尼大剧院」），
所以**成员区域**取该 item 自己的步骤+地图名，**数量**取整篇文章（页面自己的声明）。
否则一条「大剧院」的步骤会被当成「流梦礁+大剧院」7 个点位的攻略。

**(b) 同一批里重叠的集合只留最厚的那条。** 一页覆盖三片区域会产出三个集合，若互相包含，
查看器会对同一个点显示三张卡。现在按「步骤数 → 成员数」排序，先到者占用点位，
后来且重叠的记为 `OVERLAPS_RICHER_SET`。

**(c) 点位已全部发布 = 没有新覆盖。** 集合的每个成员都已有已发布攻略时，再发布只是多一张卡，
记为 `NO_NEW_COVERAGE`（对比：部分重叠仍然允许，因为整区攻略可能比点位攻略更完整）。
`review-approve` 的返回也改成 `targets` = 真正会发布的条数（新增 `candidates` 记录过滤前的候选数）。
### 9. 区域匹配的两条补充（2026-10-03，第 10 轮）

| 规则 | 说明 |
| --- | --- |
| 房间随母区域 | `_room_base()`：`朝露公馆-2` 的母区域是 `朝露公馆`；页面点名母区域时房间**加性**纳入（母区域包含房间，不是同级歧义） |
| 整页数量回退 | 多区域页面逐区域都读不到数字时，回退读整页的「共N只」；仍然必须与 member 数完全相等 |

仍然拒绝的情形（本轮实测）：页面写「共8只」而母区域+房间=10；页面只覆盖两片同名区域中的一片（3≠6）；
官方区域名与页面写的一字之差（`灾梦余梦` vs `灾梦余温`）**不做模糊匹配**——两个区域点数相同的情况下，
猜错不会被数量校验发现，这正是「数字相等」这条底线要防的事。
### 10. 作用域的三级回退（2026-10-03，第 11 轮）

`_region_set_binding` 现在按**由窄到宽**取成员：

1. **草稿标题**（`map_name`，例如「生研院」）——抽取时它就是页面小标题，最贴近这一片内容；
2. 草稿正文（标题 + 步骤）——标题点不出区域时用；
3. 整篇文章——最后兜底。

数量仍然只从**整篇文章**读（页面自己的声明），并且必须与成员数完全相等。
这样「正文里顺口提到别的区域」不会再把别的区域的点拖进集合；
反例见测试 `test_the_item_heading_decides_the_scope_not_a_passing_mention`。
## 12. 结果判定必须读评估结果，不是原始检查（2026-10-03，第 14 轮）

`ingest_page` 同时返回**原始检查**（`qa`）和**评估结论**（`qa_status` / `qa_reason`）。
`corpus` 曾从 `qa` 里读 `pass`/`reason`，于是：

- `reason` 恒为空 → 账本里全是 `QA reject: `；
- 需要浏览器的页面被记成 `IRRELEVANT`，而不是 `JS_ONLY`；
- `planner` 的 `js_only` 权重（-80）永远不生效，同一批 JS 站会被反复排队。

现在判定只认 `qa_status`/`qa_reason`（原始 `qa` 仅作兼容回退），并且报告字段 `qa_pass` 与判定同源。
证据账本的一条记录因此能回答 a1-6 §八 的两个问题：**找过没有**（有 run + query）、
**为什么停**（`JS_ONLY`：「要浏览器」，而不是「与目标无关」）。
## 14. 计数锚定与图集正文（2026-10-04，第 19 轮）

**锚定计数**：`anchored_count(text, region)` 抓「区域名之后紧邻的那个数量」，
在规范化后的文本上匹配（`『甲区』` 与 `「甲区」` 等价）。多区域路径顺序是：
逐区域段落计数 → 逐区域锚定计数 → 整页主题计数。三层都要求**数字与成员数完全相等**。

**图集正文**：整区绑定在文字不足时不直接判死，而是看页面有没有「每个点位一张内容截图」：

| 门槛 | 值 |
| --- | --- |
| 角色 | `puzzle_step` / `location_map` / `map` / `screenshot` / `step` / `gameplay` / `guide` |
| 尺寸 | ≥ 400×300（手机截图常为 560×747，故下限低于 600） |
| 数量 | ≥ 成员数（每个点位至少一张） |
| 记录 | 审批结果 `image_source: gallery` |

广告与无关图由角色过滤排除；图标与缩略图由尺寸过滤排除。文字路径优先，图集只在前者不足时启用。
### 15. 图片回退：模型没分类过的页面怎么办（2026-10-04，第 20 轮）

`vision/roles.py::classify_role` 需要模型 provider；没有 provider 时角色恒为 `unknown`（全库大多数页面如此）。
所以图集规则不能只看角色，否则等于给「被模型看过的页面」开后门。回退顺序：

1. 角色 ∈ `puzzle_step/location_map/…` → 内容图；
2. 角色为 `unknown`/空 → **规则判定** `assets/relevance.py::judge_image`（尺寸、长宽比、URL 特征、重复）；
3. 其它角色（`advertisement`/`unrelated`/`cover`）→ 不算内容图。

两条路都要求缓存尺寸 ≥ 400×300，且最终必须满足「每个点位至少一张」。
这样图集能力对**整个语料**生效（7 → 99 页），而不是只对跑过模型的页面生效。
