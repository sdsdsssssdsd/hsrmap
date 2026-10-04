<#
.SYNOPSIS
  用 headless Chrome/Edge 给 docs/images 拍真实页面截图（README 里那几张就是这么来的）。

.DESCRIPTION
  先自己把服务起起来（start.bat / start_review.bat / monitor/start_monitor.bat），
  再用本脚本按 URL 截图。默认拍四张：地图总览、点位证据抽屉、审核台、监控台。
  点位的 URL 带 ?point=<core id>：前端启动时读 hash，会直接把抽屉打开。

.EXAMPLE
  pwsh -File tools/screenshot.ps1
  pwsh -File tools/screenshot.ps1 -MapUrl 'http://127.0.0.1:8766/#/map/158?point=1932' -Width 1920 -Height 1200
#>
param(
  [string]$MapUrl = 'http://127.0.0.1:8766/#/map/38',
  [string]$PointUrl = 'http://127.0.0.1:8766/#/map/158?point=1932',
  [string]$ReviewUrl = 'http://127.0.0.1:8767/review',
  [string]$MonitorUrl = 'http://127.0.0.1:8768/',
  [string]$OutDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'docs/images'),
  [int]$Width = 1680,
  [int]$Height = 1050,
  [switch]$Only
)

#: Chrome 会把进度写到 stderr，别让它被当成致命错误中断整个循环。
$ErrorActionPreference = 'Continue'

$candidates = @(
  'C:\Program Files\Google\Chrome\Application\chrome.exe',
  'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw 'no Chrome/Edge found; install one or pass a browser path' }

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$shots = @(
  @{ name = 'map-overview.png';   url = $MapUrl;     wait = 15000 },
  @{ name = 'point-evidence.png'; url = $PointUrl;   wait = 22000 },
  @{ name = 'review-console.png'; url = $ReviewUrl;  wait = 22000 },
  @{ name = 'monitor.png';        url = $MonitorUrl; wait = 12000 }
)
if ($Only) { $shots = $shots | Where-Object { $_.name -like ('*' + $Only + '*') } }

Write-Output ('browser: ' + $browser)
foreach ($shot in $shots) {
  $file = Join-Path $OutDir $shot.name
  if (Test-Path $file) { Remove-Item $file -Force }
  $args = @("--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--window-size=$Width,$Height", "--virtual-time-budget=$($shot.wait)",
            "--screenshot=$file", $shot.url)
  & $browser @args 2>$null | Out-Null
  if (Test-Path $file) {
    Write-Output ('ok   ' + $shot.name + '  ' + [math]::Round((Get-Item $file).Length / 1KB) + ' KB')
  } else {
    Write-Output ('FAIL ' + $shot.name + '  (is the service on that port running?)')
  }
}
