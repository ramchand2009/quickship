from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .activity import log_order_activity
from .models import OrderActivityLog, ShiprocketOrder, WhatsAppNotificationLog, WhatsAppNotificationQueue
from .whatomate import (
    WhatomateNotificationError,
    build_order_status_idempotency_payload,
    send_order_status_update,
)


def _as_json_payload(value):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, "", ()):
        return {}
    return {"value": str(value)}


def _resolve_delivery_status(result, *, is_success=False):
    result = result if isinstance(result, dict) else {}
    explicit = str(result.get("delivery_status") or "").strip().lower()
    if explicit:
        return explicit

    response_payload = result.get("response_payload")
    if isinstance(response_payload, dict):
        response_candidates = [
            response_payload.get("delivery_status"),
            response_payload.get("message_status"),
            response_payload.get("status"),
        ]
        data = response_payload.get("data")
        if isinstance(data, dict):
            response_candidates.extend(
                [
                    data.get("delivery_status"),
                    data.get("message_status"),
                    data.get("status"),
                ]
            )
        for candidate in response_candidates:
            value = str(candidate or "").strip().lower()
            if value:
                return value

    return "sent" if is_success else ""


def enqueue_whatsapp_notification(
    *,
    order,
    trigger,
    previous_status="",
    current_status="",
    initiated_by="",
    payload=None,
    max_attempts=3,
):
    plan = build_order_status_idempotency_payload(order)
    if not plan.get("sendable"):
        reason = str(plan.get("reason") or "not_configured").strip()
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SKIPPED,
            title="WhatsApp queue skipped",
            description=f"Queue skip reason: {reason}",
            previous_status=previous_status,
            current_status=current_status or order.local_status,
            metadata={"reason": reason, "trigger": trigger},
            is_success=False,
            triggered_by=initiated_by,
        )
        return {"queued": False, "reason": reason, "job": None, "plan": plan}

    idempotency_key = str(plan.get("idempotency_key") or "").strip()
    phone_number = str(plan.get("phone_number") or "").strip()
    mode = str(plan.get("mode") or "").strip()
    template_name = str(plan.get("template_name") or "").strip()
    template_id = str(plan.get("template_id") or "").strip()
    current_status_value = str(current_status or order.local_status or "").strip()

    if idempotency_key:
        existing_job = (
            WhatsAppNotificationQueue.objects.filter(
                idempotency_key=idempotency_key,
                status__in=[
                    WhatsAppNotificationQueue.STATUS_PENDING,
                    WhatsAppNotificationQueue.STATUS_RETRYING,
                    WhatsAppNotificationQueue.STATUS_PROCESSING,
                ],
            )
            .order_by("created_at")
            .first()
        )
        if existing_job:
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SKIPPED,
                title="WhatsApp queue skipped",
                description=f"Similar notification is already queued as Job #{existing_job.pk}.",
                previous_status=previous_status,
                current_status=current_status_value,
                metadata={"reason": "duplicate_pending", "existing_job_id": existing_job.pk, "trigger": trigger},
                is_success=False,
                triggered_by=initiated_by,
            )
            return {"queued": False, "reason": "duplicate_pending", "job": existing_job, "plan": plan}

        already_sent = WhatsAppNotificationLog.objects.filter(
            idempotency_key=idempotency_key,
            is_success=True,
        ).exists()
        if already_sent:
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SKIPPED,
                title="WhatsApp queue skipped",
                description="Same status notification was already sent.",
                previous_status=previous_status,
                current_status=current_status_value,
                metadata={"reason": "already_sent", "trigger": trigger},
                is_success=False,
                triggered_by=initiated_by,
            )
            return {"queued": False, "reason": "already_sent", "job": None, "plan": plan}

    job = WhatsAppNotificationQueue.objects.create(
        order=order,
        shiprocket_order_id=str(order.shiprocket_order_id or "").strip(),
        trigger=trigger,
        previous_status=str(previous_status or "").strip(),
        current_status=current_status_value,
        phone_number=phone_number,
        mode=mode,
        template_name=template_name,
        template_id=template_id,
        idempotency_key=idempotency_key,
        payload=_as_json_payload(payload),
        status=WhatsAppNotificationQueue.STATUS_PENDING,
        max_attempts=max(1, int(max_attempts or 3)),
        initiated_by=str(initiated_by or "").strip(),
    )
    log_order_activity(
        order=order,
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUED,
        title=f"WhatsApp notification queued (Job #{job.pk})",
        description=f"Trigger: {trigger}",
        previous_status=str(previous_status or "").strip(),
        current_status=current_status_value,
        metadata={
            "job_id": job.pk,
            "trigger": trigger,
            "phone_number": phone_number,
            "mode": mode,
            "template_name": template_name,
            "template_id": template_id,
            "idempotency_key": idempotency_key,
        },
        is_success=True,
        triggered_by=initiated_by,
    )
    return {"queued": True, "reason": "queued", "job": job, "plan": plan}


