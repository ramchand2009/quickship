param(
    [string]$ProjectRoot = "c:\Ramc_Project\Codex_Project1",
    [string]$TaskName = "Mathukai-WhatsApp-Queue-Worker",
    [int]$IntervalSeconds = 60,
    [int]$Limit = 50,
    [string]$WorkerName = "scheduler"
)

$scriptPath = Join-Path $ProjectRoot "scripts\run_whatsapp_worker.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Worker script not found: $scriptPath"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -ProjectRoot `"$ProjectRoot`" -IntervalSeconds $IntervalSeconds -Limit $Limit -WorkerName `"$WorkerName`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Mathukai WhatsApp queue worker" -Force
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered and started scheduled task: $TaskName"
