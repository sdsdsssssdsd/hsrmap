# 历史：Phase 1 · HoYoLAB 互动地图抓取（2026-10 之前）

> 这份文档是**历史记录**。当时的 README 只讲「怎么把官方互动地图的数据抓下来」，
> 现在项目的主线已经变成 Guide Atlas（官方点位 → 攻略语料 → 完成度判定 → 证据账本 → 发布门禁），
> 抓取只是这条链路的第一步。原文保留如下，作为 phase1 的来源说明。

---

## 只抓《崩坏：星穹铁道》的 HoYoLAB 互动地图公开数据

这个版本**只针对崩铁**：

- 官方入口固定为
  `https://act.hoyolab.com/sr/app/interactive-map/index.html?lang=zh-cn`
- 不配置原神、绝区零等互动地图接口。
- 网络响应只有满足以下条件之一才会落盘：
  1. URL 明确包含 `srmap` / `sr_map`；
  2. JSON 内容具有明显的地图结构字段，例如 `map_id`、`label_id`、`x_pos`、`y_pos`、`slices` 等。
- 使用全新 Chromium 临时会话，不读取你的常用浏览器 Cookie。
- 不需要登录 HoYoLAB。

### 安装

```bash
pip install playwright
playwright install chromium
```

### 推荐抓法

```bash
python tools/hsr_hoyolab_map_dump.py --headed --download-images --out hsr_hoyolab_dump
```

打开浏览器以后，只在崩铁互动地图里操作：切星球/区域 → 打开「浮脂溯源」类别 → 逐个点开地图 →
点击具体标记（让详情数据也被请求）→ 回终端按 Enter。

### 得到什么

```text
hsr_hoyolab_dump/
├─ summary.json / api_requests.json / hsr_map_page.html
├─ maps.csv|jsonl、labels.csv|jsonl、points.csv|jsonl
├─ referenced_image_urls.txt
├─ images_manifest.json / referenced_images/     # --download-images 时
└─ raw_json/                                     # 官方页面实际请求到的 JSON
```

其中 `api_requests.json` 记录当前版本互动地图真正发出的请求（接口路径、`app_version`、
是否需要额外头、分页方式），因此不需要猜 2024 年的接口是否还有效。

### 为什么不直接写死 2024 年的接口

2024 年社区脚本用过 `https://sg-public-api-static.hoyolab.com/common/srmap/sr_map/v1/map/info`，
并从 HSR 前端 bundle 取 `app_version`。这证明数据可下载，但不证明 2026 年前端仍保持同样格式，
所以先读「当前页面自己的真实请求」，再据此生成全量下载器。

### 与现在的关系

* 抓取脚本仍在 `tools/hsr_hoyolab_map_dump.py`（默认不进交付包的运行态数据目录）；
* 抓下来的快照落在运行时目录（`HSRMAP_DATA_DIR` / 仓库 `data/` 兼容窗口）的 `snapshots/` 下，
  由 `python -m hsrmap sync` 消费；
* 参考数据（校准点位、golden 清单）留在 `phase1/calibration/`，是**不可变参考**，属于源码树；
* 生成物（raster 合成图、alignment 预览等，合计约 10 MB）已移出源码树，见 `artifacts/phase1/`。
