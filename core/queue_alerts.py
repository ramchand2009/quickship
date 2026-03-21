import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

from .models import WhatsAppNotificationQueue
from .whatomate import WhatomateNotificationError, send_test_whatsapp_message

logger = logging.getLogger(__name__)

_ALERT_CACHE_KEY = "whatsapp_queue_failed_alert_last_sent"


def _split_csv_values(raw_value):
    return [item.strip() for item in str(raw_value or "").split(",") if item.strip()]


def _to_int(raw_value, default_value):
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return int(default_value)


def _is_truthy(raw_value):
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _build_alert_message(failed_count, threshold, worker_name):
    now_text = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S %Z")
    worker_text = str(worker_name or "unknown").strip() or "unknown"
    return (
        "WhatsApp queue alert.\n"
        f"Failed jobs: {failed_count}\n"
        f"Threshold: {threshold}\n"
        f"Worker: {worker_text}\n"
        f"Time: {now_text}"
    )


def _build_alert_test_message(worker_name):
    now_text = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S %Z")
    worker_text = str(worker_name or "unknown").strip() or "unknown"
    return (
        "WhatsApp queue alert test.\n"
        f"Worker: {worker_text}\n"
        f"Time: {now_text}"
    )


def _dispatch_alert(subject, message_text, email_targets, whatsapp_targets, from_email):
    email_sent = 0
    whatsapp_sent = 0
    for recipient in email_targets:
        try:
            send_mail(
                subject=subject,
                message=message_text,
                from_email=from_email,
                recipient_list=[recipient],
                fail_silently=False,
            )
            email_sent += 1
        except Exception as exc:
            logger.warning("Queue alert email failed for %s: %s", recipient, exc)

    for phone_number in whatsapp_targets:
        try:
            send_test_whatsapp_message(phone_number=phone_number, message_text=message_text)
            whatsapp_sent += 1
        except WhatomateNotificationError as exc:
            logger.warning("Queue alert WhatsApp failed for %s: %s", phone_number, exc)
        except Exception as exc:
            logger.warning("Queue alert WhatsApp failed for %s: %s", phone_number, exc)
    return email_sent, whatsapp_sent


def check_and_send_failed_queue_alert(worker_name="", force=False):
    enabled = _is_truthy(getattr(settings, "WHATSAPP_ALERTS_ENABLED", True))
    threshold = max(1, _to_int(getattr(settings, "WHATSAPP_ALERT_FAILED_THRESHOLD", 10), 10))
    cooldown_minutes = max(1, _to_int(getattr(settings, "WHATSAPP_ALERT_COOLDOWN_MINUTES", 30), 30))
    failed_count = WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_FAILED).count()

    result = {
        "enabled": enabled,
        "failed_count": failed_count,
        "threshold": threshold,
        "cooldown_minutes": cooldown_minutes,
        "email_sent": 0,
        "whatsapp_sent": 0,
        "status": "ok",
        "message": "",
    }

    if not enabled:
        result["status"] = "disabled"
        result["message"] = "Queue alerts are disabled."
        return result

    if failed_count < threshold:
        result["status"] = "below_threshold"
        result["message"] = "Failed queue count is below threshold."
        return result

    cooldown_key = f"{_ALERT_CACHE_KEY}:{threshold}"
    if not force and cache.get(cooldown_key):
        result["status"] = "cooldown"
        result["message"] = "Alert skipped due to cooldown."
        return result

    alert_body = _build_alert_message(failed_count=failed_count, threshold=threshold, worker_name=worker_name)
    subject = f"[Mathukai] WhatsApp queue failed jobs alert ({failed_count})"

    email_targets = _split_csv_values(getattr(settings, "WHATSAPP_ALERT_EMAIL_TO", ""))
    whatsapp_targets = _split_csv_values(getattr(settings, "WHATSAPP_ALERT_WHATSAPP_TO", ""))
    from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "noreply@localhost"

    if not email_targets and not whatsapp_targets:
        result["status"] = "no_targets"
        result["message"] = "No alert targets configured."
        return result

    email_sent, whatsapp_sent = _dispatch_alert(
        subject=subject,
        message_text=alert_body,
        email_targets=email_targets,
        whatsapp_targets=whatsapp_targets,
        from_email=from_email,
    )
    result["email_sent"] = int(email_sent)
    result["whatsapp_sent"] = int(whatsapp_sent)

    if result["email_sent"] or result["whatsapp_sent"]:
        cache.set(cooldown_key, timezone.localtime(timezone.now()).isoformat(), timeout=cooldown_minutes * 60)
        result["status"] = "sent"
        result["message"] = "Queue alert sent."
        return result

    result["status"] = "error"
    result["message"] = "Queue alert could not be delivered."
    return result


def send_queue_alert_test(worker_name=""):
    enabled = _is_truthy(getattr(settings, "WHATSAPP_ALERTS_ENABLED", True))
    email_targets = _split_csv_values(getattr(settings, "WHATSAPP_ALERT_EMAIL_TO", ""))
    whatsapp_targets = _split_csv_values(getattr(settings, "WHATSAPP_ALERT_WHATSAPP_TO", ""))
    from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "noreply@localhost"
    result = {
        "enabled": enabled,
        "email_sent": 0,
        "whatsapp_sent": 0,
        "status": "ok",
        "message": "",
    }
    if not email_targets and not whatsapp_targets:
        result["status"] = "no_targets"
        result["message"] = "No alert targets configured."
        return result

    alert_body = _build_alert_test_message(worker_name=worker_name)
    subject = "[Mathukai] WhatsApp queue alert test"
    email_sent, whatsapp_sent = _dispatch_alert(
        subject=subject,
        message_text=alert_body,
        email_targets=email_targets,
        whatsapp_targets=whatsapp_targets,
        from_email=from_email,
    )
    result["email_sent"] = int(email_sent)
    result["whatsapp_sent"] = int(whatsapp_sent)
    if email_sent or whatsapp_sent:
        result["status"] = "sent"
        result["message"] = "Queue alert test sent."
        return result
    result["status"] = "error"
    result["message"] = "Queue alert test could not be delivered."
    return result
