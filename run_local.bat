@echo off
REM ============================================================
REM  GameMale 自动签到 —— 本地运行（方案 B）
REM  使用你自己的家庭 IP，可自动通过 Cloudflare 人机验证。
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- 1) 配置 -------------
if not exist config.env (
    echo [提示] 未找到 config.env，请先复制 config.env.example 为 config.env 并填写账号密码。
    pause
    exit /b 1
)

REM ---- 2) 创建虚拟环境并安装依赖（首次运行较慢）----
if not exist .venv (
    echo [步骤] 正在创建 Python 虚拟环境...
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo [步骤] 正在安装/更新依赖...
pip install --upgrade pip
pip install requests ddddocr playwright
python -m playwright install chromium

REM ---- 3) 运行 -------------
echo [步骤] 开始运行...
python gamemale_v2.py

echo.
echo [完成] 按任意键关闭窗口。
pause >nul
endlocal
