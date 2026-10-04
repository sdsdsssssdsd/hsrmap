# Phase 5C — 地区 Vision 匹配架构

日期：2026-10-02  
状态：设计稿，供规划。未实现。  
范围：在冻结的 G0–G4 之上，把攻略从「版本标题」改成「图内地区」再绑官方点。  
相关：`a1-3.md`（总规）、`docs/superpowers/plans/2026-10-02-phase-5-guide-overlay.md`、`docs/superpowers/sdd/2026-10-02-phase-5b-real-corpus/progress.md`

## 1. 目的

一篇真实浮脂攻略通常**按地图地区展开**，不是按游戏版本展开。标题写「4.2版本」，正文可能只提「海原市 3 个 / 海原电视塔 3 个」，每一处的地区名写在截图 HUD 上。

成功标准：

- 审核台能按**官方地图名**分组，不再把整篇当成一个 `pending` 点。
- 广告、封面、自拍、二维码不进入匹配。
- 匹配器只在「该地图 + 浮脂溯源」的官方候选里打分，不在全库乱猜。
- 只有人点 Approve 才写入 published guide。Real Guide Coverage 在未 Approve 前保持 0。
- 测试不连网、不调真实 Vision。密钥只来自环境或 gitignored `.env`，不进 `guide.db` / manifest / 日志 / 已提交配置。

## 2. 约束（不可改）

- G0–G4 冻结：`guide.db` V2、raw store、adapters、DocumentBlocks 接口不重写。
- 官方 `core.db` / `detail.db` / `data/assets/sha256` 只读。
- Viewer 不爬网。`connect-src 'self'`。攻略图只走本地 `/guide-assets/{sha}`。
- 不自动 Approve。`AUTO_SUGGEST` 不是 published。
- 不因 matcher 48/48 就发布 48 个点。Point Match Coverage ≠ Real Guide Coverage。
- 不转发第三方正文/原图。submit 包不含 `data/`、不含 `.env`。
- 限速、遵守 robots。不绕过验证码 / 代理 / 登录墙。
- 模型禁止脑补步骤。图上读不到的地区名必须是 `null`，不能从标题「4.2」推出来。

## 3. 现状（Phase 5B 诚实数）

| 项 | 值 |
|---|---|
| 真实页 raw | 5/5 |
| 导入 QA PASS | 2/5（17173、GamerSky） |
| DeepSeek 文本抽取（页 1） | JSON 合法，幻觉步骤 0，2 个 section，steps 空 |
| Golden 20 | 20/20 |
| Real Golden 10 | 空 |
| Point Match Coverage | 48/48（文本对上官方点名，不是已发布攻略） |
| Matcher 人工正确数 | 0 |
| Real Guide Coverage | 0/48 |
| CV vs 海原市 raster | 8 张，全 `NO_MATCH` / `weak_match`，0 张错绑 |
| Review 队列 | 25 条 `AUTO_SUGGEST`，`source_point_id=pending` |
| 审核台本地图 | 页 1 有 14 张本地图，可打开 |

当前失败点：

1. `match_sections` 用 heading/正文对官方 `map_name`。真实 heading 是版本名，对不上「海原市」。
2. `ingest._candidate_points` 把 `source_point_id` 写成 `pending`，不查官方 48 点。
3. `classify_image` / `DeepSeekGuideLLMProvider.classify_image` 只送文字 hint，**不送图片字节**。
4. 游戏内截图 ≠ 官方平面 raster，ORB 对 海原市 图对不上是预期，不能靠它读地区名。
5. 页 1 的 14 张图里混有封面、广告、自拍、二维码。`page_images` 原样列出，没有角色。

页 1 文本里**已经出现**地区名（「海原市地图共有 3 个」「海原电视塔地图共有 3 个」），但单张图的 `alt` 全是文章标题，无法按图切开。地区标签在像素里。

## 4. 决策

已定：

- 匹配主轴是**地区（官方 map_name）**，不是版本号。
- 走两段 Vision：先便宜分类，再只对有用的图做高精度读字。
- 管线先产出地区，再在该地区的官方浮脂点里建议 `source_point_id`。人 Approve 才落库。
- 废图（cover / advertisement / unrelated）不建 section、不送高精度、不进 CV。
- OpenCV 只作为**同地图 raster 上的辅助证据**，不能单独发布。

规划时可再选粒度，架构两种都支持：

- **层 A：** 只保证每张有用图绑到 `海原市` / `海原电视塔` 等官方地图。
- **层 B：** 再读「第 N 处」或位置描述，在该地图的 2–4 个官方浮脂点里给建议。

默认实现顺序：先稳住层 A，层 B 用同一套 image record，不另开平行管线。

## 5. 目标数据流

