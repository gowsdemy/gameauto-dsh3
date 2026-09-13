# remove_task.ps1 —— 由「卸载计划任务.bat」自动调用，用户不用管这个文件
# 作用：只删除计划任务 GameMaleSignin，不删除任何文件。

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$taskName = "GameMaleSignin"

Write-Host ""
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "✅ 计划任务卸载完成" -ForegroundColor Green
        Write-Host "   （只删除了计划任务，程序文件都还在）"
    } catch {
        Write-Host "❌ 卸载失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   如提示权限不足，请右键「卸载计划任务.bat」→ 以管理员身份运行后再试。" -ForegroundColor Yellow
    }
} else {
    Write-Host "ℹ️ 未找到计划任务「$taskName」，可能已经卸载过了。" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "按回车键关闭本窗口" | Out-Null
