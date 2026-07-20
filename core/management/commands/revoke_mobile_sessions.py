import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import MobileRefreshToken, MobileSession


class Command(BaseCommand):
    help = "Revoke every active mobile session for incident rollback."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--reason", default="mobile_auth_disabled")

    def handle(self, *args, **options):
        active_sessions = MobileSession.objects.filter(status=MobileSession.STATUS_ACTIVE)
        session_ids = list(active_sessions.values_list("pk", flat=True))
        token_count = MobileRefreshToken.objects.filter(
            session_id__in=session_ids,
            revoked_at__isnull=True,
        ).count()
        summary = {
            "dry_run": bool(options["dry_run"]),
            "sessions_revoked": len(session_ids),
            "refresh_tokens_revoked": token_count,
        }
        if not options["dry_run"] and session_ids:
            now = timezone.now()
            reason = str(options["reason"] or "mobile_auth_disabled")[:64]
            with transaction.atomic():
                MobileSession.objects.filter(
                    pk__in=session_ids,
                    status=MobileSession.STATUS_ACTIVE,
                ).update(
                    status=MobileSession.STATUS_REVOKED,
                    revoked_at=now,
                    revocation_reason=reason,
                )
                MobileRefreshToken.objects.filter(
                    session_id__in=session_ids,
                    revoked_at__isnull=True,
                ).update(revoked_at=now)
        self.stdout.write(json.dumps(summary, sort_keys=True))
