from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.management.commands.audit_rc2_migration_safety import (
    find_duplicate_active_whatsapp_queue_jobs,
    find_duplicate_woocommerce_orders,
)
from core.mobile_security import mobile_secret_issues


class Command(BaseCommand):
    help = "Run startup preflight checks for env, folders, credentials, webhook token, and scheduler scripts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Treat warnings as failures and exit non-zero.",
        )

    def handle(self, *args, **options):
        strict = bool(options.get("strict"))
        failures = []
        warnings = []
        successes = []

        base_dir = Path(getattr(settings, "BASE_DIR"))
        logs_dir = base_dir / "logs"
        backups_dir = base_dir / "backups"
        scripts_dir = base_dir / "scripts"

        debug_enabled = bool(getattr(settings, "DEBUG", True))
        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        if debug_enabled:
            warnings.append("DEBUG is enabled.")
        else:
            successes.append("DEBUG is disabled.")
            if not allowed_hosts:
                failures.append("ALLOWED_HOSTS is empty while DEBUG is false.")
            else:
                successes.append(f"ALLOWED_HOSTS configured ({len(allowed_hosts)} host(s)).")
            secret_issues = mobile_secret_issues(settings)
            if secret_issues:
                failures.extend(secret_issues)
            else:
                successes.append("Mobile authentication secrets are production-safe and separated.")

        csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
        if not csrf_origins:
            warnings.append("CSRF_TRUSTED_ORIGINS is empty.")
        else:
            successes.append(f"CSRF_TRUSTED_ORIGINS configured ({len(csrf_origins)} origin(s)).")

        for directory in (logs_dir, backups_dir, scripts_dir):
            if directory.exists() and directory.is_dir():
                successes.append(f"Folder present: {directory}")
            else:
                failures.append(f"Folder missing: {directory}")

        shiprocket_email = str(getattr(settings, "SHIPROCKET_EMAIL", "") or "").strip()
        shiprocket_password = str(getattr(settings, "SHIPROCKET_PASSWORD", "") or "").strip()
        if shiprocket_email and shiprocket_password:
            successes.append("Shiprocket credentials are configured.")
        else:
            failures.append("Shiprocket credentials missing (SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD).")

        whatomate_key = str(getattr(settings, "WHATOMATE_API_KEY", "") or "").strip()
        whatomate_access_token = str(getattr(settings, "WHATOMATE_ACCESS_TOKEN", "") or "").strip()
        if whatomate_key or whatomate_access_token:
            successes.append("Whatomate credentials are configured.")
        else:
            failures.append("Whatomate credentials missing (WHATOMATE_API_KEY or WHATOMATE_ACCESS_TOKEN).")

        webhook_token = str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip()
        if webhook_token:
            successes.append("Webhook token is configured.")
        else:
            warnings.append("Webhook token missing (WHATOMATE_WEBHOOK_TOKEN).")

        required_scripts = [
            scripts_dir / "nightly_backup.ps1",
            scripts_dir / "run_whatsapp_worker.ps1",
            scripts_dir / "register_nightly_backup_task.ps1",
            scripts_dir / "register_whatsapp_worker_task.ps1",
        ]
        for script_path in required_scripts:
            if script_path.exists() and script_path.is_file():
                successes.append(f"Script present: {script_path.name}")
            else:
                failures.append(f"Script missing: {script_path}")

        duplicate_orders = find_duplicate_woocommerce_orders()
        duplicate_queue_jobs = find_duplicate_active_whatsapp_queue_jobs()
        if duplicate_orders:
            failures.append(
                f"RC2 migration blocker: {len(duplicate_orders)} duplicate WooCommerce order group(s)."
            )
            for row in duplicate_orders[:5]:
                failures.append(
                    "Duplicate WooCommerce order group: tenant_id={tenant_id} tenant={tenant} "
                    "woocommerce_order_id={woo_id} count={count} pk_range={first_pk}-{last_pk}".format(
                        tenant_id=row["tenant_id"],
                        tenant=row.get("tenant__name") or "",
                        woo_id=row["woocommerce_order_id"],
                        count=row["count"],
                        first_pk=row["first_pk"],
                        last_pk=row["last_pk"],
                    )
                )
        else:
            successes.append("RC2 WooCommerce order uniqueness audit passed.")

        if duplicate_queue_jobs:
            failures.append(
                f"RC2 migration blocker: {len(duplicate_queue_jobs)} duplicate active WhatsApp queue group(s)."
            )
            for row in duplicate_queue_jobs[:5]:
                failures.append(
                    "Duplicate active WhatsApp queue group: tenant_id={tenant_id} tenant={tenant} "
                    "idempotency_key={key} count={count} pk_range={first_pk}-{last_pk}".format(
                        tenant_id=row["tenant_id"],
                        tenant=row.get("tenant__name") or "",
                        key=row["idempotency_key"],
                        count=row["count"],
                        first_pk=row["first_pk"],
                        last_pk=row["last_pk"],
                    )
                )
        else:
            successes.append("RC2 WhatsApp queue idempotency audit passed.")

        for text in successes:
            self.stdout.write(self.style.SUCCESS(f"OK {text}"))
        for text in warnings:
            self.stdout.write(self.style.WARNING(f"WARN {text}"))
        for text in failures:
            self.stdout.write(self.style.ERROR(f"FAIL {text}"))

        if failures or (strict and warnings):
            failure_count = len(failures) + (len(warnings) if strict else 0)
            raise CommandError(f"Preflight failed with {failure_count} issue(s).")
        self.stdout.write(self.style.SUCCESS("Preflight passed."))
