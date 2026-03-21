param(
    [string]$ProjectRoot = "c:\Ramc_Project\Codex_Project1",
    [string]$TaskName = "Mathukai-Nightly-Backup",
    [string]$RunAt = "02:30",
    [int]$RetentionDays = 14
)

$scriptPath = Join-Path $ProjectRoot "scripts\nightly_backup.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Backup script not found: $scriptPath"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -ProjectRoot `"$ProjectRoot`" -RetentionDays $RetentionDays"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Mathukai nightly local backup" -Force
Write-Host "Registered scheduled task: $TaskName (daily at $RunAt)"
