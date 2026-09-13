# setup_task.ps1 —— 由「配置计划任务.bat」自动调用，用户不用管这个文件
# 作用：读取 config.env 的 AUTOTIME，创建"每天自动运行"的计划任务，成功后立即运行一次签到。

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$ErrorActionPreference = "Stop"
$here     = $PSScriptRoot                                   # 本脚本所在目录（不写死路径）
$runBat   = Join-Path $here "运行签到.bat"
$cfgPath  = Join-Path $here "config.env"
$taskName = "GameMaleSignin"

Write-Host ""
Write-Host "正在配置每天自动运行 ..." -ForegroundColor Cyan

# 1) 检查运行脚本是否存在
if (-not (Test-Path $runBat)) {
    Write-Host "❌ 未找到「运行签到.bat」，请确认文件完整后再试。" -ForegroundColor Red
    Read-Host "按回车键退出" | Out-Null
    exit 1
}

# 2) 读 config.env：取 AUTOTIME（默认 00:10），并检查账号是否已填
$timeStr = "00:10"
$hasUser = $false
if (Test-Path $cfgPath) {
    foreach ($line in (Get-Content $cfgPath -Encoding UTF8)) {
        $t = $line.Trim()
        if ($t -match '^USERNAME\s*=\s*(.+)$' -and $matches[1].Trim() -ne "") { $hasUser = $true }
        if ($t -match '^AUTOTIME\s*=\s*(.+)$' -and $matches[1].Trim() -ne "") { $timeStr = $matches[1].Trim() }
    }
} else {
    Write-Host "⚠️ 未找到 config.env，请先把 config.env.example 复制为 config.env 并填写账号。" -ForegroundColor Yellow
}
if (-not $hasUser) {
    Write-Host "⚠️ config.env 里的 USERNAME 似乎还没填，签到会失败，建议先填好再来配置。" -ForegroundColor Yellow
}

# 3) 校验时间格式
if ($timeStr -notmatch '^\d{1,2}:\d{2}$') {
    Write-Host "⚠️ config.env 里的 AUTOTIME「$timeStr」格式不对，已改用默认 00:10。" -ForegroundColor Yellow
    $timeStr = "00:10"
}

# 4) 创建计划任务（每天 $timeStr 运行 运行签到.bat）
try {
    $action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c "' + $runBat + '"')
    $trigger  = New-ScheduledTaskTrigger -Daily -At $timeStr
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                 -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
} catch {
    Write-Host "❌ 创建计划任务失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "   如提示权限不足，请右键「配置计划任务.bat」→ 以管理员身份运行后再试。" -ForegroundColor Yellow
    Read-Host "按回车键退出" | Out-Null
    exit 1
}

$info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "✅ 计划任务配置成功" -ForegroundColor Green
Write-Host "   任务名：$taskName"
Write-Host "   每天运行时间：$timeStr"
if ($info -and $info.NextRunTime) {
    Write-Host "   下次运行：$($info.NextRunTime)"
}

# 5) 顺手跑一次（第一次通常需要你在窗口里点一下人机验证）
Write-Host ""
Write-Host "正在为你立即运行一次 ..." -ForegroundColor Cyan
Start-Process -FilePath "cmd.exe" -ArgumentList ('/c "' + $runBat + '"') | Out-Null
Write-Host "（已启动运行窗口；第一次可能需要你点一下人机验证，之后就会自动静默运行）"
Write-Host ""
Read-Host "按回车键关闭本窗口" | Out-Null
