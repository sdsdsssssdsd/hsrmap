@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hsrmap 项目框架台
where python >nul 2>nul
if errorlevel 1 (
  echo [hsrmap] 找不到 python：请先安装 Python 3.11 并把它加进 PATH。
  pause
  exit /b 1
)
echo [hsrmap] 生成项目框架台（从源码只读提取）...
python build.py
if errorlevel 1 (
  echo [hsrmap] 生成失败：看上面的报错；窗口留着方便你复制。
  pause
  exit /b 1
)
start "" "%~dp0index.html"
