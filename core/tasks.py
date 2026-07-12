from celery import shared_task
from django.conf import settings

from .queue_alerts import check_and_send_failed_queue_alert
from .system_status import write_system_heartbeat
from .whatsapp_queue import process_whatsapp_notification_queue


@shared_task(name="core.tasks.celery_healthcheck")
def celery_healthcheck():
    write_system_heartbeat("celery_worker", {"source": "celery_healthcheck"})
    return {"ok": True}


@shared_task(name="core.tasks.process_whatsapp_queue")
def process_whatsapp_queue():
    if not settings.CELERY_WHATSAPP_QUEUE_ENABLED:
        return {"enabled": False, "processed": 0}

    summary = process_whatsapp_notification_queue(
        limit=settings.CELERY_WHATSAPP_QUEUE_LIMIT,
        worker_name="celery",
    )
    write_system_heartbeat("queue_worker", {**summary, "worker": "celery"})

    alert_result = check_and_send_failed_queue_alert(worker_name="celery")
    write_system_heartbeat(
        "queue_alerts",
        {
            "worker": "celery",
            "status": str(alert_result.get("status") or ""),
            "failed_count": int(alert_result.get("failed_count") or 0),
            "email_sent": int(alert_result.get("email_sent") or 0),
            "whatsapp_sent": int(alert_result.get("whatsapp_sent") or 0),
        },
    )
    return {"enabled": True, **summary, "alerts": alert_result}
