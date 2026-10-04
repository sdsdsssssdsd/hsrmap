# P4 Snapshot Manifest / Typed Diff / Three-tier Gate

依据 a1-6 §15–19。全部实现在 `hsrmap/guides/publishing/diff.py`，并接进 `guides publish-snapshot`。

## Snapshot Manifest（§16，schema v3）

`snapshot_manifest(db)` 产出 `data/guides/reports/snapshot-manifest.json`：

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 3 |
| `topics` | 每个 topic 的 covered / targets / basis |
| `entries` | 每个 guide 的 target、content_sha256、step_count、assets |
| `assets` | 快照引用的全部 asset SHA256（排序去重） |
| `bindings` | guide_id → binding target |

现网实测：183 entries / 133 assets / 183 bindings / 15 topics（origami_bird 43/45、nymph 39/68、floating_grease 37/48）。
`manifest_digest()` 给出不含时间戳的稳定摘要，可直接比较两个快照。

## Typed Diff（§17）

12 类变更：`TARGET_ADDED/REMOVED`、`GUIDE_ADDED/REMOVED/CHANGED`、`BINDING_ADDED/REMOVED/CHANGED`、`ASSET_ADDED/REMOVED`、`COVERAGE_INCREASED/DECREASED`。
双报告：`snapshot-diff.json` + `snapshot-diff.md`（人机各读一份）。

## Three-tier Gate（§18）

| 档位 | 内容 | 默认行为 |
| --- | --- | --- |
| HARD FAIL | broken binding / missing asset / schema invalid / 无 waiver 的 published target 移除 | 阻断（`--force` 也不能越过 binding/asset/schema） |
| COVERAGE FAIL | COVERAGE_DECREASED、guide/target 移除 | 默认阻断；`--allow-coverage-drop` 放行非 Gate 保护 Topic；`--force` 全放行 |
| REVIEW REQUIRED | GUIDE_CHANGED / BINDING_CHANGED / ASSET_ADDED / COVERAGE_INCREASED | 只报告，不阻断（result=REVIEW） |

## Waiver（§19）

`data/guides/allowed_regressions.yaml`（`--waivers <path>` 可覆盖）：

```yaml
allowed_regressions:
  - topic: floating_grease
    target: '5169'
    reason: incorrect binding
    issue: ATLAS-1
```

- 命中 waiver 的 target 移除 ⇒ 进 `gate.waived`，不再计入 coverage_fail；
- 若某 Topic 的 coverage 下降完全由已豁免的移除解释，则该 coverage 变更一并豁免；
- 没有 waiver（且未显式 `--allow-coverage-drop/--force`）⇒ FAIL。

## 现网验证（dry-run）

```text
gate.result = PASS
change_counts = 全 0（183 篇已同步，无漂移）
manifest / snapshot-diff.json / snapshot-diff.md 均已落盘
```

测试：`tests/test_guide_snapshot_manifest.py`（12 条）+ 既有 `test_guide_snapshot_diff.py`（12 条）全绿。
