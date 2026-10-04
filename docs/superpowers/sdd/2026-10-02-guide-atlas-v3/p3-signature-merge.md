# P3 Review Unit Signature + Evidence Merge

依据 a1-6 §10–14。签名由代码确定性计算（绝不由模型生成）。

## 三层身份

| 层 | 内容 | 实现 |
| --- | --- | --- |
| L1 Target | `topic \| target_key` | `target_identity()` |
| L2 Source | `canonical article family \| normalized heading` | `article_family()` / `source_identity()` |
| L3 Unit | `sha256(topic + target_key + normalized instruction + asset shas)` | `unit_signature()` |

## Canonicalization 规则（实测）

```text
https://ol.3dmgame.com/gl/319072.html        -> 3dmgame.com/319072
https://m.3dmgame.com/ol/gl/319072.html      -> 3dmgame.com/319072
https://app.3dmgame.com/gl/319072.html       -> 3dmgame.com/319072
https://shouyou.3dmgame.com/gl/554586.html   -> 3dmgame.com/554586
https://a.9game.cn/bhxqtd/11837470.html      -> 9game.cn/11837470
https://www.gamersky.com/handbook/202304/1592527_3.shtml -> gamersky.com/1592527
```

文本规范化：NFKC + casefold + 去空白/标点（`第一步：转！` == `第一步:转!`）；asset 取排序去重的 sha 集合。

## 合并规则

- 同签名 → 保留 1 个审核对象，其余标记 `MERGED`（`reason = merged_into:<keeper>`），**不删除**；
- keeper 选择：`AUTO_SUGGEST > MATCHED > NEEDS_REVIEW`，同级取最早 id —— 建议永远不会被降级；
- keeper 的 `evidence_json.merged_sources` 记录全部来源（item/page/url/status），实现「1 个审核对象 + N 份 evidence」；
- `APPROVED / PUBLISHED / REJECTED` 永不参与合并。

## 实测（现网 working DB）

命令：`guides signature-merge --apply`

| 指标 | 值 |
| --- | ---: |
| review_items_before | 524 |
| review_items_after | **310** |
| merged_items | 214 |
| duplicate_groups | 93 |
| duplicate_ratio | **0.4084 → 0.0** |
| 幂等复跑 | merged_items = 0 |

状态分布变化：`NEEDS_REVIEW 394 → 242`、`AUTO_SUGGEST 130 → 68`、`MERGED 214`（新增审计态）。
published.db 未受影响（183 篇 / 3414 步）。
