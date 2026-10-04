@echo off
chcp 65001 >nul
echo 停止 hsrmap 实时监控台...
for /f "tokens=5" %%%%a in ('netstat -ano ^| findstr ":8768" ^| findstr LISTENING') do taskkill /F /PID %%%%a
timeout /t 2 >nul
