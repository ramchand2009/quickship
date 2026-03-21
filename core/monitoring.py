from django.conf import settings
from django.utils import timezone

from .models import OrderActivityLog, ShiprocketOrder, WhatsAppNotificationLog, WhatsAppNotificationQueue


def _stale_threshold_minutes():
    try:
        value = int(getattr(settings, "WEBHOOK_STALE_MINUTES", 30) or 30)
    except (TypeError, ValueError):
        value = 30
    return max(1, value)


def _last_webhook_log():
    return (
        WhatsAppNotificationLog.objects.filter(trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS)
        .order_by("-created_at")
        .first()
    )


def get_operational_counters():
    now = timezone.localtime(timezone.now())
    today = now.date()
    failed_queue_count = WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_FAILED).count()
    pending_queue_count = WhatsAppNotificationQueue.objects.filter(
        status__in=[
            WhatsAppNotificationQueue.STATUS_PENDING,
            WhatsAppNotificationQueue.STATUS_RETRYING,
            WhatsAppNotificationQueue.STATUS_PROCESSING,
        ]
    ).count()

    today_whatsapp_sent_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SUCCESS,
        created_at__date=today,
    ).count()
    today_whatsapp_failed_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
        created_at__date=today,
    ).count()
    today_whatsapp_retried_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_RETRY,
        created_at__date=today,
    ).count()

    webhook_log = _last_webhook_log()
    last_webhook_received_at = webhook_log.created_at if webhook_log else None
    webhook_delivery_status = str(webhook_log.delivery_status or "").strip() if webhook_log else ""
    webhook_freshness_minutes = None
    if last_webhook_received_at:
        webhook_freshness_minutes = max(0, int((now - timezone.localtime(last_webhook_received_at)).total_seconds() // 60))
    stale_threshold = _stale_threshold_minutes()
    webhook_is_stale = bool(
        webhook_freshness_minutes is not None and webhook_freshness_minutes > stale_threshold
    )

    return {
        "now": now,
        "failed_queue_count": failed_queue_count,
        "pending_queue_count": pending_queue_count,
        "today_whatsapp_sent_count": today_whatsapp_sent_count,
        "today_whatsapp_failed_count": today_whatsapp_failed_count,
        "today_whatsapp_retried_count": today_whatsapp_retried_count,
        "last_webhook_received_at": last_webhook_received_at,
        "webhook_delivery_status": webhook_delivery_status,
        "webhook_freshness_minutes": webhook_freshness_minutes,
        "webhook_is_stale": webhook_is_stale,
        "webhook_stale_threshold_minutes": stale_threshold,
    }


def build_health_payload():
    counters = get_operational_counters()
    db_ok = True
    db_error = ""
    try:
        ShiprocketOrder.objects.exists()
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    payload = {
        "ok": bool(db_ok),
        "time": counters["now"].isoformat(),
        "checks": {
            "database": {
                "ok": bool(db_ok),
                "error": db_error,
            },
            "queue": {
                "failed": counters["failed_queue_count"],
                "pending": counters["pending_queue_count"],
            },
            "webhook": {
                "last_received_at": counters["last_webhook_received_at"].isoformat()
                if counters["last_webhook_received_at"]
                else "",
                "last_delivery_status": counters["webhook_delivery_status"],
                "freshness_minutes": counters["webhook_freshness_minutes"]
                if counters["webhook_freshness_minutes"] is not None
                else -1,
                "is_stale": bool(counters["webhook_is_stale"]),
                "stale_threshold_minutes": counters["webhook_stale_threshold_minutes"],
            },
        },
    }
    return payload