```text
DocumentBlocks（已冻结）
        │
        ├─ 文本：文章级 maps[] / claimed_point_count（可选，只作先验）
        │
        └─ 每张有 asset SHA 的图
                │
                ▼
        cheap vision classify
        role = location_map | puzzle_step | route_map
             | reward | cover | advertisement | unrelated
                │
                ├─ 废图 → 停
                │
                └─ 有用图
                        │
                        ▼
                high-detail vision（只读图上存在的字）
                map_name / ordinal / overlay_text / instruction_text
                        │
                        ▼
                官方 map_name 归一
                （「海原市」「海原电视塔」∈ core maps；对不上 → null）
                        │
                        ▼
                按 map_name 切 section
                        │
                        ▼
                Candidate：该 map + 浮脂溯源 label
                （例：海原市 → 3 个官方点，不是 5330 个）
                        │
                        ├─ 文本 / ordinal / overlay
                        ├─ 可选 OpenCV vs 该地图 raster
                        └─ 可选 LLM assist（只能选 candidate 列表内的 id）
                        │
                        ▼
                review_item
                status = AUTO_SUGGEST | NEEDS_REVIEW
                source_point_id = 官方 id 或空
                不写 published
```

版本号只进 `article.game_version`，**不参与** point 打分。

## 6. 组件

G0–G4 不动。新增或补全这些单元，现有文件只加接口、不改旧契约。

### 6.1 ImageClassifier（便宜）

- 文件：扩展 `hsrmap/guides/llm/vision.py` 与 `DeepSeekGuideLLMProvider.classify_image`。
- 输入：本地 asset 路径或字节 + 可选低分辨率。必须把图以 `image_url` / data URL 送给 Vision，禁止只送 hint。
- 输出：

```json
{
  "block_id": "b8",
  "sha256": "...",
  "role": "location_map",
  "contains_map": true,
  "contains_instruction_text": false,
  "confidence": 0.9
}
```

- Fake provider：按 fixture 文件名/hint 返回固定 role，测试零网络。
- 无密钥 / 超时 / 非 JSON：role=`unknown`，不抛到 ingest 外层。

### 6.2 ImageRegionReader（高精度，仅有用图）

- 输入：原图像素 + 官方地图名白名单（从 core 只读查出，例如当前快照含「海原市」「海原电视塔」等）。
- 输出：

```json
{
  "block_id": "b8",
  "sha256": "...",
  "map_name": "海原市",
  "map_name_raw": "海原市",
  "ordinal": 2,
  "overlay_text": ["海原市"],
  "instruction_text": [],
  "grounded": true
}
```

- `map_name` 必须等于白名单一项，否则 `null`。禁止用标题「4.2」或作者名填。
- `ordinal` 图上没有数字就 `null`。
- `grounded=true` 仅当 `map_name_raw` 是模型声称在图上看到的子串。测试用 fixture JSON，不读真实 PNG。

### 6.3 RegionSectionBuilder

- 输入：文本抽取的 sections（可空）+ 每图的 region record。
- 输出：按归一化 `map_name` 分组的 sections。同一地区的 location_map / puzzle_step 进同一 section。
- 一篇文章可以对应多个地区 section。版本标题单独丢进 article，不单开 section。
- 没有读出任何地区时：section 的 `map_name=null`，review 标 `NEEDS_REVIEW`，不编造 pending 点。

### 6.4 OfficialCandidateQuery（只读 core）

- 输入：`map_name` + 语义标签「浮脂溯源」（既有 grease topic）。
- 输出：该地图上的官方点列表：`source_point_id` / `map_id` / `map_name` / `x` / `y`。
- 海原市预期 3 个，海原电视塔预期 3 个。0 个候选 → 停在地区层，不猜。
- 禁止把 LLM 生成的 id 塞进候选。

### 6.5 PointMatcher（改输入，不改发布规则）

- 现文件 `hsrmap/guides/matching/matcher.py` 继续无 LLM。
- 打分对象从「heading vs 48 点」改为「section.map_name + ordinal + overlay vs 该地图候选」。
- 证据字段沿用 a1-3：`map` / `semantic_label` / `ordinal` / `location_text` / `cv_map_match` / `official_image_similarity` / `llm_assist`。
- 规则：
  - `map`：归一名命中官方 map_name 才 ≥ 0.85。
  - `ordinal`：图上「第 2」且该地图恰好 3 个点时，只作为排序提示，**不足以**单独 AUTO_SUGGEST 到具体 id。
  - `cv_map_match`：仅当 `match_location_map` 返回 `MATCH` 且候选在本地图列表内。`NO_MATCH` / `AMBIGUOUS` 记 0，不改 map_name。
  - `llm_assist`：只能返回候选 id 或 null。
- `source_point_id`：置信且唯一则填官方 id；否则空字符串，**禁止**再写 `pending`。
- ingest 状态：高置信 → `AUTO_SUGGEST`；其余 → `NEEDS_REVIEW`。永不 `APPROVED`。

### 6.6 Review 展示

- 左栏按 `map_name` 分组，不再按版本标题堆 25 条重复行。
- 中栏：`source_point_id` 只显示官方 id 或空白，提供该地图候选下拉。
- 右栏：本地区有用图；废图折进「已排除」不挡核验。
- 缩略图继续 `/guide-assets/{sha}`。`draft.images` 写 block_id + sha，不再长期空数组。
- Approve 仍要人改到真实官方 id 后才能过。空 id 禁止 Approve。

