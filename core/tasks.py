from celery import shared_task
from django.conf import settings

from .queue_alerts import check_and_send_failed_queue_alert
from .models import Tenant
from .system_status import write_system_heartbeat
from .whatsapp_queue import process_whatsapp_notification_queue


@shared_task(name="core.tasks.celery_healthcheck")
def celery_healthcheck():
    write_system_heartbeat("celery_worker", {"source": "celery_healthcheck"})
    return {"ok": True}


@shared_task(name="core.tasks.process_whatsapp_queue")
def process_whatsapp_queue(limit=None, include_not_due=False, tenant_id=None, worker_name="celery"):
    if not settings.CELERY_WHATSAPP_QUEUE_ENABLED:
        return {"enabled": False, "processed": 0}

    tenant = Tenant.objects.filter(pk=tenant_id).first() if tenant_id else None
    summary = process_whatsapp_notification_queue(
        limit=limit or settings.CELERY_WHATSAPP_QUEUE_LIMIT,
        worker_name=str(worker_name or "celery"),
        include_not_due=bool(include_not_due),
        tenant=tenant,
    )
    write_system_heartbeat("queue_worker", {**summary, "worker": "celery"})

    alert_result = check_and_send_failed_queue_alert(worker_name=str(worker_name or "celery"))
    write_system_heartbeat(
        "queue_alerts",
        {
            "worker": "celery",
            "status": str(alert_result.get("status") or ""),
            "failed_count": int(alert_result.get("failed_count") or 0),
            "email_sent": int(alert_result.get("email_sent") or 0),
            "whatsapp_sent": int(alert_result.get("whatsapp_sent") or 0),
            "whatsapp_queued": int(alert_result.get("whatsapp_queued") or 0),
        },
    )
    return {"enabled": True, **summary, "alerts": alert_result}
