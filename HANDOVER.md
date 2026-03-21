# Mathukai Ops - Handover Note

Date: March 19, 2026 (IST)
Project Path: `c:\Ramc_Project\Codex_Project1`

## 1) Project Overview

Django operations platform for:

- Shiprocket order sync and order-status lifecycle
- WhatsApp notification send/resend with queue + retries
- Webhook delivery status ingestion and logs
- Packing and print workflows
- Operations dashboard + admin controls

## 2) What Was Completed Today

- Fixed resend reliability and webhook test flow
- Added role access model (`admin`, `ops_viewer`) with read-only enforcement
- Added queue controls (`Retry Failed Queue`) and KPI cards (`sent/failed/retried`)
- Added webhook token status badge and queue alert test button in settings
- Added health endpoint (`/healthz`) and metrics endpoint (`/metrics`)
- Added stale webhook admin warning banner
- Added CSV exports (delivery logs and audit export)
- Added integration smoke trigger from dashboard
- Added backup + restore + restore dry-run tooling
- Added preflight startup checks and runtime cleanup command
- Added error digest and incident snapshot commands
- Added login lockout/rate limit protections

## 3) Current Status

- `manage.py check`: PASS
- `manage.py test core.tests`: PASS (89 tests)
- Incident/ops commands validated in local run

## 4) Required Environment Variables

Minimum production essentials:

- `DJANGO_DEBUG=false`
- `DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com`
- `DJANGO_SESSION_COOKIE_SECURE=true`
- `DJANGO_CSRF_COOKIE_SECURE=true`
- `DJANGO_USE_X_FORWARDED_PROTO=true` (if behind reverse proxy)
- `SHIPROCKET_EMAIL=...`
- `SHIPROCKET_PASSWORD=...`
- `WHATOMATE_ENABLED=true`
- `WHATOMATE_BASE_URL=...`
- `WHATOMATE_API_KEY=...` (or `WHATOMATE_ACCESS_TOKEN`)
- `WHATOMATE_WEBHOOK_TOKEN=...`

Useful operational vars:

- `WHATSAPP_ALERT_EMAIL_TO=ops@example.com`
- `WHATSAPP_ALERT_WHATSAPP_TO=919999999999`
- `WEBHOOK_STALE_MINUTES=30`
- `METRICS_TOKEN=...`

## 5) Day-1 Production Checklist

1. Set/update `.env` with production values.
2. Run:
   - `.\.venv\Scripts\python.exe manage.py preflight_check --strict`
3. Run DB + app checks:
   - `.\.venv\Scripts\python.exe manage.py migrate`
   - `.\.venv\Scripts\python.exe manage.py check`
4. Bootstrap roles:
   - `.\.venv\Scripts\python.exe manage.py bootstrap_roles`
5. Register scheduler tasks (PowerShell as Admin):
   - `powershell -ExecutionPolicy Bypass -File c:\Ramc_Project\Codex_Project1\scripts\register_whatsapp_worker_task.ps1`
   - `powershell -ExecutionPolicy Bypass -File c:\Ramc_Project\Codex_Project1\scripts\register_nightly_backup_task.ps1`
6. Verify endpoints:
   - `/healthz/`
   - `/metrics/` (with token if configured)
7. Run smoke:
   - `.\.venv\Scripts\python.exe manage.py integration_smoke --skip-webhook-http`

## 6) Regular Ops Commands

- Queue worker (manual run):  
  `.\.venv\Scripts\python.exe manage.py run_whatsapp_queue_worker --interval 60 --limit 50 --worker daemon`

- One-off queue process:  
  `.\.venv\Scripts\python.exe manage.py process_whatsapp_queue --limit 50 --worker manual`

- Alert threshold check:  
  `.\.venv\Scripts\python.exe manage.py check_whatsapp_queue_alerts --worker manual`

- Error digest:  
  `.\.venv\Scripts\python.exe manage.py send_error_digest --hours 24`

- Incident snapshot JSON:  
  `.\.venv\Scripts\python.exe manage.py export_incident_snapshot --hours 24 --limit 100`

- Backup:  
  `.\.venv\Scripts\python.exe manage.py backup_local_data --retention-days 14`

- Restore dry-run:  
  `.\.venv\Scripts\python.exe manage.py restore_local_data --archive c:\Ramc_Project\Codex_Project1\backups\local_backup_YYYYMMDD_HHMMSS.zip --dry-run`

## 7) Quick Troubleshooting

- WhatsApp not sending:
  - Check `WHATSAPP` settings page, queue health card, and logs page.
  - Run queue process command once and check failures.

- Webhook not updating:
  - Validate `WHATOMATE_WEBHOOK_TOKEN`.
  - Use `Send Webhook Test`.
  - Check stale webhook banner and delivery logs.

- Credentials issues:
  - Run `preflight_check` and `integration_smoke`.

---

Primary docs for team: `OPERATIONS.md` and this `HANDOVER.md`.