## 7. 存储

不改 G0 表结构也能跑。derived 增两类 JSON（page_id 为键）：

- `data/guides/derived/image_roles/<page_id>.json`
- `data/guides/derived/image_regions/<page_id>.json`

raw HTML / extracted blocks / asset SHA 仍是唯一真相。Vision 结果可重跑覆盖 derived，不改 raw。

密钥：`DEEPSEEK_API_KEY` 仅环境或 `.env`。derived 与 review JSON 只存 role / map_name / sha，不存 prompt 全文、不存 key。

## 8. Viewer / 发布

- 未 Approve：地图 overlay 不出现这些攻略。
- Approve：只写该 `source_point_id` 的 `guide_entry`。
- 10 点 canary snapshot 仍等人审满 10 个不同官方点后再做。剩余 38 继续 BLOCKED。
- CSP 不变。`/review.js` 继续外置。

## 9. 错误处理

| 情况 | 行为 |
|---|---|
| 无 Vision 密钥 | 分类/读地区跳过；文本里出现的官方 map_name 仍可用于层 A；图角色全 `unknown` |
| Vision 超时 / 非 JSON | 该图 `unknown`，其它图继续 |
| 图文件缺失 / QA_FAIL | 不送 Vision；页 3–5 保持现在的 QA_FAIL |
| 读到的字不在地图白名单 | `map_name=null` |
| 一图像两个地区 | `AMBIGUOUS`，进 NEEDS_REVIEW |
| CV NO_MATCH | 不降级已读出的 map_name |
| 候选 0 或 ≥2 且同分 | 不填 source_point_id |

## 10. 测试

全部用 fixture + Fake provider。禁止测试访问 17173 / DeepSeek。

最少用例：

1. 分类：location_map / advertisement / unrelated 各一张 hint fixture → 正确 role。
2. 读地区：假图记录声明 overlay「海原市」→ `map_name=海原市`；声明「4.2版本」→ `map_name=null`。
3. 白名单：读到「二相乐园」且 core 无此 map → null。
4. Section：3 张海原市 + 2 张海原电视塔 + 1 张广告 → 2 个 section，广告不进。
5. Candidate：海原市 → 只返回该地图浮脂点（fixture 3 个 id）。
6. Matcher：heading 为「4.2版本」且 region 为海原市 → 不再产出 `pending`；无 region 时 `source_point_id=""`。
7. 发布：AUTO_SUGGEST 不写 `guide_entry`；Approve 且 id 为空被拒绝。
8. 无密钥：ingest 不崩溃，review 仍可列出本地图。

现有 Golden 20、coverage 分项、CSP / `/review.js` 测试保持绿。

## 11. 已有文件 vs 要补的

已有、规划时应当复用：

- `hsrmap/guides/extract/blocks.py` — DocumentBlocks
- `hsrmap/guides/llm/{provider,fake,deepseek,config,vision}.py` — 接口在，Vision 未送图
- `hsrmap/guides/prompts/image_classifier_v1.txt`
- `hsrmap/guides/matching/{matcher,cv}.py`
- `hsrmap/guides/ingest.py` — 需改候选来源，不改「只 Suggest」
- `hsrmap/guides/review/{service,console.html,console.js}` — 已有 `page_images`
- `hsrmap/viewer_app.py` — `/review` `/review.js` `/guide-assets/{sha}`
- 官方只读：`viewer_repo` / core maps / grease topic

要补（规划时拆任务，本文件不写实现步骤）：

- 真实多模态请求（image bytes → Vision）
- 官方地图白名单查询
- RegionSectionBuilder
- OfficialCandidateQuery
- matcher 改输入
- review 按地区分组 + 废图折叠
- Fake Vision fixtures
- 上表测试

## 12. 明确不做

- 不重爬、不改 G0–G4 adapter。
- 不把 QA_FAIL 的 3DM/TapTap 页假装成可核验。
- 不把 CV 失败改成最近邻强绑。
- 不在 submit / git 里放 `.env`、真实攻略 HTML、guide-assets 原图。
- 不在本阶段做全站 Vision 或非浮脂标签。
- 不把 Point Match 48/48 当成可发布。

## 13. 给规划用的验收句子

规划完成后，实现方应能对照这些句子验收，无需再猜意图：

1. 打开 `/review` 点 17173 页，左栏看到「海原市」「海原电视塔」，看不到「4.2版本」当成一个点。
2. 右栏默认是地图/解密图；自拍和二维码不在主核验条。
3. 中栏下拉只有该地图的官方浮脂 id。
4. 不填官方 id 时 Approve 按钮无效。
5. 拔掉 `DEEPSEEK_API_KEY` 再 ingest，进程不挂，只是图没有 role。
6. `pytest tests/test_guide_*.py` 全绿且无网络。
7. Real Guide Coverage 在无人 Approve 时仍是 0/48。
