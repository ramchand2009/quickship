from django.core.management.base import BaseCommand

from core.queue_alerts import check_and_send_failed_queue_alert
from core.system_status import write_system_heartbeat
from core.whatsapp_queue import process_whatsapp_notification_queue


class Command(BaseCommand):
    help = "Process pending WhatsApp notification queue jobs with retry/backoff handling."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20, help="Maximum queue jobs to process in one run.")
        parser.add_argument(
            "--worker",
            type=str,
            default="manual",
            help="Worker name for traceability in queue processing.",
        )
        parser.add_argument(
            "--alerts",
            dest="alerts",
            action="store_true",
            default=True,
            help="Check failed queue threshold and send alerts after processing (default: enabled).",
        )
        parser.add_argument(
            "--no-alerts",
            dest="alerts",
            action="store_false",
            help="Disable queue failure alert checks for this run.",
        )
        parser.add_argument(
            "--force-alert",
            action="store_true",
            help="Ignore alert cooldown when threshold is met.",
        )

    def handle(self, *args, **options):
        limit = options.get("limit") or 20
        worker = options.get("worker") or "manual"
        alerts_enabled = bool(options.get("alerts", True))
        force_alert = bool(options.get("force_alert"))
        summary = process_whatsapp_notification_queue(limit=limit, worker_name=worker)
        write_system_heartbeat(
            "queue_worker",
            metadata={
                "worker": str(worker),
                "picked": int(summary.get("picked", 0)),
                "processed": int(summary.get("processed", 0)),
                "success": int(summary.get("success", 0)),
                "retried": int(summary.get("retried", 0)),
                "failed": int(summary.get("failed", 0)),
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Queue processed | picked={summary['picked']} processed={summary['processed']} "
                    f"success={summary['success']} retried={summary['retried']} failed={summary['failed']} "
                    f"worker={summary['worker']}"
                )
            )
        )
        if alerts_enabled:
            alert_result = check_and_send_failed_queue_alert(worker_name=worker, force=force_alert)
            write_system_heartbeat(
                "queue_alerts",
                metadata={
                    "worker": str(worker),
                    "status": str(alert_result.get("status") or ""),
                    "failed_count": int(alert_result.get("failed_count") or 0),
                    "email_sent": int(alert_result.get("email_sent") or 0),
                    "whatsapp_sent": int(alert_result.get("whatsapp_sent") or 0),
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        f"Queue alert check | status={alert_result.get('status')} "
                        f"failed={alert_result.get('failed_count')} "
                        f"email_sent={alert_result.get('email_sent')} "
                        f"whatsapp_sent={alert_result.get('whatsapp_sent')}"
                    )
                )
            )
