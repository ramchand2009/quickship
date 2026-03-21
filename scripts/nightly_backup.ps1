param(
    [string]$ProjectRoot = "c:\Ramc_Project\Codex_Project1",
    [int]$RetentionDays = 14,
    [int]$CleanupHeartbeatDays = 30,
    [int]$CleanupLogDays = 30
)

Set-Location $ProjectRoot
& ".\.venv\Scripts\python.exe" manage.py backup_local_data --retention-days $RetentionDays
& ".\.venv\Scripts\python.exe" manage.py cleanup_runtime_files --heartbeat-days $CleanupHeartbeatDays --log-days $CleanupLogDays
