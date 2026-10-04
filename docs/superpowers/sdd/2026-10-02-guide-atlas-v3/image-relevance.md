# Image Relevance Filter（a1-6 §十）

攻略页 55 张图不代表 55 张有用。在**任何模型调用之前**先用规则过滤（`hsrmap/guides/assets/relevance.py`）：

```text
min width 200 / min height 150 / aspect ratio 0.2–5.0
URL pattern：logo / icon / avatar / sprite / banner / qrcode / loading / blank / placeholder / spacer / /ads/ …
alt 广告 / 相关推荐
DOM 区域：header / footer / sidebar / nav / menu / breadcrumb / related / recommend
duplicate SHA256     -> 完全重复
duplicate pHash ≤ 6  -> 视觉重复（重新压缩、缩放、加水印、换站转载）
```

`judge_image()` 返回 `{keep, score, reasons}`（每条拒绝都写明理由），`filter_images()` 在一页内做两层去重。
尺寸规则在拿不到宽高时自动跳过（不猜），URL/alt/DOM/重复规则始终生效。

接入点：`ingest.observe_images()` 在分类角色之前先判相关性 —— 家具图、广告图、重复图**不会**产生 vision 调用，
观察记录里带 `relevance` 字段，页面级结果写进 derived 的 `image-roles.json`（含 `dropped` 明细），可审计。

```bash
python -m hsrmap guides image-filter --page 140
# {"images": 15, "kept": 15, "dropped": 0, "reasons": []}
```

实测（page 140 筑梦边境折纸小鸟）：15 张图全部保留 —— 规则没有误杀真实攻略截图。

测试：`tests/test_guide_image_relevance.py`（4）—— 尺寸/长宽比、URL/alt/DOM 命中、SHA 与 pHash 重复、保留顺序与理由。
