@echo off
chcp 65001 >nul
REM ============================================================
REM  GameMale Auto Sign-in - LOCAL RUN
REM  Requires: Windows + Microsoft Edge + Python 3.10+
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- 1) config check ----
if not exist config.env (
    echo [ERROR] config.env not found.
    echo   Copy config.env.example to config.env, then fill in your account:
    echo   copy config.env.example config.env
    pause
    exit /b 1
)

REM ---- 2) create venv (only first run) ----
if not exist .venv\Scripts\python.exe (
    echo [STEP] Creating Python virtual environment...
    py -3 -m venv .venv || python -m venv .venv
    if not exist .venv\Scripts\python.exe (
        echo [ERROR] Could not create the environment. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

REM ---- 3) install deps only if missing (faster on re-runs) ----
python -c "import ddddocr, playwright" 2>nul
if errorlevel 1 (
    echo [STEP] Installing dependencies (first time only)...
    python -m pip install --upgrade pip
    pip install ddddocr playwright
) else (
    echo [OK] Dependencies already installed.
)

REM ---- 4) run ----
echo [STEP] Starting GameMale sign-in ...
echo [INFO] A real Edge window will open. If it shows a verification box, click it.
python gamemale_v2.py

echo.
echo [DONE] Task finished. Press any key to close now, or it will auto-close in 10 seconds.
timeout /t 10
endlocal
