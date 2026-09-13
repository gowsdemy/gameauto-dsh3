@echo off
chcp 65001 >nul
REM One-click: remove the daily scheduled task (all logic lives in remove_task.ps1)
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0remove_task.ps1"
endlocal
