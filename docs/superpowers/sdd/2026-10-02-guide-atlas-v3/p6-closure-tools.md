# P6 — 收官工具：正文重算 / chrome 清理 / 接地分级

Spec: `a1-6.md` §33（quality debt → 0）；上游 `p5-published-audit.md`。

## 1. 为什么需要正文重算（`guides refresh-text`）

抽取器会随时间变好，但 `guide_page.raw_text_path` 是**导入当天**那个解析器写下的。
旧正文一旦短于真实文章，审计就会把页面里真实存在的步骤读成 "幻觉"。

```
python -m hsrmap guides refresh-text            # 干跑：哪些页面的正文会变
python -m hsrmap guides refresh-text --apply    # 用当前解析器重写 raw_text_path
python -m hsrmap guides refresh-text --page 96 --apply
```

- 只动 `guide_page.raw_text_path` / `parser_version`，不动 guide / step / asset。
- 正文写入新的 raw run（`data/guides/raw/<run>/pages/pN.txt`），旧文件保留，可回滚。
- 实测：139 页中 90 页正文变化（16 页变长、74 页变短——旧解析器把侧栏也算成正文）。

### 触发它的解析器 bug（已修）

`blocks.py` 里 `_omit` 是**跨块状态**：只要出现 `17173 新闻导语` 或游戏名标题，
它就把之后所有段落全部丢弃，直到遇到下一个标题。现在只有真正的尾部标题
（`相关推荐` / `热门推荐` / `广告` / `关于崩坏…` / `更多相关`）才结束正文，
其余被跳过的标题只丢自己。

## 2. 接地分级（`audit.grounding`）

| 级别 | 含义 | 审计结论 |
| --- | --- | --- |
| `EXACT` | 步骤文本（剥离我们自己的编号/序数后）就是文章的一段 | 通过 |
| `FRAGMENT` | 由多个文章片段拼成，逐句都能在文章里找到 | 通过 |
| `ASSEMBLED` | ≤24 字的短标签，每个词都在文章里（如 `3层区域 王下一桶`） | `DERIVED_LABEL`（可见、不阻断） |
| `NONE` | 文章里找不到 | 再看 chrome：命中记 `CHROME_STEP`，否则 `HALLUCINATED_STEP`（HARD FAIL） |

两条对称规则是关键：

1. 序数（`第3个` / `第2处` / `第1次`）从**步骤和正文两侧**同时剥离，
   否则 `第3个【梦境迷钟】` 永远匹配不上页面里的 `第3个梦境迷钟`。
2. 我们自己的列表编号（`（1）` / `1.` / `2、`）在归一化前先剥离，它不是内容。

## 3. chrome 清理（`guides chrome-prune`）

```
python -m hsrmap guides chrome-prune                                  # 只报告
python -m hsrmap guides chrome-prune --apply                          # 删步骤并重排 step_index
python -m hsrmap guides chrome-prune --apply --quarantine-empty       # 顺便隔离空掉的 entry
```

- 判据：步骤文本在**整页 chrome**（侧栏、下载榜、推荐位）里有，但在**文章正文**里没有。
  这不是编造，是当年抽取器读错了容器。
- 删除后重排 `step_index`：图片跟随它所属的步骤；挂在被删步骤上的图片解绑
  （文件仍在内容寻址缓存里，不删磁盘）。
- `--quarantine-empty`：如果一条 entry 的步骤**全部**是 chrome，它会被标成
  `QUARANTINED_CHROME`——除 `published` 之外的状态都不可发布，所以它不会被 "空着发布"。
- 实测：删除 236 步；30 条 entry 变成 `QUARANTINED_CHROME`；发布库 183 → 153 条，
  `CHROME_STEP` 234 → 0、`HALLUCINATED_STEP` 12 → 0。

## 4. 覆盖损失必须显式豁免（`allowed_regressions.yaml`）
## 5. 隔离重建（`guides rebuild-quarantined`，§30 / §33 步 9）

```bash
python -m hsrmap guides rebuild-quarantined             # 只看哪些能重建
python -m hsrmap guides rebuild-quarantined --apply     # 重建并改回 published
```

对每条 `QUARANTINED_CHROME` entry 只问一个问题：**它自己的文章真的讲到这个 target 吗？**

- **证据必须来自 target 专属名字**（map_name / region / label）。topic 名（例如「折纸小鸟」）出现在每一篇同 topic 的页面上，
  单靠它会让「11 张地图总览页」同时"证明"18 个地图 target —— 那正是这批 entry 当初被错误匹配的原因。
  MAP_LABEL target 只看它自己的地图名，绝不看同 topic 的其它点。
