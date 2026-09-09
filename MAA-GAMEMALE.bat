@echo off
chcp 65001 >nul 2>nul

REM 启动MAA（静默）
start "" /B "E:\Program Files (x86)\MAA-v5.18.1-win-x64\MAA.exe"

REM 启动游戏自动化（静默）
start "" /B /D "E:\Demo\deepseek\repo-gameauto-dsh3" "run_local.bat"

exit