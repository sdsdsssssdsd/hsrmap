@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hsrmap 审核台 (8767)
where python >nul 2>nul
if errorlevel 1 (
  echo [hsrmap] 找不到 python：请先安装 Python 3.11 并把它加进 PATH。
  pause
  exit /b 1
)
echo [hsrmap] 审核台    ->  http://127.0.0.1:8767/review
echo [hsrmap] 离线地图  ->  另开 start.bat（http://127.0.0.1:8766/）
echo [hsrmap] 浏览器会自动打开；关掉这个窗口就停止服务。
echo.
python -m hsrmap serve --app review --port 8767
echo.
echo [hsrmap] 服务已退出（退出码 %errorlevel%）。窗口留着，方便你看上面的报错。
pause
