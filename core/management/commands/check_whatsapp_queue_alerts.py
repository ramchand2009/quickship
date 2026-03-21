from django.core.management.base import BaseCommand

from core.queue_alerts import check_and_send_failed_queue_alert
from core.system_status import write_system_heartbeat


class Command(BaseCommand):
    help = "Check failed WhatsApp queue threshold and send email/WhatsApp alerts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--worker",
            type=str,
            default="manual",
            help="Worker/source label included in the alert message.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore cooldown and force alert attempt when threshold is met.",
        )

    def handle(self, *args, **options):
        worker = str(options.get("worker") or "manual").strip() or "manual"
        force = bool(options.get("force"))
        result = check_and_send_failed_queue_alert(worker_name=worker, force=force)
        write_system_heartbeat(
            "queue_alerts",
            metadata={
                "worker": worker,
                "status": str(result.get("status") or ""),
                "failed_count": int(result.get("failed_count") or 0),
                "email_sent": int(result.get("email_sent") or 0),
                "whatsapp_sent": int(result.get("whatsapp_sent") or 0),
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Queue alert check | status={result.get('status')} failed={result.get('failed_count')} "
                    f"threshold={result.get('threshold')} email_sent={result.get('email_sent')} "
                    f"whatsapp_sent={result.get('whatsapp_sent')} message={result.get('message')}"
                )
            )
        )
