# Runbook · 把发布包传上 GitHub（去隐私化）

## 位置

| 东西 | 路径 |
| --- | --- |
| 发布包（每次 `hsrmap release` 重建） | `<repo>/submit/` + `<repo>/submit.zip` |
| 发布用的 Git 暂存仓库（**在项目之外**，别放进源码树那层） | `<publish-workspace>\hsrmap` |
| GitHub 仓库 | https://github.com/sdsdsssssdsd/hsrmap |

## 每次发布的步骤

```powershell
# 1) 重建发布包（自带 allowlist，data/ .env submit/ web/dist 的取舍都在里面）
python -m hsrmap release

# 2) 隐私扫描：把绝对路径、用户名、key、cookie 之类挡在包外
python tools/privacy_scan.py submit          # 退出码 0 = 没有未复核命中（2 = 必须处理）

# 3) 同步到暂存仓库（/MIR 镜像，但别删 .git）
robocopy submit <publish-workspace>\hsrmap /MIR /XD .git

# 4) 提交并推送
cd <publish-workspace>\hsrmap
git add -A
git commit -m "release: <一句话>"
git push
```

## 两个坑（都踩过）

* **代理**：这台机器上 github.com:443 直连会被 reset。系统代理在
  `HKCU\...\Internet Settings` 里（当前 `127.0.0.1:7892`），但 **git 不读 WinINET 设置**，
  必须显式告诉它。暂存仓库已经配好：
  ```powershell
  git config http.proxy http://127.0.0.1:7892
  git config https.proxy http://127.0.0.1:7892
  ```
  代理没开时还可以走 API 上传（`api.github.com` 是通的），但那要 550 次 blob 请求，很慢。
* **别把暂存仓库放在源码树旁边**：放在项目同级会让 `repo-hygiene` / `dod` 把它当成仓库的一部分
  （第一次就是这么误报的）。放 `<publish-workspace>\hsrmap` 这种项目之外的位置。

## 去隐私化清单（打包前应该全绿）

* `.env` 不进包（只有 `.env.example` 键名清单）；
* `data/`、`*.db`、`submit/`、`artifacts/`、构建缓存不进包（release 的 forbidden 规则负责）；
* 构建机绝对路径不落进包里：`release-manifest.json` 只写 `root_name`，
  `framework/architecture.json` 同理（生成器里改的，重跑不会回来）；
* 文档里不写用户名（`C:\Users\<user>` / `Temp\pytest-of-<name>`）。

## CI

`.github/workflows/ci.yml` 四步：`pytest -q` → `ruff check` → `repo-hygiene` → `dod`。
**依赖清单必须完整**：`numpy` 是 `hsrmap/guides/assets/phash.py` 的模块级 import，
第一次 CI 就是因为它不在 `requirements*.txt` 里而 10 个测试模块 collection error。
opencv / playwright 都在函数内 try/except，属于可选能力，放在 `requirements-vision.txt` 与
`pyproject` 的 extras 里。
