import shutil
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Restore local data from backup zip (db.sqlite3 + logs)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--archive",
            type=str,
            required=True,
            help="Backup archive path created by backup_local_data.",
        )
        parser.add_argument(
            "--skip-db",
            action="store_true",
            help="Skip db.sqlite3 restore even if present in archive.",
        )
        parser.add_argument(
            "--skip-logs",
            action="store_true",
            help="Skip logs restore even if present in archive.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be restored without writing files.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required for real restore (safety confirmation).",
        )

    def handle(self, *args, **options):
        archive_path = Path(str(options.get("archive") or "")).expanduser().resolve()
        if not archive_path.exists():
            raise CommandError(f"Archive not found: {archive_path}")
        if not archive_path.is_file():
            raise CommandError(f"Archive path is not a file: {archive_path}")

        skip_db = bool(options.get("skip_db"))
        skip_logs = bool(options.get("skip_logs"))
        dry_run = bool(options.get("dry_run"))
        confirmed = bool(options.get("yes"))

        base_dir = Path(getattr(settings, "BASE_DIR"))
        backup_dir = base_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        with ZipFile(archive_path, mode="r") as archive:
            members = archive.namelist()
            has_db = "db.sqlite3" in members
            log_members = [name for name in members if name.startswith("logs/") and not name.endswith("/")]

            if (not has_db or skip_db) and (not log_members or skip_logs):
                raise CommandError("Nothing to restore with current flags (db/logs both skipped or missing).")

            self.stdout.write(
                self.style.SUCCESS(
                    f"Restore plan | archive={archive_path} has_db={has_db} log_files={len(log_members)} "
                    f"skip_db={skip_db} skip_logs={skip_logs} dry_run={dry_run}"
                )
            )

            if dry_run:
                return

            if not confirmed:
                raise CommandError("Pass --yes to execute restore.")

            stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")

            if has_db and not skip_db:
                db_path = base_dir / "db.sqlite3"
                if db_path.exists():
                    pre_restore_copy = backup_dir / f"pre_restore_db_{stamp}.sqlite3"
                    shutil.copy2(db_path, pre_restore_copy)
                    self.stdout.write(self.style.WARNING(f"Current DB backed up: {pre_restore_copy}"))
                temp_db_path = backup_dir / f"restore_tmp_db_{stamp}.sqlite3"
                with archive.open("db.sqlite3", "r") as src, open(temp_db_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                temp_db_path.replace(db_path)
                self.stdout.write(self.style.SUCCESS(f"Database restored: {db_path}"))

            if log_members and not skip_logs:
                logs_restore_dir = logs_dir / f"restored_{stamp}"
                restored_count = 0
                for member in log_members:
                    relative = Path(member).relative_to("logs")
                    target = logs_restore_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member, "r") as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    restored_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Logs restored: {restored_count} file(s) into {logs_restore_dir}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Restore completed."))
