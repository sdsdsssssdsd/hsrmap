# Runbook · S5 Evidence 一等化（a1-8 五 / 十一）

**状态：完成（2026-10-04）**。硬约束（判定语义不变）已证明：**1006 点位的状态矩阵 sha256 与 S0 基线逐位相同**，
`closure-check` PASS、`Ready to grab 1006 / 1006`。

## 1. 版本化迁移（a1-8 十一）

`hsrmap/guide_db.py` 现在有一张 `schema_migration(version, name, checksum, applied_at)` 与六条幂等迁移：

| 版本 | 名字 | 内容 |
| ---: | --- | --- |
| 1 | initial | 基线 schema（`CREATE TABLE IF NOT EXISTS`，老库同构） |
| 2 | review_signature | `review_item` 补列 + 签名索引 |
| 3 | page_qa | `guide_page` 补 QA 列 |
| 4 | extraction_metrics | `guide_asset_cache.phash`、`extraction_run` 的 token/延迟/成本列 |
| 5 | claim_evidence | `guide_claim_evidence` 表（证据声明） |
| 6 | evidence_indexes | 证据/步骤/资产/目标关系索引 + `guide_entry(status, source_point_id)` |

规则：

* **写入型打开（create / readwrite）**按序补迁移，同一事务，记住 (version, name, checksum, applied_at)；
* **只读打开永不迁移**——发布库应当是生成时就迁好的不可变产物（viewer 只读打开 published.db ✓）；
* 版本号没变但迁移内容变了（checksum 不一致）会记进 `db.migration_drift`，不阻断但可发现；
* `db.schema_version()` 报当前版本；`db.applied_migrations` 报这次补了什么。

## 2. 证据声明表

```sql
guide_claim_evidence(id, guide_id, step_id, target_id,
                     claim_kind,      -- SCOPE / LOCATE / SOLVE
                     evidence_level,  -- OFFICIAL / COMMUNITY_TEXT / TRANSCRIPTION / CROSS_INFERENCE
                     grounding_tier,  -- EXACT / FRAGMENT / ASSEMBLED / IMAGE_REF / DERIVED
                     source_page_id, official_point_id, asset_sha256, basis_json,
                     method_version, created_at)
```

分类规则（`hsrmap/guides/claims.py`，纯函数，可单测）：

| 输入 | claim_kind | evidence_level | grounding_tier |
| --- | --- | --- | --- |
| 官方点位条目的说明文字 | 有解法信号→SOLVE，否则 LOCATE/SCOPE | `OFFICIAL` | `DERIVED` |
| 以 `[图解法转录 <sha>]` 开头 | SOLVE | `TRANSCRIPTION` | `IMAGE_REF`（并记 `asset_sha256`） |
| 其它正文（有方向序列/动作词） | SOLVE | `COMMUNITY_TEXT` | `EXACT` / `FRAGMENT` / `ASSEMBLED` / `DERIVED` |
| 其它正文（说「在哪儿」） | LOCATE | `COMMUNITY_TEXT` | 同上 |
| 范围条目（`set:`/`map:`/`global:`）里的背景句 | SCOPE | `COMMUNITY_TEXT` | 同上 |
| 交叉推断（人工登记） | 任意 | `CROSS_INFERENCE` | 必须带 `basis_json` |

**现有 audit 的成果全部保留**：`EXACT/FRAGMENT/ASSEMBLED` 就是 `COMMUNITY_TEXT` 下的子类型，
`[图解法转录]` 从「一个前缀」升级成「TRANSCRIPTION + IMAGE_REF + 图片 sha」。

## 3. 完成度不再只有一个 1006/1006

`hsrmap guides completeness` 的每行多两个字段 `locate_evidence` / `solve_evidence`，
报告顶层多一个 `evidence_layers`：

| 层 | 当前值 | 含义 |
| --- | ---: | --- |
| 直接证据 | **960** | 官方地图 / 社区正文就够 |
| 图解法转录 | **46** | 必须加上 `[图解法转录]` 那一步才成立 |
| 交叉推断 | **0** | 必须加上写明依据的推断才成立 |
| 未完成 | **0** | 缺定位或缺解法 |

`--markdown` 也会带上这张分层表。逐点分布：761 个点位不需要解法、199 个靠社区正文、
46 个靠图解转录。

## 4. 回填与查询

```bash
python -m hsrmap guides claims --backfill            # 干跑：会写多少条、各等级多少
python -m hsrmap guides claims --backfill --apply    # 落库（幂等：先删这些 guide 的旧行再写一遍）
python -m hsrmap guides claims                       # 现状汇总 + 证据摘要 sha256
```

本次落库：**5,079 条声明**（COMMUNITY_TEXT 4,460 / OFFICIAL 555 / TRANSCRIPTION 64；
SCOPE 3,814 / LOCATE 662 / SOLVE 603）。`claim_digest()` 是这些声明的 sha256，
下一步（S6）会把它并进 snapshot-manifest，让「步骤文字没变但证据等级退化」也能被 diff 看见。

## 5. 证据

* `tests/test_claims.py`（6 条）：转录前缀识别、官方/转录/社区三种定级、tier 落档、
  `point_evidence` 与 `evidence_layers` 的算法、回填幂等（重复 apply 不翻倍、摘要不变）；
* `tests/test_claims_shadow.py`（3 条，`--run-data-e2e`）：**状态矩阵 sha256 == S0 基线**、
  证据分层覆盖全部点位且 missing = 0、已发布条目都有声明；
* 现场复核：`schema_version() = 6`、`applied_migrations = [1..6]`、`migration_drift = []`；
  `pytest -q` = 594 passed / 80 skipped；`closure-check` PASS；`repo-hygiene` PASS；`release` 535 项 / 1.51 MB。
