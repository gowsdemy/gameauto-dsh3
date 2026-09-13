@echo off
chcp 65001 >nul
REM One-click: create the daily scheduled task (all logic lives in setup_task.ps1)
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_task.ps1"
endlocal
