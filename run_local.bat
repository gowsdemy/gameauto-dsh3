@echo off
chcp 65001 >nul
REM ============================================================
REM  GameMale Auto Sign-in - LOCAL RUN (real Edge + Cloudflare)
REM  Uses your own home IP and real Edge browser to pass the
REM  Cloudflare Turnstile verification. Requires Python 3.10+.
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- 1) config check ----
if not exist config.env (
    echo [ERROR] config.env not found. Copy config.env.example to config.env and fill it with your account info.
    echo Example:  copy config.env.example config.env
    pause
    exit /b 1
)

REM ---- 2) create venv + install deps (first run is slow) ----
if not exist .venv (
    echo [STEP] Creating Python virtual environment...
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo [STEP] Installing / updating dependencies...
python -m pip install --upgrade pip
pip install ddddocr playwright

REM ---- 3) run ----
echo [STEP] Starting...
python gamemale_v2.py

echo.
echo [DONE] Finished. Press any key to close.
pause >nul
endlocal
