# Runbook · S6 证据进 publish gate（a1-8 六）

**状态：完成（2026-10-04）**。硬约束（判定语义不变）仍然成立：**1006 点位的状态矩阵 sha256 与 S0 基线逐位相同**，
`closure-check` 新增一栏 `Evidence gates` 后整体仍是 **PASS**，`Ready to grab 1006 / 1006`。

S5 把「凭什么」变成了可查询的声明；S6 让**没有凭据的快照发布不出去**。

## 1. 新规则（a1-8 六）

```text
HALLUCINATED_STEP > 0                     HARD FAIL   （不再区分新旧，旧债也拦）
UNGROUNDED_STEP > 0                       HARD FAIL
TRANSCRIPTION without referenced asset    HARD FAIL
CROSS_INFERENCE without basis refs        HARD FAIL
required claim has no evidence provenance HARD FAIL
claim 与当前语料不一致（内容改过没重建）   HARD FAIL   （STALE_CLAIM）
上次发布有声明、这次整条没了               HARD FAIL   （EVIDENCE_REMOVED）
direct → transcription/inference 降级      REVIEW / WAIVER REQUIRED（EVIDENCE_DOWNGRADED）
```

问题词汇表在 `hsrmap/guides/claims.py::CLAIM_PROBLEMS`：

| problem | 触发条件 |
| --- | --- |
| `TRANSCRIPTION_ASSET_MISSING` | 声明是转录却没写是哪张图；或写了但磁盘上没有那个文件 |
| `TRANSCRIPTION_ASSET_NOT_ATTACHED` | 说了是哪张图，但那张图没挂在这条攻略（或它同一来源页）上 |
| `INFERENCE_WITHOUT_BASIS` | `CROSS_INFERENCE` 的 `basis_json` 为空 / 解析不出引用列表 |
| `CLAIM_WITHOUT_PROVENANCE` | 页 / 官方点 / 图 / 依据四个来源一个都没有 |
| `STEP_WITHOUT_CLAIM` | 该条目已经声明过证据，却有步骤没被覆盖（先跑 `claims --backfill --apply`） |
| `STALE_CLAIM` | 表里的声明 ≠ 按当前语料重算的声明（内容改过、声明没重建；或步骤已删的孤儿行） |
| `EVIDENCE_REMOVED` | 已发布快照里这条条目有声明，候选快照一个都不剩 |

**为什么这几条让「等级退化」真的拦得住人**：`EVIDENCE_DOWNGRADED` 走 REVIEW 层，
只有 `allowed_regressions.yaml` 里写明 `target`（或 `topic`）才算豁免——默认是「停下来看一眼」。

### 「已经声明过证据」这个前提

`STEP_WITHOUT_CLAIM` 只对**已经声明过证据的条目**生效；整库一条声明都没有时，
gate 报 `adopted: false` 而不是把整个发布挡住。证据层是新加的一层，
没跑过回填的库被这条规则整库挡住只会逼人绕过门；一旦第一版声明落库，漏步就再也藏不住。
跨快照那一半不受影响：**发布库有、候选库没有**照样 HARD FAIL（`EVIDENCE_REMOVED`）。

## 2. 声明随快照一起发布

`hsrmap/guides/publishing/sync.py` 现在把 `guide_claim_evidence` 和 steps / assets 一样同步：

* `clear_claims()` 先清（未发布、被删的条目也要清），**必须在 `guide_entry` 落库之后**再插
  （`guide_id` 有外键）；
* 不复制 `id`：`--backfill` 是全表重建，跨库沿用旧 id 会撞上别的条目残行；顺序不变，摘要照旧可复现。

发布库没有声明，manifest 的 evidence digest 就是空的，等级退化也就无从对比。

## 3. evidence digest 进 snapshot-manifest

`MANIFEST_SCHEMA_VERSION` **3 → 4**：

```json
{
  "schema_version": 4,
  "evidence": {
    "digest": "cd08466d…",          // 整库声明的 sha256
    "by_guide": {"1": "1d4969…"},   // 逐条目，进 manifest_digest
    "summary": {"by_level": {"COMMUNITY_TEXT": 4460, "OFFICIAL": 555, "TRANSCRIPTION": 64}}
  }
}
```

摘要算上 `evidence_level | grounding_tier | asset_sha256 | basis_json | method_version`：
步骤文字一个字没变、等级从 COMMUNITY_TEXT 掉到 CROSS_INFERENCE，`entries.*.content_sha256` 不动，
`evidence.by_guide` 会动，`manifest_digest` 就会动。**换了规则重建过的声明也算不同状态**（method_version 进摘要）。

`snapshot_diff` 多一个顶层 `evidence` 段：候选/已发布各自的 claims 数、digest、逐等级计数、
`findings`（上面那张表）、`downgrades` / `upgrades`、`losses`。

## 4. 发布路径的收口

* `publish_atomic` 在**切换成功之后**重写 `reports/snapshot-manifest.json`：
  以前真发布不写 manifest，只写 dry-run 那一份，读者拿到的 digest 还是切换前的；
* `closure-check` 新增 `Evidence gates` 一栏（claims 数 / 缺口数）与 `Evidence layer` 区块；
* `snapshot-diff.md` 多一段 `## Evidence`（digest 迁移、逐等级计数、HARD 与 REVIEW 项）。

## 5. 本次现场数字

| 项 | 值 |
| --- | --- |
| 已发布条目 | 779 |
| 声明 | 5,079（COMMUNITY_TEXT 4,460 / OFFICIAL 555 / TRANSCRIPTION 64） |
| 候选 digest | `cd08466d868cc25d…` |
| evidence findings | 0（转录 64 条全部指到挂在该条目/同页的图，且图在磁盘上） |
| audit | HALLUCINATED 0 / UNGROUNDED 0 / MISSING_ASSET 0 / IMAGE_TRANSCRIBED 64 |
| 发布 | `publish-snapshot` 原子切换成功（备份 `published.db.bak-*`），779 条 + 5,079 条声明入库 |
| closure | **PASS**（11 栏全 PASS，含新的 Evidence gates） |
| 完成度 | 1006 / 1006（到点即完成 829 + 含解法完成 177） |

## 6. 证据（测试与退出码）

* `tests/test_claims_gate.py`（17 条）：七条规则各一条 + 正例（转录自证通过、
  manifest digest 随等级变化、发布库不落地就空 digest、发布库同步不翻倍）+
  CLI 层 `publish-snapshot --dry-run` 遇证据缺口 **rc=2**；
* `tests/test_guide_audit.py`：`test_preexisting_hallucination_*` 改成「新旧都拦」，
  新旧仍然分开报（closure 要看的债和这次引入的问题不是一件事）；
* `tests/test_guide_snapshot_diff.py` / `test_guide_snapshot_manifest.py` / `test_guide_atomic_publish.py`：
  夹具补上**来源页**（`_ground()`）——a1-8 六 起 UNGROUNDED 是 HARD FAIL，
  合成条目必须有自己的语料，否则测的不是发布门；
* 现场复核：`pytest -q` = **611 passed / 80 skipped**（S5 时 594；S6 新增 17 条）、
  `ruff check .` All checks passed、`repo-hygiene` PASS（536 文件 / 基线内 8 / 新增 0）、
  `closure-check` PASS、`pytest --run-data-e2e tests/test_claims_shadow.py` 3 passed
  （状态矩阵 sha256 == S0 基线）。
