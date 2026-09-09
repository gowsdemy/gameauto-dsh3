@echo off
chcp 65001 >nul
REM ============================================================
REM  GameMale Auto Sign-in - LOCAL RUN
REM  Requires: Windows + a Chromium browser (Edge or Chrome) + Python 3.10+
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- 1) config check ----
if exist config.env goto have_config
echo [ERROR] config.env not found.
echo   Copy config.env.example to config.env, then fill in your account.
echo   Example:  copy config.env.example config.env
pause
exit /b 1
:have_config

REM ---- 2) create venv ----
if exist .venv\Scripts\python.exe goto have_venv
echo [STEP] Creating Python virtual environment...
py -3 -m venv .venv 2>nul
if exist .venv\Scripts\python.exe goto have_venv
python -m venv .venv 2>nul
if exist .venv\Scripts\python.exe goto have_venv
echo [ERROR] Could not create the Python environment. Is Python installed and on PATH?
pause
exit /b 1
:have_venv
call .venv\Scripts\activate.bat

REM ---- 3) install deps only if missing ----
python -c "import ddddocr, playwright" 2>nul
if not errorlevel 1 goto deps_ok
echo [STEP] Installing dependencies, first time only...
python -m pip install --upgrade pip
pip install ddddocr playwright
:deps_ok

REM ---- 4) run ----
echo [STEP] Starting GameMale sign-in ...
echo [INFO] A real browser window will open. If a verification box appears, click it.
python gamemale_v2.py

echo.
echo [DONE] Task finished. Press any key to close now, or it will auto-close in 10 seconds.
timeout /t 10
endlocal
