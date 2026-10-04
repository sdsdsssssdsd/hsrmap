# Runbook · S7 性能：先消灭查询结构问题（a1-8 十二）

**状态：完成（2026-10-04）**。判定语义不变：**1006 点位的状态矩阵 sha256 与 S0 基线逐位相同**，
而且这次是**两条查法各验一遍**（字符串键 / 正规关系表），外加逐点位对比。

## 1. 两个 N+1

| 位置 | 以前 | 现在 |
| --- | --- | --- |
| `stages.completeness_report()` | 每个点位 1 条候选查询 + 每个候选 2 条子查询 | **固定 5 条**：entries / steps / assets / 关系表（2 条） |
| `GuideDatabase.list_for_keys()` | 每条条目 2 条子查询 | **固定 3 条**：entries / steps / assets（`_child_rows` 批量，500 一段避开 SQLite 变量上限） |

`EntryIndex`（`stages.py`）一次预加载 entries + steps + assets，并建两张倒排表：

```text
_by_key     键 -> 条目（完全相等）
_by_member  set: 成员点位 -> 条目（数字组）
```

点位循环里只剩字典查找，一条 SQL 都不发。顺带解决了**路径特判**：以前靠
`LIKE '%<pid>%'` 预筛，于是 388 会被 3388 的攻略「覆盖」；索引版按规则直接建表，
那个容易出错的中间层整个消失。

现场数字（1006 点位，同机同库）：**3,042 条 SQL → 5 条**；
整份报告的墙钟 **441 ms**（旧结构 348 ms，见下「取舍」）。

## 2. 关系表：补齐 → shadow 对比 → 才允许切

`guide_target` / `guide_entry_target` 早就有，但只覆盖 **37 / 779** 条条目，所以不能直接切。
新模块 `hsrmap/guides/targets.py`：

| 函数 | 作用 |
| --- | --- |
| `key_members(key)` | 一个键点名了哪些点位（与判定层逐字相同的规则：完全相等 / `set:` 数字组；`map:`/`global:` 不算） |
| `sync_relations(db, apply=)` | 按同一套规则把关系补齐（幂等；干跑报计划：630 个点位目标 + 982 条绑定） |
| `relations_in_sync(db)` | 关系表齐不齐——**能不能切**的判据 |
| `shadow_compare(db, points)` | 逐点位对比两条路，报差异样本 |

现场结果：

```text
plan    : targets_created 630 / bindings_added 982
after   : expected 1019 / bound 1019 / missing 0 / in_sync true
shadow  : points 1006 / matches 1006 / mismatches 0 / equal true
```

**切换是「验证过的切换」，不是「换一套试试」**：

* `EntryIndex(lookup="auto")`（各处默认）先问 `relations_in_sync()`，齐才走关系表；
  不齐退回字符串键，并把走的是哪条写进报告 `lookup.mode`；
* `lookup="relation"` 在关系不全时**直接报错**，而不是悄悄漏攻略（那会让完成度虚低）；
* 两条路都在 `tests/test_claims_shadow.py` 里对着 S0 基线逐点验证。

CLI：

```bash
python -m hsrmap guides target-shadow            # 干跑：报计划 + 关系状态 + shadow 对比（rc=0）
python -m hsrmap guides target-shadow --apply    # 补齐关系；不齐/不一致时 rc=2
python -m hsrmap guides completeness --stats --target-lookup auto|string|relation
```

## 3. closure 不重复算同一个账本

三道闸门（`atlas_gates`，7 个主题）和语料健康度（`corpus_health`，全部启用主题）
看的是同一份 (working, published) 账本，以前各算一遍。现在 `closure.shared_ledgers()`
算一次，两处共用（`atlas_gates(..., ledgers=)` / `corpus_health(..., ledgers=, point_counts=)`）。
**主题账本从 22 次降到 15 次**，收口报告的数字一字未变（639 / 1006、账本口径 611、Needs review 18、No public source 102）。

`snapshot_diff` 的覆盖率对比刻意**不共用**这份账本：它比的是「已发布快照 vs 候选快照」两个不同状态，
必须各算各的，共用才会真的算错。

## 4. 验收指标与基线

| 指标 | 口径 | 结果 |
| --- | --- | --- |
| 查询数不随点位数增长 | `completeness --stats`（1006 点位） | **5 条**（旧结构 3,042 条） |
| 同上，逐条目子查询 | `list_for_keys` 1 条 vs 30 条条目 | **3 条 = 3 条** |
| 关系表 shadow | 1006 个官方点位 | **mismatches 0** |
| 墙钟基线（同机，只读库） | `completeness_report` 全量 | **441 ms** |
| 墙钟基线 | `closure-check`（含 offline E2E + 审计 + 关系表） | **18.5 s** |

**取舍要说清楚**：新结构 SQL 少了三个数量级，但每次要把 5,706 个步骤读进内存，
所以单次墙钟**约慢 90 ms**（348 → 441 ms）。这是刻意的：验收指标是结构（查询数），
而且 `EntryIndex` 是后续关系查询、Web 侧复用同一份预加载的基础。

## 5. 证据（测试与退出码）

* `tests/test_guide_perf.py`（3 条）：`list_for_keys` 1 条 vs 30 条条目查询数相同、
  子行挂得对、`completeness` 在小主题与大主题上查询数相同且 `lookup.mode` 有值；
* `tests/test_guide_targets.py`（6 条）：键规则（含 `map:`/`global:` 不算点位证据）、
  `sync_relations` 幂等与干跑计划、抽掉一条绑定后 shadow 必须报错、
  `auto` 退回 / `relation` 拒绝、建完索引后 200 个点位 0 条查询、CLI `target-shadow` 的 rc 契约；
* `tests/test_claims_shadow.py`（6 条，`--run-data-e2e`）：三种 lookup 模式的状态矩阵
  **都**等于 S0 基线，且两条路逐点位挑出同一条攻略。

现场复核：`pytest -q` = **619 passed / 84 skipped**（S6 时 611 / 80）、`ruff check .` All checks passed、
`repo-hygiene` PASS、`closure-check` PASS（11 栏，含 Evidence gates 5079 claims / 0 gap）。
