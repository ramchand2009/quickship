import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import OrderActivityLog, WhatsAppNotificationLog, WhatsAppNotificationQueue
from core.monitoring import build_health_payload
from core.system_status import get_dashboard_system_status


class Command(BaseCommand):
    help = "Export incident snapshot JSON (health + recent queue/log/activity)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Include events from last N hours (default: 24).")
        parser.add_argument("--limit", type=int, default=100, help="Max rows per section (default: 100).")
        parser.add_argument("--out-file", type=str, default="", help="Optional output file path.")

    def _serialize_dt(self, value):
        if not value:
            return ""
        return timezone.localtime(value).isoformat()

    def handle(self, *args, **options):
        hours = max(1, int(options.get("hours") or 24))
        limit = max(1, int(options.get("limit") or 100))
        now = timezone.localtime(timezone.now())
        since = now - timedelta(hours=hours)

        queue_rows = (
            WhatsAppNotificationQueue.objects.filter(updated_at__gte=since)
            .order_by("-updated_at")[:limit]
        )
        whatsapp_rows = (
            WhatsAppNotificationLog.objects.filter(created_at__gte=since)
            .order_by("-created_at")[:limit]
        )
        activity_rows = (
            OrderActivityLog.objects.filter(created_at__gte=since)
            .order_by("-created_at")[:limit]
        )

        system_status = get_dashboard_system_status()
        serializable_status = {
            "worker": {
                "last_run_text": system_status["worker"]["last_run_text"],
                "age_minutes": system_status["worker"]["age_minutes"],
                "is_recent": system_status["worker"]["is_recent"],
            },
            "alerts": {
                "last_run_text": system_status["alerts"]["last_run_text"],
                "age_minutes": system_status["alerts"]["age_minutes"],
                "is_recent": system_status["alerts"]["is_recent"],
            },
            "backup": {
                "last_run_text": system_status["backup"]["last_run_text"],
                "age_minutes": system_status["backup"]["age_minutes"],
                "is_recent": system_status["backup"]["is_recent"],
            },
        }

        payload = {
            "generated_at": now.isoformat(),
            "window_hours": hours,
            "health": build_health_payload(),
            "system_status": serializable_status,
            "recent": {
                "queue": [
                    {
                        "id": row.pk,
                        "order_id": str(row.shiprocket_order_id or ""),
                        "status": str(row.status or ""),
                        "trigger": str(row.trigger or ""),
                        "attempt_count": int(row.attempt_count or 0),
                        "max_attempts": int(row.max_attempts or 0),
                        "last_error": str(row.last_error or ""),
                        "updated_at": self._serialize_dt(row.updated_at),
                    }
                    for row in queue_rows
                ],
                "whatsapp_logs": [
                    {
                        "id": row.pk,
                        "order_id": str(row.shiprocket_order_id or ""),
                        "trigger": str(row.trigger or ""),
                        "result": "success" if row.is_success else "failed",
                        "error_message": str(row.error_message or ""),
                        "delivery_status": str(row.delivery_status or ""),
                        "message_id": str(row.external_message_id or ""),
                        "created_at": self._serialize_dt(row.created_at),
                    }
                    for row in whatsapp_rows
                ],
                "activity_logs": [
                    {
                        "id": row.pk,
                        "order_id": str(row.shiprocket_order_id or ""),
                        "event_type": str(row.event_type or ""),
                        "result": "success" if row.is_success else "failed",
                        "title": str(row.title or ""),
                        "description": str(row.description or ""),
                        "created_at": self._serialize_dt(row.created_at),
                    }
                    for row in activity_rows
                ],
            },
        }

        output_path_value = str(options.get("out_file") or "").strip()
        if output_path_value:
            output_path = Path(output_path_value).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            incident_dir = Path(getattr(settings, "BASE_DIR")) / "logs" / "incidents"
            incident_dir.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%Y%m%d_%H%M%S")
            output_path = incident_dir / f"incident_snapshot_{stamp}.json"

        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Incident snapshot exported: {output_path}"))