def _build_failure_log_message(exc):
    return str(exc or "Unknown WhatsApp queue failure").strip()


def _calculate_backoff_minutes(attempt_count):
    return min(60, 2 ** max(0, int(attempt_count or 0) - 1))


def _next_due_job(*, job_id=None, include_not_due=False):
    now = timezone.now()
    queryset = WhatsAppNotificationQueue.objects.select_for_update().filter(
        status__in=[WhatsAppNotificationQueue.STATUS_PENDING, WhatsAppNotificationQueue.STATUS_RETRYING]
    )
    if job_id is not None:
        queryset = queryset.filter(pk=job_id)
    if not include_not_due:
        queryset = queryset.filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
    return queryset.order_by("next_retry_at", "created_at").first()


def _create_log_for_job(job, *, is_success, result=None, error_message=""):
    result = result if isinstance(result, dict) else {}
    order = job.order
    delivery_status = _resolve_delivery_status(result, is_success=is_success)
    WhatsAppNotificationLog.objects.create(
        order=order,
        shiprocket_order_id=job.shiprocket_order_id,
        trigger=job.trigger,
        previous_status=job.previous_status,
        current_status=job.current_status,
        phone_number=str(result.get("phone_number") or job.phone_number or "").strip(),
        mode=str(result.get("mode") or job.mode or "").strip(),
        template_name=str(result.get("template_name") or job.template_name or "").strip(),
        template_id=str(result.get("template_id") or job.template_id or "").strip(),
        idempotency_key=str(job.idempotency_key or "").strip(),
        external_message_id=str(result.get("external_message_id") or "").strip(),
        delivery_status=delivery_status,
        webhook_event_id=str(result.get("webhook_event_id") or "").strip(),
        request_payload=_as_json_payload(result.get("request_payload")),
        response_payload=_as_json_payload(result.get("response_payload")),
        is_success=bool(is_success),
        error_message=str(error_message or "").strip(),
        triggered_by=job.initiated_by,
    )


def _execute_job(job):
    order = job.order
    if not order and job.shiprocket_order_id:
        order = ShiprocketOrder.objects.filter(shiprocket_order_id=job.shiprocket_order_id).first()

    if not order:
        raise WhatomateNotificationError("Order not found while processing WhatsApp queue job.")

    if job.idempotency_key and WhatsAppNotificationLog.objects.filter(
        idempotency_key=job.idempotency_key,
        is_success=True,
    ).exists():
        return order, {
            "sent": True,
            "mode": job.mode,
            "phone_number": job.phone_number,
            "template_name": job.template_name,
            "template_id": job.template_id,
            "request_payload": {},
            "response_payload": {"status": "duplicate_skipped"},
        }

    result = send_order_status_update(order, previous_status=job.previous_status or order.local_status)
    if not result.get("sent"):
        reason = str(result.get("reason") or "unknown").strip()
        raise WhatomateNotificationError(f"WhatsApp update not sent: {reason}")
    if not result.get("phone_number"):
        result["phone_number"] = job.phone_number
    if not result.get("mode"):
        result["mode"] = job.mode
    if not result.get("template_name"):
        result["template_name"] = job.template_name
    if not result.get("template_id"):
        result["template_id"] = job.template_id
    return order, result


