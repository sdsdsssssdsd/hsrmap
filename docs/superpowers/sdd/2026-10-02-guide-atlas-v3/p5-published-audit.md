# Published Audit（a1-6 §33 第 5 步 / a1-5 硬指标）

命令：`guides published-audit --json data/guides/reports/published-audit.json`
审计对象：`published.db` 全部 **183 篇**，每篇对比其**自身来源页**（含 article family 的分页续页）。

## 判据

| 结论 | 含义 |
| --- | --- |
| `IMAGE_ONLY_STEP` | 无文字但有图 —— 图集型攻略的正常形态，**保留** |
| `EMPTY_STEP_NO_ASSET` | 无文字也无图 —— 纯噪声，可删 |
| `HALLUCINATED_STEP` | 文字在来源页正文与整页 HTML 中都找不到（含按标点切分后的逐段核对） |
| `CHROME_STEP` | 文字只存在于整页 HTML（侧栏/推荐位），不在正文块里 |
| `MISSING_ASSET` / `SOURCE_PAGE_MISSING` | 图不在盘 / 来源页不在工作库 |

序号标签（`海原市 第1处`）会先剥离序号再核对，因此不会被误判为幻觉。

## 实测结果（183 篇）

| 指标 | 值 |
| --- | ---: |
| guides_checked | 183 |
| guides_with_problems | 64 |
| EMPTY_STEP_NO_ASSET | **0** |
| IMAGE_ONLY_STEP | 10 |
| HALLUCINATED_STEP | **22** |
| CHROME_STEP | 242 |
| SOURCE_PAGE_MISSING / SOURCE_TEXT_MISSING | 0 / 0 |
| MISSING_ASSET | **0** |

## 结论（诚实记账）

1. **那 10 条空 step 不是损坏**：它们是「图集型」攻略的图片容器（每篇 1 步、1–2 张图），
   `fix_empty_steps` 只删「无文字且无图」的行 —— 本次为 **0 行**。
2. `MISSING_ASSET = 0`、`SOURCE_PAGE_MISSING = 0`：发布库的图与来源完好。
3. **`HALLUCINATED_STEP = 22`（14 篇）尚未归零** —— a1-5 的「Published hallucinated step = 0」
   **目前不成立**；这些 step 的文字无法在来源页正文/整页 HTML 中定位（集中在游民星空几篇）。
4. `CHROME_STEP = 242` 是另一类质量问题：侧栏/推荐位文字混进了步骤，属于 a1-6 §11/§20 的抽取器课题。

## 闸门语义（本轮同时落地）

`snapshot_diff` 会把审计纳入 gate：**新增/变更**的 guide 若含幻觉 step ⇒ HARD FAIL；
已存在的历史欠账只进入 `audit.pre_existing_hallucinations` 并在报告中列出，**不会冻结发布通路**
（否则修不了旧数据也发不出新数据）。
