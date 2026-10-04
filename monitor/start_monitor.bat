@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hsrmap 实时监控台 (8768)
where python >nul 2>nul
if errorlevel 1 (
  echo [hsrmap] 找不到 python：请先安装 Python 3.11 并把它加进 PATH。
  pause
  exit /b 1
)
echo [hsrmap] 实时监控台（只读）  ->  http://127.0.0.1:8768/
echo [hsrmap] 离线地图 8766 / 审核台 8767 各自独立，互不抢端口。
echo [hsrmap] 浏览器会自动打开；关掉这个窗口就停止服务（或双击 stop_monitor.bat）。
echo.
python monitor_server.py
echo.
echo [hsrmap] 监控台已退出（退出码 %errorlevel%）。
pause
