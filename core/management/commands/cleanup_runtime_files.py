from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Cleanup old runtime files (heartbeats and rotated logs)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--heartbeat-days",
            type=int,
            default=int(getattr(settings, "RUNTIME_HEARTBEAT_RETENTION_DAYS", 30)),
            help="Delete heartbeat files older than this many days (default from settings).",
        )
        parser.add_argument(
            "--log-days",
            type=int,
            default=int(getattr(settings, "RUNTIME_LOG_RETENTION_DAYS", 30)),
            help="Delete rotated log files older than this many days (default from settings).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show files that would be removed without deleting.",
        )

    def _file_mtime(self, file_path):
        return timezone.make_aware(
            datetime.fromtimestamp(file_path.stat().st_mtime),
            timezone.get_current_timezone(),
        )

    def _cleanup(self, files, cutoff, dry_run):
        deleted = 0
        for file_path in files:
            try:
                if self._file_mtime(file_path) >= cutoff:
                    continue
                if dry_run:
                    self.stdout.write(f"DRY-RUN remove: {file_path}")
                    deleted += 1
                    continue
                file_path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
        return deleted

    def handle(self, *args, **options):
        base_dir = Path(getattr(settings, "BASE_DIR"))
        logs_dir = base_dir / "logs"
        heartbeats_dir = logs_dir / "heartbeats"
        dry_run = bool(options.get("dry_run"))
        heartbeat_days = max(1, int(options.get("heartbeat_days") or 30))
        log_days = max(1, int(options.get("log_days") or 30))
        now = timezone.localtime(timezone.now())
        heartbeat_cutoff = now - timedelta(days=heartbeat_days)
        log_cutoff = now - timedelta(days=log_days)

        heartbeat_files = list(heartbeats_dir.glob("*.json")) if heartbeats_dir.exists() else []
        rotated_logs = []
        if logs_dir.exists():
            rotated_logs = [
                item
                for item in logs_dir.glob("*.log.*")
                if item.is_file()
            ]

        deleted_heartbeats = self._cleanup(heartbeat_files, heartbeat_cutoff, dry_run=dry_run)
        deleted_logs = self._cleanup(rotated_logs, log_cutoff, dry_run=dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Runtime cleanup done | heartbeat_deleted={deleted_heartbeats} "
                    f"log_deleted={deleted_logs} dry_run={dry_run}"
                )
            )
        )
