# 收官后项：统一 AI 缓存（ADR-005）+ Topic Admin 指标（§27）

Spec: `a1-6.md` §29（AI Cache）/ §27（Topic Admin 页放到后面）。

## 1. 统一 AI 缓存（`hsrmap/guides/ai_cache.py`）

```text
key = sha256(provider + model + prompt_version + normalized_input)
```

- **必须带 `prompt_version`**：否则改了 prompt 却命中旧答案，是最隐蔽的数据污染（§29 原文）。
- `normalized_input` 是规范化 JSON（键排序、bytes 换成 sha256），所以字典顺序不影响命中，图片键保持短小。
- 存储：`ai_cache` 表（key / provider / model / prompt_version / input_hash / output_json / hits / created_at / last_hit_at）
  + 可选磁盘镜像 `<root>/<key[:2]>/<key>.json` + 内存 L1。没有 DB 时磁盘仍然跨进程命中。
- `CachedProvider` / `wrap_provider()`：包住**任何** provider，逐方法缓存（`extract_sections` / `classify_article` / `read_region` …），
  不按 Topic 各建一套；`build_provider(..., db=…)` 直接返回带缓存的 provider。

```bash
python -m hsrmap guides ai-cache                                  # 条目数 / 命中数 / 按 prompt_version 分布
python -m hsrmap guides ai-cache --invalidate-prompt reader_v3    # prompt 改了，先清旧答案
```

`guides closure-check` 的 JSON 新增 `ai_cache` 证据段（条目、命中、键长、以及"改 prompt_version 键会变"的自检）。
**ADR-005：POST-CLOSURE → IMPLEMENTED**。

## 2. Topic Admin 指标（`hsrmap/guides/admin.py`）

§27 说 Admin 页"放到后面"，但要求先把底层指标定义稳定，并明确 `next_action` 必须是**推导**出来的。
现在六个 API 全部就位（前两个此前已有）：

```text
GET /api/v1/atlas/topics      每 topic 一行：official/published/matched/needs review/needs source/
                              no public source/rejected、QA 通过率、review 重复率、next_action
GET /api/v1/atlas/coverage    topic_ledger 全量（可 ?topic=）
GET /api/v1/atlas/review      队列分布 + 重复率 + 待审样本
GET /api/v1/atlas/sources     每 host 产出（source_yield）+ 搜索证据账本摘要
GET /api/v1/atlas/snapshots   最近一次 closure / publish-diff / manifest / audit / quarantine / ai-cache 报告
GET /api/v1/atlas/gates       （已有）
```

`derive_next_action(state)` 按 §27 的五条映射推导，优先级为：

```text
coverage_regression                     -> INVESTIGATE_REGRESSION
QA 失败占已检 >= 50%                     -> FIX_SOURCE_PIPELINE
缺源是最大的一桶（needs_source >= max(published, needs_review, approved)） -> DISCOVER_SOURCE
review 积压超过已批准                     -> REVIEW
approved/matched 多于已发布（有货没发）   -> PUBLISH
其余                                     -> HOLD
```

注意"缺源"的判据是**最大的一桶**而不是"存在缺源"：9/10 已发布、只差一个 target 的 topic 不是发现层的问题。
