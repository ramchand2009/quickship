from datetime import datetime
from datetime import timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.system_status import write_system_heartbeat


class Command(BaseCommand):
    help = "Create timestamped local backup (SQLite DB + logs) and prune old backups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out-dir",
            type=str,
            default="",
            help="Optional backup output directory. Default: <BASE_DIR>/backups",
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=14,
            help="Delete backup archives older than this many days (default: 14).",
        )

    def handle(self, *args, **options):
        base_dir = Path(getattr(settings, "BASE_DIR"))
        output_dir = Path(options.get("out_dir") or "").expanduser().resolve() if options.get("out_dir") else base_dir / "backups"
        output_dir.mkdir(parents=True, exist_ok=True)

        stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
        archive_path = output_dir / f"local_backup_{stamp}.zip"
        db_path = base_dir / "db.sqlite3"
        logs_dir = base_dir / "logs"

        with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
            if db_path.exists():
                archive.write(db_path, arcname="db.sqlite3")
            else:
                self.stdout.write(self.style.WARNING("db.sqlite3 not found; DB file skipped."))

            if logs_dir.exists():
                for item in logs_dir.rglob("*"):
                    if item.is_file():
                        archive.write(item, arcname=str(Path("logs") / item.relative_to(logs_dir)))
            else:
                self.stdout.write(self.style.WARNING("logs directory not found; log files skipped."))

        self.stdout.write(self.style.SUCCESS(f"Backup archive created: {archive_path}"))

        retention_days = max(1, int(options.get("retention_days") or 14))
        cutoff = timezone.localtime(timezone.now()) - timedelta(days=retention_days)
        deleted = 0
        for old_archive in output_dir.glob("local_backup_*.zip"):
            modified = timezone.make_aware(
                datetime.fromtimestamp(old_archive.stat().st_mtime),
                timezone.get_current_timezone(),
            )
            if modified < cutoff and old_archive != archive_path:
                try:
                    old_archive.unlink(missing_ok=True)
                    deleted += 1
                except OSError:
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f"Backup cleanup done: removed {deleted} archive(s) older than {retention_days} day(s)."
            )
        )
        write_system_heartbeat(
            "nightly_backup",
            metadata={
                "archive_path": str(archive_path),
                "retention_days": int(retention_days),
                "deleted_old_archives": int(deleted),
            },
        )
