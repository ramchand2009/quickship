from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import OrderActivityLog, WhatsAppNotificationLog, WhatsAppNotificationQueue


class Command(BaseCommand):
    help = "Prune old operational rows with separate retention for success and failure records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--success-days",
            type=int,
            default=30,
            help="Delete successful WhatsApp/activity rows older than this many days (default: 30).",
        )
        parser.add_argument(
            "--failure-days",
            type=int,
            default=90,
            help="Delete failed WhatsApp/activity rows older than this many days (default: 90).",
        )
        parser.add_argument(
            "--queue-success-days",
            type=int,
            default=14,
            help="Delete successful queue jobs older than this many days (default: 14).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be removed without deleting them.",
        )

    def handle(self, *args, **options):
        now = timezone.localtime(timezone.now())
        success_cutoff = now - timedelta(days=max(1, int(options.get("success_days") or 30)))
        failure_cutoff = now - timedelta(days=max(1, int(options.get("failure_days") or 90)))
        queue_success_cutoff = now - timedelta(days=max(1, int(options.get("queue_success_days") or 14)))
        dry_run = bool(options.get("dry_run"))

        whatsapp_success_qs = WhatsAppNotificationLog.objects.filter(is_success=True, created_at__lt=success_cutoff)
        whatsapp_failure_qs = WhatsAppNotificationLog.objects.filter(is_success=False, created_at__lt=failure_cutoff)
        activity_success_qs = OrderActivityLog.objects.filter(is_success=True, created_at__lt=success_cutoff)
        activity_failure_qs = OrderActivityLog.objects.filter(is_success=False, created_at__lt=failure_cutoff)
        queue_success_qs = WhatsAppNotificationQueue.objects.filter(
            status=WhatsAppNotificationQueue.STATUS_SUCCESS,
            updated_at__lt=queue_success_cutoff,
        )

        counts = {
            "whatsapp_success": whatsapp_success_qs.count(),
            "whatsapp_failure": whatsapp_failure_qs.count(),
            "activity_success": activity_success_qs.count(),
            "activity_failure": activity_failure_qs.count(),
            "queue_success": queue_success_qs.count(),
        }

        if not dry_run:
            whatsapp_success_qs.delete()
            whatsapp_failure_qs.delete()
            activity_success_qs.delete()
            activity_failure_qs.delete()
            queue_success_qs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Ops prune complete | dry_run={dry_run} "
                    f"whatsapp_success={counts['whatsapp_success']} "
                    f"whatsapp_failure={counts['whatsapp_failure']} "
                    f"activity_success={counts['activity_success']} "
                    f"activity_failure={counts['activity_failure']} "
                    f"queue_success={counts['queue_success']}"
                )
            )
        )
