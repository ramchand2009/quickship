import json

from django.conf import settings
from django.core.management.base import BaseCommand

from core.api.v1.cleanup import cleanup_mobile_auth


class Command(BaseCommand):
    help = "Expire stale mobile auth records and prune terminal history."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=settings.MOBILE_AUTH_CLEANUP_BATCH_SIZE,
        )
        parser.add_argument(
            "--retention-days",
            type=int,
            default=settings.MOBILE_AUTH_RETENTION_DAYS,
        )

    def handle(self, *args, **options):
        summary = cleanup_mobile_auth(
            batch_size=options["batch_size"],
            retention_days=options["retention_days"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(json.dumps(summary, sort_keys=True))
