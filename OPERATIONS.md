# Operations Runbook

## 1) WhatsApp Queue Worker

Run continuously (recommended):

```powershell
.\.venv\Scripts\python.exe manage.py run_whatsapp_queue_worker --interval 60 --limit 50 --worker daemon
```

Register as startup scheduled task (recommended on Windows):

```powershell
powershell -ExecutionPolicy Bypass -File c:\Ramc_Project\Codex_Project1\scripts\register_whatsapp_worker_task.ps1
```

Run one cycle only:

```powershell
.\.venv\Scripts\python.exe manage.py run_whatsapp_queue_worker --once --limit 50 --worker manual
```

## 2) Scheduler Option (Windows Task Scheduler)

If you prefer scheduler instead of a long-running process, create a task every 1 minute:

```powershell
.\.venv\Scripts\python.exe manage.py process_whatsapp_queue --limit 50 --worker cron
```

To disable alert checks on this one-off command, add `--no-alerts`.

## 3) Required Environment Variables

Set these in your deployment environment:

- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com`
- `DJANGO_SESSION_COOKIE_SECURE=true`
- `DJANGO_CSRF_COOKIE_SECURE=true`
- `DJANGO_USE_X_FORWARDED_PROTO=true` (when behind reverse proxy)
- `SHIPROCKET_EMAIL`
- `SHIPROCKET_PASSWORD`
- `SHIPROCKET_BASE_URL` (optional if default endpoint is used)
- `WHATOMATE_ENABLED`
- `WHATOMATE_BASE_URL`
- `WHATOMATE_API_KEY` (or `WHATOMATE_ACCESS_TOKEN`)
- `WHATOMATE_WEBHOOK_TOKEN` (recommended for securing webhook endpoint)
- `LOGIN_LOCKOUT_ATTEMPTS=5`
- `LOGIN_LOCKOUT_WINDOW_SECONDS=900`
- `LOGIN_LOCKOUT_DURATION_SECONDS=900`

## 4) Startup Preflight Check

Run before app start/deploy cutover:

```powershell
.\.venv\Scripts\python.exe manage.py preflight_check
```

Strict mode (warnings fail the command):

```powershell
.\.venv\Scripts\python.exe manage.py preflight_check --strict
```

## 5) Role Bootstrap

Create default role groups:

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_roles
```

Role behavior:

- `admin`: can edit statuses/settings and view raw payload details.
- `ops_viewer`: read-only operational access.

## 6) Integration Smoke Check

Run service wiring checks:

```powershell
.\.venv\Scripts\python.exe manage.py integration_smoke --base-url https://your-domain
```

UI shortcut:

- `Orders Dashboard -> Run Smoke Check`

Checks:

- Shiprocket authentication
- Whatomate API connectivity
- Webhook route and optional HTTP probe

## 7) Health Endpoint

Application health endpoint:

- `/healthz/`

## 8) Retry Failed Queue

Use the dashboard button:

- `Orders Dashboard -> WhatsApp Queue Health -> Retry Failed Queue`

Or run manually:

```powershell
.\.venv\Scripts\python.exe manage.py process_whatsapp_queue --limit 50 --worker manual
```

## 9) Nightly Backups (SQLite + Logs)

One-off backup:

```powershell
.\.venv\Scripts\python.exe manage.py backup_local_data --retention-days 14
```

Restore from backup archive (safe restore, requires confirmation):

```powershell
.\.venv\Scripts\python.exe manage.py restore_local_data --archive c:\Ramc_Project\Codex_Project1\backups\local_backup_YYYYMMDD_HHMMSS.zip --yes
```

Task Scheduler script:

```powershell
powershell -ExecutionPolicy Bypass -File c:\Ramc_Project\Codex_Project1\scripts\nightly_backup.ps1
```

Register nightly backup task (daily at 02:30):

```powershell
powershell -ExecutionPolicy Bypass -File c:\Ramc_Project\Codex_Project1\scripts\register_nightly_backup_task.ps1
```

Nightly script also runs runtime cleanup:

```powershell
.\.venv\Scripts\python.exe manage.py cleanup_runtime_files --heartbeat-days 30 --log-days 30
```

## 10) Delivery Log CSV Export

Use the export button on:

- `WhatsApp Delivery Logs -> Export CSV`

Filters are preserved in the exported file.

## 11) Failed Queue Alerts (Email + WhatsApp)

Configure in `.env`:

- `WHATSAPP_ALERTS_ENABLED=true`
- `WHATSAPP_ALERT_FAILED_THRESHOLD=10`
- `WHATSAPP_ALERT_COOLDOWN_MINUTES=30`
- `WHATSAPP_ALERT_EMAIL_TO=ops@example.com,owner@example.com`
- `WHATSAPP_ALERT_WHATSAPP_TO=919999999999`

Manual check:

```powershell
.\.venv\Scripts\python.exe manage.py check_whatsapp_queue_alerts --worker manual
```

UI test button:

- `WhatsApp Settings -> Send Queue Alert Test`

`run_whatsapp_queue_worker` and `process_whatsapp_queue` now run alert checks automatically by default.

## 12) Dashboard System Status Card

Home dashboard now shows:

- `Worker` last run
- `Alerts` last run
- `Backups` last run

These values come from heartbeat files under `logs/heartbeats/` and are updated by:

- `run_whatsapp_queue_worker`
- `process_whatsapp_queue`
- `check_whatsapp_queue_alerts`
- `backup_local_data`

## 13) Audit Export CSV

Use dashboard action:

- `Orders Dashboard -> Audit CSV`

Optional filters:

- `from_date` and `to_date` (YYYY-MM-DD)

Includes:

- status changes (`OrderActivityLog`)
- manual edits (`OrderActivityLog`)
- WhatsApp resends (`WhatsAppNotificationLog`)

## 14) Restore Dry-Run From Dashboard

Use dashboard action:

- `Orders Dashboard -> Run Restore Dry-Run`

This checks the latest `backups/local_backup_*.zip` archive using `restore_local_data --dry-run`.

## 15) Metrics Endpoint

Prometheus-style metrics endpoint:

- `/metrics/`

Optional protection:

- Set `METRICS_TOKEN` and send it as:
  - `X-Metrics-Token` header, or
  - `Authorization: Bearer <token>`, or
  - `?token=<token>` query param.

## 16) Error Digest

Console summary (last 24h):

```powershell
.\.venv\Scripts\python.exe manage.py send_error_digest --hours 24
```

Send digest email:

```powershell
.\.venv\Scripts\python.exe manage.py send_error_digest --hours 24 --send-email --email-to ops@example.com
```

## 17) Incident Snapshot JSON

Export one JSON file with health + recent queue/log/activity:

```powershell
.\.venv\Scripts\python.exe manage.py export_incident_snapshot --hours 24 --limit 100
```

Optional file target:

```powershell
.\.venv\Scripts\python.exe manage.py export_incident_snapshot --out-file c:\Ramc_Project\Codex_Project1\logs\incidents\manual_snapshot.json
```

## 18) Webhook Stale Admin Banner

Dashboard shows warning banner for admin users when last webhook callback is older than:

- `WEBHOOK_STALE_MINUTES` (default `30`)