- **步骤按「标题 + 其下编号指令」整段保留**：攻略页通常是 `第N个【目标】…` 后面跟 `（1）（2）…`，
  编号行不会再重复目标名；只匹配单句会把标题留下、把真正的解法丢掉。
- **过滤**：页面标题（与 `guide_page.title` 同源的行走掉）、`请看/希望/大家好/本篇/本期/以上就是` 之类套话、
  以及短于 12 字的列表项（`2，筑梦边境` 不是指令）。证据不足（<2 条，且单条 <24 字）即不重建。

实测（现网 30 条隔离项）：**5 条重建成功**——

```text
dream_ticker 2124 / 2133（朝露公馆迷钟，12 步，逐条对应文章里的「第N个…（1）（2）」）
king_bucket 2448 / 2508（流梦礁王下一桶，各 3 步）
dimensional_trotter 1883（稚子的梦宝箱，3 步 + 4 图）
```

其余 25 条无法从自身文章获得证据，写入 `data/guides/reports/quarantine.json`：
`NO_ARTICLE_TEXT` 7（TapTap 页面 JS 渲染，正文为空）、`NO_ARTICLE_EVIDENCE` 17（总览页从不提该地图）、
`NO_TARGET_KEYS` 1（官方没有可搜索的名字）。**这些目标的缺口是"需要另一个源"，不是"可以编内容"。**

因为上一轮发布时这 30 条就已经被排除，本次发布会话里没有任何 target 掉线：
`TARGET_REMOVED 0 / GUIDE_ADDED 5 / GUIDE_CHANGED 5`，闸门 `REVIEW`（不阻断）。
于是 `data/guides/allowed_regressions.yaml` 现在是空的 `allowed_regressions: []`——豁免用不上了，
缺口改由 quarantine.json 记账。发布库 153 → **158 条**，覆盖率 150 → **155 / 611**。

## 6. 顺带修掉的解析器问题

`blocks.py` 现在会丢弃**站点家具容器**里的内容（`class` 含 `nav / menu / breadcrumb / footer / sidebar / related / recommend / rank / advert / ad` 的 `div`）：
游民星空的导航列表（`网页游戏 / 热门单机 / 近期新作 / 即将上市`）此前会被当成正文块存进 `raw_text_path`，
既污染审计语料，也让重建的攻略以导航文字开头。新增 `BlockList.dropped_chrome` 计数与回归测试。

## 7. 覆盖损失必须显式豁免（历史）

删掉 30 条 entry 会让 5 个 topic 覆盖下降，闸门因此**正确地**返回 FAIL：

```
coverage regression: origami_bird 43 -> 25
coverage regression: floating_grease 37 -> 30
removed published entries: 30
```

处理方式是写豁免（a1-6 §19），而不是 `--force`：

```yaml
allowed_regressions:
  - topic: origami_bird
    target: "1883"
    reason: QUARANTINED_CHROME - every step was page chrome; awaiting rebuild from the article
    kind: GUIDE_REMOVED
```

`data/guides/allowed_regressions.yaml` 里 30 条与 30 个被隔离 entry 一一对应；
重建一条就删一条。带豁免后 `publish-snapshot --waivers … --dry-run`：
`hard_fail: []`、`coverage_fail: []`、`result: REVIEW`（42 条 GUIDE_CHANGED 待人工复核）。
## 5. 合并不是损失：`GUIDE_MERGED`（2026-10-03）

去重（`guides dedupe-entries`）会把同一目标的多条指南合并成最厚的一条，被合并的 entry 会在下一次
`publish-snapshot` 里离开快照。旧的 diff 把它记成 `GUIDE_REMOVED`，于是闸门直接 FAIL——
但快照并没有丢内容：**同一个目标仍然有一条（更厚的）指南**。

所以 diff 层新增了第四类改动：

```json
{ "type": "GUIDE_MERGED", "guide_id": 174, "target": "1142", "survivor": 219 }
```

- 判定：被移除的 entry 的 `source_point_id` 在候选快照里**仍有**指南 → `GUIDE_MERGED`（REVIEW 级，不阻断）；
- 候选快照里该目标**一条都不剩** → 仍然是 `GUIDE_REMOVED` + `TARGET_REMOVED` + `COVERAGE_DECREASED`，硬失败；
- 因此豁免文件继续留给**真正的**覆盖率损失，不需要为去重写 30 条 waiver（它仍是空的）。

实测：`GUIDE_MERGED 30 / GUIDE_REMOVED 0 / TARGET_REMOVED 0 / COVERAGE_DECREASED 0`，
`result: REVIEW`，原子切换后发布库 198 → **168 条 entry**，覆盖 179 点不变。