def process_whatsapp_notification_queue(*, limit=20, worker_name="manual", specific_job_id=None, include_not_due=False):
    summary = {
        "picked": 0,
        "processed": 0,
        "success": 0,
        "retried": 0,
        "failed": 0,
        "worker": str(worker_name or "manual"),
        "specific_job_id": specific_job_id,
    }

    iterations = 1 if specific_job_id is not None else max(1, int(limit or 20))
    for _ in range(iterations):
        with transaction.atomic():
            job = _next_due_job(job_id=specific_job_id, include_not_due=include_not_due)
            if not job:
                break
            job.status = WhatsAppNotificationQueue.STATUS_PROCESSING
            job.attempt_count = int(job.attempt_count or 0) + 1
            job.locked_at = timezone.now()
            job.save(update_fields=["status", "attempt_count", "locked_at", "updated_at"])

        summary["picked"] += 1
        try:
            _, send_result = _execute_job(job)
        except Exception as exc:
            message = _build_failure_log_message(exc)
            lowered = message.lower()
            should_retry = True
            if "not sent: not_configured" in lowered or "not sent: disabled" in lowered:
                should_retry = False

            if (not should_retry) or job.attempt_count >= job.max_attempts:
                job.status = WhatsAppNotificationQueue.STATUS_FAILED
                job.processed_at = timezone.now()
                job.next_retry_at = None
                summary["failed"] += 1
            else:
                job.status = WhatsAppNotificationQueue.STATUS_RETRYING
                job.next_retry_at = timezone.now() + timedelta(minutes=_calculate_backoff_minutes(job.attempt_count))
                summary["retried"] += 1
            job.last_error = message
            job.result_payload = {"error": message, "worker": summary["worker"]}
            job.save(
                update_fields=[
                    "status",
                    "processed_at",
                    "next_retry_at",
                    "last_error",
                    "result_payload",
                    "updated_at",
                ]
            )
            _create_log_for_job(job, is_success=False, result={}, error_message=message)
            if job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
                log_order_activity(
                    order=job.order,
                    shiprocket_order_id=job.shiprocket_order_id,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_RETRY,
                    title=f"WhatsApp retry scheduled for Job #{job.pk}",
                    description=message,
                    previous_status=job.previous_status,
                    current_status=job.current_status,
                    metadata={
                        "job_id": job.pk,
                        "attempt_count": job.attempt_count,
                        "max_attempts": job.max_attempts,
                        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else "",
                        "worker": summary["worker"],
                    },
                    is_success=False,
                    triggered_by=job.initiated_by or summary["worker"],
                )
            else:
                log_order_activity(
                    order=job.order,
                    shiprocket_order_id=job.shiprocket_order_id,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
                    title=f"WhatsApp notification failed for Job #{job.pk}",
                    description=message,
                    previous_status=job.previous_status,
                    current_status=job.current_status,
                    metadata={
                        "job_id": job.pk,
                        "attempt_count": job.attempt_count,
                        "max_attempts": job.max_attempts,
                        "worker": summary["worker"],
                    },
                    is_success=False,
                    triggered_by=job.initiated_by or summary["worker"],
                )
        else:
            job.status = WhatsAppNotificationQueue.STATUS_SUCCESS
            job.processed_at = timezone.now()
            job.next_retry_at = None
            job.last_error = ""
            job.result_payload = _as_json_payload(send_result)
            job.save(
                update_fields=[
                    "status",
                    "processed_at",
                    "next_retry_at",
                    "last_error",
                    "result_payload",
                    "updated_at",
                ]
            )
            _create_log_for_job(job, is_success=True, result=send_result, error_message="")
            response_payload = send_result.get("response_payload") if isinstance(send_result, dict) else {}
            response_status = ""
            if isinstance(response_payload, dict):
                response_status = str(response_payload.get("status") or "").strip().lower()
            if response_status == "duplicate_skipped":
                log_order_activity(
                    order=job.order,
                    shiprocket_order_id=job.shiprocket_order_id,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SKIPPED,
                    title=f"WhatsApp duplicate skipped for Job #{job.pk}",
                    description="Queue worker skipped duplicate notification because it was already sent.",
                    previous_status=job.previous_status,
                    current_status=job.current_status,
                    metadata={
                        "job_id": job.pk,
                        "worker": summary["worker"],
                        "idempotency_key": job.idempotency_key,
                    },
                    is_success=True,
                    triggered_by=job.initiated_by or summary["worker"],
                )
            else:
                log_order_activity(
                    order=job.order,
                    shiprocket_order_id=job.shiprocket_order_id,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SUCCESS,
                    title=f"WhatsApp update sent (Job #{job.pk})",
                    description=f"Message sent to {send_result.get('phone_number') or job.phone_number or ''}.",
                    previous_status=job.previous_status,
                    current_status=job.current_status,
                    metadata={
                        "job_id": job.pk,
                        "worker": summary["worker"],
                        "phone_number": send_result.get("phone_number") or job.phone_number,
                        "mode": send_result.get("mode") or job.mode,
                        "template_name": send_result.get("template_name") or job.template_name,
                        "template_id": send_result.get("template_id") or job.template_id,
                        "external_message_id": send_result.get("external_message_id") or "",
                    },
                    is_success=True,
                    triggered_by=job.initiated_by or summary["worker"],
                )
            summary["success"] += 1

        summary["processed"] += 1

    return summary
