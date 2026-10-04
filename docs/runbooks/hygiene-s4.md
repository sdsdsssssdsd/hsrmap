# Runbook · S4 工作流产品化（a1-8 七）

**状态：完成（2026-10-04）**。以前这些事都靠 `%TEMP%\rNN*.py`：391 个一次性脚本里，
15 个在发布条目（图解转录）、29 个在写检索账本、17 个在打快照/交付包。现在它们都有正式入口。

## 正式入口

| 以前 | 现在 |
| --- | --- |
| 手拼 `[图解法转录 sha]` + 自己调 `create_item/approve_item` | `hsrmap guides evidence-transcribe --spec payload.json [--apply]` |
| 自己 INSERT `source_search_run/result` | `hsrmap guides search-record --json payload.json [--dry-run]` |
| `snapshot_diff → assert → sync` 手写一遍 | `hsrmap guides publish-snapshot [--dry-run / --in-place]`（唯一发布入口） |
| 手工 copy + 打 zip | `hsrmap release [--dry-run]` |
| 手工查仓库脏不脏 | `hsrmap repo-hygiene [--update-baseline]` |

## A. 检索账本：`search-record`

```json
{
  "schema_version": 1,
  "topic": "golden_scapegoat",
  "target_key": "point:3556",
  "query": "神悟树庭 两个空间 黄金替罪羊",
  "provider": "web_search",
  "status": "OK",
  "reason": "本轮真实检索",
  "results": [
    {"url": "https://www.miyoushe.com/sr/article/62292428", "decision": "ACCEPTED", "score": 60, "reason": "两条走法逐字"}
  ]
}
```

* **校验**：`schema_version`、`topic`/`query` 必填、`results` 至少一条、每条要有 `url` 与合法 `decision`
  （`ACCEPTED`/`DUPLICATE`/`MIRROR`/`IRRELEVANT`/`JS_ONLY`/`BLOCKED`/`ALREADY_IMPORTED`）；
* **幂等**：同 `topic/target_key/query/provider` 且候选与决策完全一致时返回既有 run（`"skipped": true`），
  不会重复制造逻辑相同的账本；
* **dry-run**：只回显将要写入什么，不落库；
* `verdict`（`NO_PUBLIC_SOURCE_FOUND` 之类）仍走审阅过的 `guides no-public-source --apply`，本命令不越权。

## B. 图解转录发布：`evidence-transcribe`

```json
{
  "schema_version": 1,
  "topic_key": "golden_scapegoat",
  "target_key": "set:3556-3622:topic:golden_scapegoat",
  "map_name": "翁法罗斯 / 特殊房间",
  "page": {"url": "https://www.miyoushe.com/sr/article/62292428"},
  "steps": [
    {"text": "[图解法转录 435467c5] 特殊区域2：面板第一排是右左左左右左。", "assets": ["435467c5…"]}
  ],
  "summary": "给玩家看的边界说明（可选）"
}
```

* **来源页必须先导入**（`hsrmap guides import-page`），否则报「来源页还没导入」；
* 每一步写着「从图 X 转录」，就必须在这一步挂上 X，且 X 必须真的在磁盘资产库里——
  这正是 `audit.py` 的 `IMAGE_TRANSCRIBED_STEP` 规则，工作流把它变成**写入前**的校验；
* 发布仍然走 `create_item → approve_item`（同一条评审、审计与门禁通道），不绕过任何 gate；
* 默认 **dry-run**，只有 `--apply` 才落库。

## C. 快照发布

`publish-snapshot` 已经是唯一入口（`snapshot_diff → assert_publishable → sync_published`，
报告写 `data/guides/reports/snapshot-diff.md`），S4 没有再造第四套流程；
audit + offline E2E 在 `closure-check` 与 `publish-snapshot` 里各跑一次（同一个实现）。

## 临时脚本退役

391 个 `%TEMP%\rNN*.py` 已归档到 `artifacts/legacy/temp-scripts/`（运行态目录，gitignored），
按用途统计：**发布/转录 15、账本/检索 29、快照/发布包 17、探针/检查 325、其它 5**。
归档而不是直接删，是为了留一份「当时怎么算的」的历史；但它们**不再承担任何当前生产流程**——
上面三条命令 + `release` + `repo-hygiene` 覆盖了全部日常动作。

## 证据

* `tests/test_workflows.py`（13 条）：账本写入/幂等/新决策/非法载荷（5 种）/dry-run；
  转录缺来源页、缺磁盘资产、dry-run、apply 走评审通道（注入 stub 断言 draft 形状与调用）；
  两条 CLI 端到端（`search-record` 往返、`evidence-transcribe` 默认 dry-run 且非法载荷 rc=1）。
* `pytest -q` = **588 passed / 77 skipped**；`ruff check` = All checks passed。
