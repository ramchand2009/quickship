import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.queue_alerts import check_and_send_failed_queue_alert
from core.system_status import write_system_heartbeat
from core.whatsapp_queue import process_whatsapp_notification_queue


class Command(BaseCommand):
    help = "Run WhatsApp queue worker continuously with a fixed polling interval."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=60,
            help="Polling interval in seconds between queue runs (default: 60).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum jobs to process per cycle (default: 50).",
        )
        parser.add_argument(
            "--worker",
            type=str,
            default="daemon",
            help="Worker name recorded in queue processing logs.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single cycle and exit.",
        )
        parser.add_argument(
            "--alerts",
            dest="alerts",
            action="store_true",
            default=True,
            help="Check failed queue threshold and send alerts after each cycle (default: enabled).",
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
        interval = max(1, int(options.get("interval") or 60))
        limit = max(1, int(options.get("limit") or 50))
        worker = str(options.get("worker") or "daemon").strip() or "daemon"
        run_once = bool(options.get("once"))
        alerts_enabled = bool(options.get("alerts", True))
        force_alert = bool(options.get("force_alert"))

        self.stdout.write(
            self.style.SUCCESS(
                f"WhatsApp worker started | interval={interval}s limit={limit} worker={worker} once={run_once}"
            )
        )
        try:
            while True:
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
                stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S %Z")
                self.stdout.write(
                    (
                        f"[{stamp}] picked={summary['picked']} processed={summary['processed']} "
                        f"success={summary['success']} retried={summary['retried']} failed={summary['failed']}"
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
                        (
                            f"[{stamp}] alert_status={alert_result.get('status')} "
                            f"failed={alert_result.get('failed_count')} "
                            f"email_sent={alert_result.get('email_sent')} "
                            f"whatsapp_sent={alert_result.get('whatsapp_sent')}"
                        )
                    )
                if run_once:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("WhatsApp worker stopped by user."))
