# Crawler Sprint 2 — Identity

Spec: `a1-6.md` §三（URL Canonicalization）/ §四（Article Family Resolver）/ §二十四 Sprint 2。
退出条件：**同一文章 / 同一图片不会因为分页、移动版、转载形态反复污染 Corpus。**

## 1. URL 身份（`hsrmap/guides/crawler/identity.py`）

```python
ref = ArticleFamilyResolver().resolve(url)
# ArticleRef(family="gamersky.com/1729233", host="gamersky.com", raw_host="m.gamersky.com",
#            site="gamersky.com", article_id="1729233", page_index=2,
#            canonical_url="gamersky.com/handbook/202404/1729233.shtml")
```

| 方法 | 作用 |
| --- | --- |
| `resolve(url)` | 解析成 `ArticleRef`（站点、文章号、页码、family） |
| `same_article(a, b)` | 镜像主机 / 分页 / 追踪参数都不影响判定 |
| `group(urls)` / `duplicates(urls)` | 按 family 分组；只返回被重复给出的文章 |
| `unique(urls)` | 每篇文章留一个 URL，优先第一页 |
| `mirror_of_known(url, known)` | 该 URL 是否是已知文章的镜像视图 |
| `declared_canonical(html)` | 页面自述的 canonical（`<link rel=canonical>` → `og:url`） |
| `reprint_of(url, html)` | 自述 canonical 属于**别的站点**时判定为转载 |

实现要点：

- 身份规则只有一份（`guides/signature.py`）：`article_family` / `canonical_url` / `registrable_domain` / `query_signature`。
  爬虫、审计、Review 合并共用，不可能漂移。
- 镜像：`m.` / `app.` / `a.` / `mip.` / `3g.` / `mob.` / `wap.` 前缀 + `KNOWN_MIRROR_HOSTS` 显式表，
  站点适配器还可以用 `ArticleFamilyResolver(mirrors={...})` 追加。
- 分页：`_2.shtml` 与 `?page=2` 都收敛到同一 family，`page_index` 单独记录；`unique()` 优先第一页。
- 镜像判定必须看**原始 host**：`canonical_host()` 会把 `m.gamersky.com` 归一成 `gamersky.com`，
  拿归一化后的 host 去比较永远发现不了镜像（`ArticleRef.raw_host` 就是为此保留的）。

### Corpus 侧行为

`guides corpus` / `import_real_urls()` 现在按 **family + 原始 host** 跳过镜像视图：

```json
{"url": "https://m.17173.com/content/05112024/152401625.shtml", "status": "SKIPPED_MIRROR",
 "of": "https://news.17173.com/content/05112024/152401625.shtml"}
```

同一次运行里先抓到桌面版，后面出现的移动版就不会再抓一遍；跨运行则与 `guide_page` 已存 URL 比对。
分页续页仍然按 URL 抓（否则 family 语料会缺页，接地审计需要它）。

## 2. 图片身份（`hsrmap/guides/assets/phash.py`）

- 算法：灰度 → 32×32 → 二维 DCT（自己实现的正交 DCT-II 矩阵，不引入 scipy）→ 取左上 8×8、去掉 DC → 63 bit 中位数哈希。
- 阈值 `SIMILARITY_THRESHOLD = 6`；`hamming()` / `similar()` / `duplicate_groups()`（并查集聚类）。
- 精确去重仍是 SHA256；pHash 只补「重新编码 / 缩放 / 换站转载」这一类漏网。

接入点：

- `AssetCache.store()` 落库时计算并写入 `guide_asset_cache.phash`（新增列 + 迁移）。
- `AssetCache.phash_of(sha)` / `visual_duplicates(threshold)`：老缓存里的资产在首次报告时补算（有上限）。

### 实测（现网缓存）

| 指标 | 值 |
| --- | ---: |
| 去重后的缓存资产 | 1242 |
| 落在近重复组里的资产 | 801（314 组：2 元 218、3 元 49、5 元 26、4 元 19、6 元 2） |
| 随机 4000 对的 hamming | min 0、p1 20、median 32、p95 38、max 46 |
| 随机对 ≤6 bit | 7 / 4000（0.18%） |
| 组内对的期望占比 | 769 / 770 661 = 0.100% |

随机对里 ≤6 的比例与「真实重复对占比」同量级（期望 4 对、观测 7 对，泊松噪声内），
说明阈值 6 没有把不相干的截图大规模合并。抽样人工核对：`thumbnews/…/1711587802904.jpg`（196×118）
与 `raiders/…/1711587797538.jpg`（1920×1080）hamming = 0，是同一张匹诺康尼地图截图的不同尺寸 ✓。

## 3. CLI

```
python -m hsrmap guides identity --url <url> [--url <url> …]   # family / 镜像 / 页码 / unique
python -m hsrmap guides asset-dedup [--threshold N] [--limit N] # 近重复图片组（缺哈希的按需补算）
```

## 4. 与 ADR 的关系

- **ADR-003 ArticleFamilyResolver：PARTIAL → IMPLEMENTED**（本 Sprint 落地）。
  `guides closure-check` 的 Architecture decisions 现在输出 `article resolver ....... IMPLEMENTED ADR-003`，
  并在 JSON 里附 `identity` 证据段（镜像判定、页码、pHash 阈值）。
- 仍留在后续 Sprint 的：Reviewer 侧「同一张图选一个规范资产」（当前只报告分组，不自动改绑定）、
  fixture corpus / live smoke suite（Sprint 4）。
