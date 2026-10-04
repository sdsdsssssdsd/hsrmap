# 爬虫收口：Host Scheduler / Circuit Breaker / Robots Cache / URL Admission

Spec: `a1-6.md` §十四（Rate Limit → Host Scheduler）/ §十五（Circuit Breaker）/ §二十一（Robots 缓存）/ §二十二（安全边界）。

## 1. Host Scheduler（§十四）

`hsrmap/guides/crawler/hosts.py::HostScheduler`——每个 host 一份状态：

```text
last_request_at / min_interval / backoff_until / consecutive_errors / suppressed
```

- 同一 host 仍然 ≥ 1s，但**不同 host 互不等待**（不再全局 sleep 1 秒）；
- `before(url)` 返回 False 表示断路器打开：请求直接不发；`after(url, ok=…, blocked=…)` 记结果。

```python
scheduler.before("https://a.test/1")   # True，记录 last_request_at
scheduler.before("https://b.test/1")   # True，另一个 host 不用等
scheduler.before("https://a.test/2")   # 等满 min_interval 再放行
```

## 2. Circuit Breaker（§十五）

连续失败达到 `error_threshold`（默认 10，`guides corpus` 用 1s 间隔 + 10 次）就暂停该 host：

- 403/封锁类 → `HOST_BLOCKED`，超时/其它 → `HOST_DEGRADED`；`backoff_until = now + cooldown`（默认 30 min）；
- 冷却期内的请求全部计入 `suppressed` 并不发出；冷却结束后自动半开（清零计数再试）。
- `guides corpus` 的 `crawl-report.json` 现在带 `scheduler` 段：每个 host 的 status/reason/requests/suppressed/waits，
  正是文档里那句 "3dm asset host / 17 consecutive 403 / circuit opened / 83 requests suppressed" 的可读形式。

`AssetFetcher(scheduler=…)` 同样接入：图片 host 连续 403 时不再继续打 200 个 URL。

## 3. Robots Cache（§二十一）

`RobotsCache(fetch=…, ttl=3600)`：按 host 缓存 `robots.txt`（`fetched_at` / `expires_at` / 内容），
同一个 job 内不重复请求；`allowed(url)` 走既有的 `path_allowed`。

**Robots 拒绝有了正式状态**：`ROBOTS_DENIED`（不再是 `NEEDS_MANUAL_IMPORT + reason=robots`）。
它进 crawl-report 的 status 统计，证据账本记为 `BLOCKED`（reason 写明 robots），不会与抓取失败混在一起。

## 4. URL Admission（§二十二）

`hsrmap/guides/crawler/guard.py`：

```python
admit_url(url)                  # 只放行 http(s) 公网 URL，否则 URLRejected
admit_redirect(original, next)  # 重定向目标必须重新验证（文档明确要求）
```

拒绝：非 http(s)（`file:`/`ftp:`/`data:`/`javascript:`）、URL 内嵌账号密码、`localhost`/`*.local`/`*.internal`、
`metadata.google.internal`、以及所有非公网地址段（`0/8`、`10/8`、`100.64/10`、`127/8`、`169.254/16`、`172.16/12`、
`192.0.0/24`、`192.168/16`、`198.18/15`、`::1`、`fc00::/7`、`fe80::/10`）。

DNS 解析是**显式选项**（`resolve=True`，测试用注入的 resolver），因为"解析到内网"同样是 SSRF（DNS rebinding）。
`guides corpus` 在抓取前调用 `admit_url`，被拒的 URL 记为 `URL_REJECTED` 并进证据账本。

## 5. 测试

- `tests/test_guide_crawler_guard.py`（6）：公网放行、scheme 拒绝、本机/内网/metadata 拒绝、重定向复检、
  DNS 解析选项、地址段清单。
- `tests/test_guide_host_scheduler.py`（7）：分 host 独立节流、连续失败开断路器 + 抑制计数 + 冷却后半开、
  成功清零、`HOST_DEGRADED` 与 `HOST_BLOCKED` 区分、robots 缓存 TTL、无 robots 时全放行、
  `AssetFetcher` 在断路器打开时不再发请求、连续 403 打开 asset host 断路器。
