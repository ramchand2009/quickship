from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import OrderActivityLog, WhatsAppNotificationLog, WhatsAppNotificationQueue


def _split_csv(raw_value):
    return [item.strip() for item in str(raw_value or "").split(",") if item.strip()]


class Command(BaseCommand):
    help = "Build an error digest for recent failures and optionally send it by email."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24).")
        parser.add_argument("--limit", type=int, default=10, help="Top error lines to include (default: 10).")
        parser.add_argument(
            "--send-email",
            action="store_true",
            help="Send digest summary email to recipients.",
        )
        parser.add_argument(
            "--email-to",
            type=str,
            default="",
            help="Comma-separated email recipients. Default uses WHATSAPP_ALERT_EMAIL_TO.",
        )
        parser.add_argument(
            "--subject",
            type=str,
            default="",
            help="Optional subject override.",
        )

    def handle(self, *args, **options):
        hours = max(1, int(options.get("hours") or 24))
        limit = max(1, int(options.get("limit") or 10))
        window_start = timezone.localtime(timezone.now()) - timedelta(hours=hours)

        activity_failures = OrderActivityLog.objects.filter(created_at__gte=window_start, is_success=False)
        whatsapp_failures = WhatsAppNotificationLog.objects.filter(created_at__gte=window_start, is_success=False)
        queue_failed_rows = WhatsAppNotificationQueue.objects.filter(
            updated_at__gte=window_start,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
        )

        error_counter = Counter()
        for item in activity_failures:
            text = str(item.description or item.title or "").strip()
            if text:
                error_counter[text] += 1
        for item in whatsapp_failures:
            text = str(item.error_message or "").strip()
            if text:
                error_counter[text] += 1
        for item in queue_failed_rows:
            text = str(item.last_error or "").strip()
            if text:
                error_counter[text] += 1

        lines = [
            f"Error digest window: last {hours} hour(s)",
            f"Activity failures: {activity_failures.count()}",
            f"WhatsApp log failures: {whatsapp_failures.count()}",
            f"Queue failed rows: {queue_failed_rows.count()}",
            "Top errors:",
        ]
        top_errors = error_counter.most_common(limit)
        if top_errors:
            for message, count in top_errors:
                lines.append(f"- ({count}) {message}")
        else:
            lines.append("- none")
        digest_text = "\n".join(lines)
        self.stdout.write(self.style.SUCCESS(digest_text))

        if not options.get("send_email"):
            return

        recipients = _split_csv(options.get("email_to")) or _split_csv(getattr(settings, "WHATSAPP_ALERT_EMAIL_TO", ""))
        if not recipients:
            raise CommandError("No recipients for --send-email. Provide --email-to or WHATSAPP_ALERT_EMAIL_TO.")
        subject = str(options.get("subject") or "").strip() or f"[Mathukai] Error Digest ({hours}h)"
        from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "noreply@localhost"
        send_mail(subject=subject, message=digest_text, from_email=from_email, recipient_list=recipients, fail_silently=False)
        self.stdout.write(self.style.SUCCESS(f"Digest email sent to {', '.join(recipients)}"))
