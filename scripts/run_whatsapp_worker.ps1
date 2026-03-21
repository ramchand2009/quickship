param(
    [string]$ProjectRoot = "c:\Ramc_Project\Codex_Project1",
    [int]$IntervalSeconds = 60,
    [int]$Limit = 50,
    [string]$WorkerName = "scheduler"
)

Set-Location $ProjectRoot
& ".\.venv\Scripts\python.exe" manage.py run_whatsapp_queue_worker --interval $IntervalSeconds --limit $Limit --worker $WorkerName
