import json
from collections import Counter
import hashlib
import hmac
import re
import csv
from urllib.parse import parse_qsl, urlencode
from io import StringIO
from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import timesince
from django.test import Client
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .access import (
    can_manage_stock,
    can_edit_operations,
    can_sync_orders,
    can_update_order_status,
    is_ops_admin,
    is_ops_viewer,
)
from .forms import (
    BulkSmartbizMappingForm,
    ContactForm,
    ProductForm,
    ProductCategoryForm,
    SenderAddressForm,
    ShiprocketOrderManualUpdateForm,
    ShiprocketOrderStatusForm,
    SignUpForm,
    StockAdjustmentForm,
    WhatsAppApiSettingsForm,
    WhatsAppMessageTestForm,
    WhatsAppStatusTemplateConfigForm,
)
from .activity import log_order_activity
from .monitoring import build_health_payload, get_operational_counters
from .models import (
    ContactMessage,
    OrderActivityLog,
    Product,
    ProductCategory,
    Project,
    SenderAddress,
    ShiprocketOrder,
    StockMovement,
    WhatsAppNotificationLog,
    WhatsAppNotificationQueue,
    WhatsAppSettings,
    WhatsAppStatusTemplateConfig,
    WhatsAppTemplate,
)
from .stock import (
    apply_manual_stock_movement,
    build_packing_scan_requirements,
    find_product_by_lookup,
    reconcile_missed_stock_deductions,
    set_manual_stock_quantity,
    sync_stock_for_status_transition,
    validate_packing_scans,
)
from .queue_alerts import send_queue_alert_test
from .shiprocket import ShiprocketAPIError, sync_orders
from .system_status import get_dashboard_system_status, write_system_heartbeat
from .whatsapp_queue import enqueue_whatsapp_notification, process_whatsapp_notification_queue
from .whatomate import (
    ORDER_TEMPLATE_FIELD_CHOICES,
    WhatomateNotificationError,
    build_order_template_context,
    check_api_connection,
    send_test_template_message,
    send_test_whatsapp_message,
    sync_templates_from_api,
)

START_POSITION_TOP_LEFT = "top_left"
START_POSITION_TOP_RIGHT = "top_right"
START_POSITION_BOTTOM_LEFT = "bottom_left"
START_POSITION_BOTTOM_RIGHT = "bottom_right"
START_POSITION_CHOICES = [
    (START_POSITION_TOP_LEFT, "Top Left"),
    (START_POSITION_TOP_RIGHT, "Top Right"),
    (START_POSITION_BOTTOM_LEFT, "Bottom Left"),
    (START_POSITION_BOTTOM_RIGHT, "Bottom Right"),
]
START_POSITION_FLOW = [value for value, _ in START_POSITION_CHOICES]
STATUS_UPDATE_SOFT_LOCK_SECONDS = 8
ORDER_MANAGEMENT_PER_PAGE_CHOICES = (25, 50, 100)
ORDER_MANAGEMENT_AUTO_REFRESH_CHOICES = (0, 15, 30, 60)
ORDER_MANAGEMENT_SAVED_VIEWS_SESSION_KEY = "order_management_saved_views"
ORDER_MANAGEMENT_UNDO_SESSION_KEY = "order_management_last_action"
ORDER_MANAGEMENT_UNDO_WINDOW_SECONDS = 10
OPS_VIEWER_TAB_ALL = "all"
OPS_VIEWER_TAB_PENDING = "pending"
OPS_VIEWER_TAB_ACCEPTED = "accepted"
OPS_VIEWER_TAB_SHIPPED = "shipped"
OPS_VIEWER_TAB_COMPLETED = "completed"


def _resolve_start_position(raw_value):
    return raw_value if raw_value in START_POSITION_FLOW else START_POSITION_TOP_LEFT


def _is_truthy(raw_value):
    return str(raw_value).lower() in {"1", "true", "yes", "on"}


def _build_bulk_pages(orders, start_position):
    queue = list(orders)
    if not queue:
        return []

    pages = []
    first_start_index = START_POSITION_FLOW.index(start_position)

    first_slots = [None] * 4
    for slot_index in range(first_start_index, 4):
        if not queue:
            break
        first_slots[slot_index] = queue.pop(0)
    pages.append({"slots": first_slots})

    while queue:
        slots = [None] * 4
        for slot_index in range(4):
            if not queue:
                break
            slots[slot_index] = queue.pop(0)
        pages.append({"slots": slots})

    return pages


_TEMPLATE_TOKEN_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _collect_strings_from_payload(value):
    strings = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(_collect_strings_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_strings_from_payload(item))
    return strings


def _extract_template_placeholders(template_obj):
    payload = template_obj.raw_payload or {}
    tokens = []
    seen = set()

    for text in _collect_strings_from_payload(payload):
        for match in _TEMPLATE_TOKEN_PATTERN.findall(text):
            token = str(match).strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)

    return tokens


def _extract_template_preview_text(template_obj):
    payload = template_obj.raw_payload or {}
    candidates = []
    seen = set()

    for text in _collect_strings_from_payload(payload):
        value = str(text or "").strip()
        if not value:
            continue
        looks_like_message = "{{" in value and "}}" in value
        if not looks_like_message and len(value) < 20:
            continue
        if value in seen:
            continue
        seen.add(value)
        candidates.append(value)

    if not candidates:
        return ""
    return "\n".join(candidates[:3])


def _default_preview_context(status_key, status_label):
    return {
        "name": "Mathukai Customer",
        "customer_name": "Mathukai Customer",
        "order_id": "SR123456789",
        "shiprocket_order_id": "SR123456789",
        "channel_order_id": "CH12345",
        "tracking_number": "TRK1234567890",
        "tracking": "TRK1234567890",
        "status": status_label,
        "local_status": status_key,
        "phone": "919876543210",
        "customer_phone": "919876543210",
        "order_date": "19-Mar-2026",
        "total": "299.00",
        "amount": "299.00",
    }


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


def _create_whatsapp_log(
    *,
    trigger,
    request,
    order=None,
    previous_status="",
    current_status="",
    result=None,
    is_success=False,
    error_message="",
):
    result = result if isinstance(result, dict) else {}
    delivery_status = _resolve_delivery_status(result, is_success=is_success)
    order_id = ""
    if order and order.shiprocket_order_id:
        order_id = order.shiprocket_order_id
    elif result.get("order_id"):
        order_id = str(result.get("order_id") or "").strip()

    user_name = ""
    if getattr(request, "user", None) and request.user.is_authenticated:
        user_name = str(request.user.username or "").strip()

    WhatsAppNotificationLog.objects.create(
        order=order,
        shiprocket_order_id=order_id,
        trigger=trigger,
        previous_status=str(previous_status or "").strip(),
        current_status=str(current_status or "").strip(),
        phone_number=str(result.get("phone_number") or "").strip(),
        mode=str(result.get("mode") or "").strip(),
        template_name=str(result.get("template_name") or "").strip(),
        template_id=str(result.get("template_id") or "").strip(),
        idempotency_key=str(result.get("idempotency_key") or "").strip(),
        external_message_id=str(result.get("external_message_id") or "").strip(),
        delivery_status=delivery_status,
        webhook_event_id=str(result.get("webhook_event_id") or "").strip(),
        request_payload=_as_json_payload(result.get("request_payload")),
        response_payload=_as_json_payload(result.get("response_payload")),
        is_success=bool(is_success),
        error_message=str(error_message or "").strip(),
        triggered_by=user_name,
    )


def _request_actor(request):
    if getattr(request, "user", None) and request.user.is_authenticated:
        return str(request.user.username or "").strip()
    return ""


def _is_ops_admin(user):
    return is_ops_admin(user)


def _is_ops_viewer(user):
    return is_ops_viewer(user)


def _can_edit_operations(user):
    return can_edit_operations(user)


def _can_sync_orders(user):
    return can_sync_orders(user)


def _can_update_order_status(user):
    return can_update_order_status(user)


def _can_manage_stock(user):
    return can_manage_stock(user)


def _redirect_ops_viewer_to_order_management(request, *, include_message=True):
    if not _is_ops_viewer(getattr(request, "user", None)):
        return None
    if include_message:
        messages.error(request, "Your role can access only Order Management and Stock Management.")
    return redirect("order_management")


def _status_update_soft_lock_key(*, order_id, actor, target_status, session_key=""):
    actor_key = str(actor or "anonymous").strip() or "anonymous"
    status_key = str(target_status or "").strip() or "unknown"
    session_text = str(session_key or "").strip() or "nosession"
    return f"status-update-lock:{order_id}:{actor_key}:{session_text}:{status_key}"


def _attempt_inline_queue_send(queue_job, *, actor="", source="ui"):
    job_id = getattr(queue_job, "pk", None)
    if not job_id:
        return None

    actor_text = str(actor or "").strip()
    worker_name = f"inline_{source}"
    if actor_text:
        worker_name = f"{worker_name}:{actor_text}"

    try:
        process_whatsapp_notification_queue(
            limit=1,
            worker_name=worker_name,
            specific_job_id=job_id,
            include_not_due=True,
        )
    except Exception:
        return None

    return WhatsAppNotificationQueue.objects.filter(pk=job_id).first()


def _process_queue_once(*, limit, worker_name, include_not_due=False):
    summary = process_whatsapp_notification_queue(
        limit=max(1, int(limit or 20)),
        worker_name=str(worker_name or "manual").strip() or "manual",
        include_not_due=bool(include_not_due),
    )
    write_system_heartbeat(
        "queue_worker",
        metadata={
            "worker": summary["worker"],
            "picked": int(summary.get("picked", 0)),
            "processed": int(summary.get("processed", 0)),
            "success": int(summary.get("success", 0)),
            "retried": int(summary.get("retried", 0)),
            "failed": int(summary.get("failed", 0)),
        },
    )
    return summary


def _dashboard_status_url(status_key):
    return f"{reverse('order_management')}?tab={status_key}"


def _format_dashboard_delta(today_value, yesterday_value):
    delta = int(today_value or 0) - int(yesterday_value or 0)
    if delta > 0:
        return f"+{delta} vs yesterday"
    if delta < 0:
        return f"{delta} vs yesterday"
    return "Same as yesterday"


def _describe_recent_timestamp(dt):
    if not dt:
        return "No activity yet"
    localized = timezone.localtime(dt)
    age_minutes = max(0, int((timezone.localtime(timezone.now()) - localized).total_seconds() // 60))
    if age_minutes < 1:
        relative = "just now"
    elif age_minutes == 1:
        relative = "1 minute ago"
    elif age_minutes < 60:
        relative = f"{age_minutes} minutes ago"
    else:
        relative = localized.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"{localized.strftime('%Y-%m-%d %H:%M:%S %Z')} ({relative})"


def _emit_stock_sync_messages(request, stock_result, *, context_label="Order"):
    if not stock_result or not stock_result.get("mode"):
        return

    mode = stock_result.get("mode")
    action_label = "deducted" if mode == "deduct" else "restored"
    movement_count = int(stock_result.get("movement_count") or 0)
    missing_skus = stock_result.get("missing_skus") or []

    if movement_count:
        messages.info(request, f"{context_label}: stock {action_label} for {movement_count} SKU(s).")
    if missing_skus:
        messages.warning(
            request,
            f"{context_label}: no product mapping found for item identifier(s): {', '.join(missing_skus)}.",
        )


def _dashboard_order_row(order, *, note="", url_name="order_detail"):
    shipping = order.display_shipping_address or {}
    return {
        "pk": order.pk,
        "order_id": str(order.shiprocket_order_id or "").strip(),
        "customer_name": shipping.get("name") or order.customer_name or "Unknown customer",
        "phone": shipping.get("phone") or "-",
        "status_label": order.get_local_status_display(),
        "note": str(note or "").strip(),
        "url": reverse(url_name, args=[order.pk]),
    }


def _order_received_note(order):
    if order.order_date:
        return f"Received {timesince(order.order_date)} ago."
    return "Received time unavailable."


def _build_dashboard_work_queues():
    new_orders = list(
        ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_NEW)
        .order_by("-order_date", "-updated_at")[:5]
    )
    accepted_orders = list(
        ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_ACCEPTED)
        .order_by("-order_date", "-updated_at")[:12]
    )
    packed_orders = list(
        ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_PACKED, label_print_count=0)
        .order_by("-order_date", "-updated_at")[:5]
    )

    packing_blockers = []
    ready_to_pack = []
    for order in accepted_orders:
        missing_fields = order.missing_fields_for_packing()
        if missing_fields:
            packing_blockers.append((order, missing_fields))
        else:
            ready_to_pack.append(order)

    return {
        "new_orders": {
            "title": "Needs Acceptance",
            "count": ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_NEW).count(),
            "empty_text": "No new orders waiting for intake.",
            "action_label": "Open New Orders",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_NEW),
            "items": [
                _dashboard_order_row(order, note=_order_received_note(order))
                for order in new_orders
            ],
        },
        "ready_to_pack": {
            "title": "Ready to Pack",
            "count": ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_ACCEPTED).count(),
            "empty_text": "No accepted orders waiting for packing.",
            "action_label": "Open Accepted Orders",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_ACCEPTED),
            "items": [
                _dashboard_order_row(order, note="Ready for packing checklist review.")
                for order in ready_to_pack[:5]
            ],
        },
        "packing_blockers": {
            "title": "Packing Checklist Blockers",
            "count": len(packing_blockers),
            "empty_text": "No accepted orders are blocked by missing packing details.",
            "action_label": "Review Accepted Orders",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_ACCEPTED),
            "items": [
                _dashboard_order_row(
                    order,
                    note=f"Packing Checklist Pending | Missing: {', '.join(missing_fields)}",
                )
                for order, missing_fields in packing_blockers[:5]
            ],
        },
        "ready_to_print": {
            "title": "Ready to Print",
            "count": ShiprocketOrder.objects.filter(
                local_status=ShiprocketOrder.STATUS_PACKED,
                label_print_count=0,
            ).count(),
            "empty_text": "No packed orders are waiting for their first label print.",
            "action_label": "Open Print Queue",
            "action_url": reverse("print_queue"),
            "items": [
                _dashboard_order_row(order, note="Packed and not yet printed.")
                for order in packed_orders
            ],
        },
    }


def _latest_queue_job():
    return WhatsAppNotificationQueue.objects.order_by("-updated_at", "-created_at").first()


def _latest_queue_failure():
    return (
        WhatsAppNotificationQueue.objects.exclude(last_error__exact="")
        .order_by("-updated_at", "-created_at")
        .first()
    )


def _latest_whatsapp_failure_log():
    return (
        WhatsAppNotificationLog.objects.filter(is_success=False)
        .order_by("-created_at")
        .first()
    )


def _build_whatsapp_diagnostics():
    queue_counts = {
        "failed": WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_FAILED).count(),
        "pending": WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_PENDING).count(),
        "retrying": WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_RETRYING).count(),
        "processing": WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_PROCESSING).count(),
        "success": WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_SUCCESS).count(),
    }
    oldest_open_job = (
        WhatsAppNotificationQueue.objects.filter(
            status__in=[
                WhatsAppNotificationQueue.STATUS_PENDING,
                WhatsAppNotificationQueue.STATUS_RETRYING,
                WhatsAppNotificationQueue.STATUS_PROCESSING,
            ]
        )
        .order_by("created_at")
        .first()
    )
    recent_jobs = list(
        WhatsAppNotificationQueue.objects.select_related("order")
        .order_by("-updated_at", "-created_at")[:8]
    )
    recent_failures = list(
        WhatsAppNotificationLog.objects.select_related("order")
        .filter(is_success=False)
        .order_by("-created_at")[:8]
    )
    latest_failure_job = _latest_queue_failure()
    latest_failure_log = _latest_whatsapp_failure_log()
    latest_error = ""
    latest_error_source = ""
    if latest_failure_job and str(latest_failure_job.last_error or "").strip():
        latest_error = str(latest_failure_job.last_error or "").strip()
        latest_error_source = f"Queue Job #{latest_failure_job.pk}"
    elif latest_failure_log and str(latest_failure_log.error_message or "").strip():
        latest_error = str(latest_failure_log.error_message or "").strip()
        latest_error_source = f"Delivery Log #{latest_failure_log.pk}"

    diagnosis = "No recent WhatsApp delivery issues detected."
    error_lower = latest_error.lower()
    if "winerror 10013" in error_lower or "forbidden by its access permissions" in error_lower:
        diagnosis = "Likely local firewall, antivirus, or network policy is blocking outbound Whatomate API calls."
    elif "unable to reach" in error_lower or "connection" in error_lower or "timeout" in error_lower:
        diagnosis = "Connectivity to the Whatomate API looks unstable. Check base URL reachability, DNS, proxy, or server availability."
    elif "api key" in error_lower or "unauthorized" in error_lower or "forbidden" in error_lower:
        diagnosis = "Whatomate credentials or access permissions may be invalid."

    error_counter = Counter()
    window_start = timezone.localtime(timezone.now()) - timedelta(hours=24)
    for item in WhatsAppNotificationQueue.objects.filter(updated_at__gte=window_start):
        text = str(item.last_error or "").strip()
        if text:
            error_counter[text] += 1
    for item in WhatsAppNotificationLog.objects.filter(created_at__gte=window_start, is_success=False):
        text = str(item.error_message or "").strip()
        if text:
            error_counter[text] += 1

    oldest_open_age_text = ""
    if oldest_open_job and oldest_open_job.created_at:
        oldest_open_age_text = f"{timesince(oldest_open_job.created_at)} ago"

    return {
        "queue_counts": queue_counts,
        "oldest_open_job": oldest_open_job,
        "oldest_open_age_text": oldest_open_age_text,
        "recent_jobs": recent_jobs,
        "recent_failures": recent_failures,
        "latest_error": latest_error,
        "latest_error_source": latest_error_source,
        "diagnosis": diagnosis,
        "top_failure_reasons": error_counter.most_common(3),
        "last_success_log": (
            WhatsAppNotificationLog.objects.filter(is_success=True)
            .exclude(trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS)
            .order_by("-created_at")
            .first()
        ),
    }


def _build_webhook_diagnostics():
    recent_webhooks = list(
        WhatsAppNotificationLog.objects.select_related("order")
        .filter(trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS)
        .order_by("-created_at")[:25]
    )
    unmatched_recent = [log for log in recent_webhooks if not log.order_id][:10]
    latest_webhook = recent_webhooks[0] if recent_webhooks else None
    return {
        "recent_webhooks": recent_webhooks,
        "unmatched_recent": unmatched_recent,
        "latest_webhook": latest_webhook,
        "webhook_token_configured": bool(str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip()),
        "health": build_health_payload()["checks"]["webhook"],
    }


def _delete_demo_data():
    demo_filter = {"shiprocket_order_id__startswith": "DEMO-"}
    counts = {
        "orders": ShiprocketOrder.objects.filter(**demo_filter).count(),
        "activity_logs": OrderActivityLog.objects.filter(**demo_filter).count(),
        "queue_jobs": WhatsAppNotificationQueue.objects.filter(**demo_filter).count(),
        "whatsapp_logs": WhatsAppNotificationLog.objects.filter(**demo_filter).count(),
    }
    with transaction.atomic():
        OrderActivityLog.objects.filter(**demo_filter).delete()
        WhatsAppNotificationQueue.objects.filter(**demo_filter).delete()
        WhatsAppNotificationLog.objects.filter(**demo_filter).delete()
        ShiprocketOrder.objects.filter(**demo_filter).delete()
    return counts


def _get_nested_value(payload, path):
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text_value(payload, paths):
    for path in paths:
        value = _get_nested_value(payload, path)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_webhook_phone(raw_phone):
    digits = "".join(ch for ch in str(raw_phone or "") if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def _build_webhook_signature(payload_bytes, secret):
    if not payload_bytes or not secret:
        return ""
    return hmac.new(
        str(secret).encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _build_webhook_test_payload():
    sample_order = ShiprocketOrder.objects.order_by("-order_date", "-updated_at").first()
    now = timezone.localtime(timezone.now())
    event_id = f"evt_ui_test_{now.strftime('%Y%m%d%H%M%S%f')}"
    order_id = sample_order.shiprocket_order_id if sample_order else "TEST-WEBHOOK-ORDER"
    phone_number = ""
    if sample_order:
        phone_number = (
            sample_order.display_shipping_address.get("phone")
            or sample_order.manual_customer_phone
            or sample_order.customer_phone
        )
    normalized_phone = _normalize_webhook_phone(phone_number) or "919999999999"
    return {
        "event_id": event_id,
        "event_type": "message_status",
        "delivery_status": "delivered",
        "message_id": f"msg_ui_{now.strftime('%H%M%S')}",
        "phone_number": normalized_phone,
        "order_id": order_id,
        "metadata": {"source": "ui_webhook_test"},
    }


def _send_internal_webhook_test(payload, host=""):
    payload = payload if isinstance(payload, dict) else {}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {}
    host = str(host or "").strip()
    if host:
        headers["HTTP_HOST"] = host
    token = str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip()
    if token:
        headers["HTTP_X_WEBHOOK_TOKEN"] = token
        signature = _build_webhook_signature(raw_body, token)
        if signature:
            headers["HTTP_X_WEBHOOK_SIGNATURE"] = signature

    response = Client().post(
        reverse("whatomate_webhook"),
        data=raw_body,
        content_type="application/json",
        **headers,
    )
    parsed = {}
    try:
        parsed = response.json()
    except Exception:
        parsed = {}
    response_text = ""
    try:
        response_text = response.content.decode("utf-8", errors="replace")
    except Exception:
        response_text = ""
    return {
        "status_code": response.status_code,
        "payload": parsed,
        "text": response_text[:500],
    }


def _resolve_order_for_webhook(order_id_text, normalized_phone, idempotency_key):
    if order_id_text:
        order = ShiprocketOrder.objects.filter(shiprocket_order_id=order_id_text).first()
        if order:
            return order

    if idempotency_key:
        log = (
            WhatsAppNotificationLog.objects.filter(idempotency_key=idempotency_key)
            .exclude(order__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if log and log.order:
            return log.order

    if normalized_phone:
        variants = {normalized_phone}
        digits = "".join(ch for ch in normalized_phone if ch.isdigit())
        if len(digits) > 10:
            variants.add(digits[-10:])
        if len(digits) == 10:
            variants.add(f"91{digits}")

        by_log = (
            WhatsAppNotificationLog.objects.filter(phone_number__in=list(variants))
            .exclude(order__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if by_log and by_log.order:
            return by_log.order

        by_order = (
            ShiprocketOrder.objects.filter(
                Q(customer_phone__in=list(variants))
                | Q(manual_customer_phone__in=list(variants))
                | Q(shipping_address__phone__in=list(variants))
            )
            .order_by("-order_date", "-updated_at")
            .first()
        )
        if by_order:
            return by_order
    return None


def _is_webhook_authorized(request, raw_body=b""):
    expected = str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip()
    if not expected:
        return True

    header_token = str(request.headers.get("X-Webhook-Token") or "").strip()
    if header_token and header_token == expected:
        return True

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer == expected:
            return True

    header_signature = str(request.headers.get("X-Webhook-Signature") or "").strip().lower()
    if header_signature and raw_body:
        expected_signature = _build_webhook_signature(raw_body, expected).lower()
        if expected_signature and hmac.compare_digest(header_signature, expected_signature):
            return True
    return False


def _is_metrics_authorized(request):
    expected = str(getattr(settings, "METRICS_TOKEN", "") or "").strip()
    if not expected:
        return True

    query_token = str(request.GET.get("token") or "").strip()
    if query_token and hmac.compare_digest(query_token, expected):
        return True

    header_token = str(request.headers.get("X-Metrics-Token") or "").strip()
    if header_token and hmac.compare_digest(header_token, expected):
        return True

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer and hmac.compare_digest(bearer, expected):
            return True
    return False


def _resolve_ops_redirect(request, *, default_name="home", active_tab=""):
    return_to = str(request.POST.get("return_to") or "").strip()
    return_query = str(request.POST.get("return_query") or "").strip()
    redirect_name = return_to if return_to in {"home", "order_management"} else default_name
    redirect_url = reverse(redirect_name)

    params = {}
    if return_query:
        for key, value in parse_qsl(return_query, keep_blank_values=False):
            if key and key != "page":
                params[key] = value
    if active_tab in dict(ShiprocketOrder.STATUS_CHOICES):
        params["tab"] = active_tab

    if params:
        redirect_url = f"{redirect_url}?{urlencode(params)}"
    if redirect_name == "order_management":
        return f"{redirect_url}#order-management-section"
    return redirect_url


def _order_status_tabs():
    return [
        {"key": ShiprocketOrder.STATUS_NEW, "label": "New Order"},
        {"key": ShiprocketOrder.STATUS_ACCEPTED, "label": "Order Accepted"},
        {"key": ShiprocketOrder.STATUS_PACKED, "label": "Order Packed"},
        {"key": ShiprocketOrder.STATUS_SHIPPED, "label": "Shipped"},
        {"key": ShiprocketOrder.STATUS_DELIVERY_ISSUE, "label": "Delivery Issue"},
        {"key": ShiprocketOrder.STATUS_OUT_FOR_DELIVERY, "label": "Out for Delivery"},
        {"key": ShiprocketOrder.STATUS_DELIVERED, "label": "Delivered"},
        {"key": ShiprocketOrder.STATUS_COMPLETED, "label": "Completed"},
        {"key": ShiprocketOrder.STATUS_CANCELLED, "label": "Order Cancelled"},
    ]


def _ops_viewer_status_tabs():
    return [
        {"key": OPS_VIEWER_TAB_ALL, "label": "All"},
        {"key": OPS_VIEWER_TAB_PENDING, "label": "Pending"},
        {"key": OPS_VIEWER_TAB_ACCEPTED, "label": "Accepted"},
        {"key": OPS_VIEWER_TAB_SHIPPED, "label": "Shipped"},
        {"key": OPS_VIEWER_TAB_COMPLETED, "label": "Completed"},
    ]


def _ops_viewer_filter_queryset(queryset, active_tab):
    if active_tab == OPS_VIEWER_TAB_PENDING:
        return queryset.filter(local_status=ShiprocketOrder.STATUS_NEW)
    if active_tab == OPS_VIEWER_TAB_ACCEPTED:
        return queryset.filter(
            local_status__in=[ShiprocketOrder.STATUS_ACCEPTED, ShiprocketOrder.STATUS_PACKED]
        )
    if active_tab == OPS_VIEWER_TAB_SHIPPED:
        return queryset.filter(
            local_status__in=[
                ShiprocketOrder.STATUS_SHIPPED,
                ShiprocketOrder.STATUS_DELIVERY_ISSUE,
                ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
            ]
        )
    if active_tab == OPS_VIEWER_TAB_COMPLETED:
        return queryset.filter(
            local_status__in=[
                ShiprocketOrder.STATUS_DELIVERED,
                ShiprocketOrder.STATUS_COMPLETED,
            ]
        )
    return queryset.exclude(local_status=ShiprocketOrder.STATUS_CANCELLED)


def _build_ops_viewer_status_counts():
    base_queryset = ShiprocketOrder.objects.all()
    return {
        OPS_VIEWER_TAB_ALL: _ops_viewer_filter_queryset(base_queryset, OPS_VIEWER_TAB_ALL).count(),
        OPS_VIEWER_TAB_PENDING: _ops_viewer_filter_queryset(base_queryset, OPS_VIEWER_TAB_PENDING).count(),
        OPS_VIEWER_TAB_ACCEPTED: _ops_viewer_filter_queryset(base_queryset, OPS_VIEWER_TAB_ACCEPTED).count(),
        OPS_VIEWER_TAB_SHIPPED: _ops_viewer_filter_queryset(base_queryset, OPS_VIEWER_TAB_SHIPPED).count(),
        OPS_VIEWER_TAB_COMPLETED: _ops_viewer_filter_queryset(base_queryset, OPS_VIEWER_TAB_COMPLETED).count(),
    }


def _ops_viewer_stage_key(status_value):
    if status_value in {ShiprocketOrder.STATUS_NEW, ShiprocketOrder.STATUS_CANCELLED}:
        return "pending"
    if status_value in {ShiprocketOrder.STATUS_ACCEPTED, ShiprocketOrder.STATUS_PACKED}:
        return "accepted"
    if status_value in {
        ShiprocketOrder.STATUS_SHIPPED,
        ShiprocketOrder.STATUS_DELIVERY_ISSUE,
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
    }:
        return "shipped"
    return "delivered"


def _ops_viewer_action_label(status_value, fallback_label):
    label_map = {
        ShiprocketOrder.STATUS_ACCEPTED: "Accept Order",
        ShiprocketOrder.STATUS_PACKED: "Pack Order",
        ShiprocketOrder.STATUS_SHIPPED: "Ship Order",
        ShiprocketOrder.STATUS_DELIVERY_ISSUE: "Mark Delivery Issue",
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY: "Out For Delivery",
        ShiprocketOrder.STATUS_DELIVERED: "Mark Delivered",
        ShiprocketOrder.STATUS_COMPLETED: "Complete Order",
        ShiprocketOrder.STATUS_CANCELLED: "Reject Order",
    }
    return label_map.get(status_value, fallback_label)


def _build_ops_viewer_detail_actions(order, status_form):
    actions = []
    for status_value, fallback_label in status_form.fields["local_status"].choices:
        actions.append(
            {
                "value": status_value,
                "label": _ops_viewer_action_label(status_value, fallback_label),
                "tone": "secondary" if status_value == ShiprocketOrder.STATUS_CANCELLED else "primary",
                "requires_phone": (
                    status_value == ShiprocketOrder.STATUS_ACCEPTED
                    and order.local_status == ShiprocketOrder.STATUS_NEW
                ),
                "requires_tracking": status_value == ShiprocketOrder.STATUS_SHIPPED,
                "is_cancel": status_value == ShiprocketOrder.STATUS_CANCELLED,
            }
        )
    return actions


def _build_ops_viewer_primary_action(order, status_form):
    actions = _build_ops_viewer_detail_actions(order, status_form)
    for action in actions:
        if not action["is_cancel"]:
            return action
    return actions[0] if actions else None


def _safe_int_choice(raw_value, choices, default_value):
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed in choices else default_value


def _resolve_active_tab(request, status_tabs):
    tab_keys = [tab["key"] for tab in status_tabs]
    requested_tab = (request.GET.get("tab") or "").strip()
    active_tab = requested_tab if requested_tab in tab_keys else ""
    if active_tab:
        return active_tab

    counts = (
        ShiprocketOrder.objects.values("local_status")
        .annotate(total=Count("id"))
    )
    count_map = {row["local_status"]: row["total"] for row in counts}
    return next(
        (tab["key"] for tab in status_tabs if count_map.get(tab["key"], 0) > 0),
        status_tabs[0]["key"],
    )


def _build_status_counts(status_tabs):
    rows = ShiprocketOrder.objects.values("local_status").annotate(total=Count("id"))
    count_map = {row["local_status"]: row["total"] for row in rows}
    counts = {"total": ShiprocketOrder.objects.count()}
    for tab in status_tabs:
        counts[tab["key"]] = int(count_map.get(tab["key"], 0))
    return counts


def _get_order_management_filters(request):
    from_date_text = (request.GET.get("from_date") or "").strip()
    to_date_text = (request.GET.get("to_date") or "").strip()
    shiprocket_status = (request.GET.get("shiprocket_status") or "").strip()
    filters = {
        "q": (request.GET.get("q") or "").strip(),
        "order_id": (request.GET.get("order_id") or "").strip(),
        "phone": (request.GET.get("phone") or "").strip(),
        "from_date": from_date_text,
        "to_date": to_date_text,
        "from_date_parsed": parse_date(from_date_text) if from_date_text else None,
        "to_date_parsed": parse_date(to_date_text) if to_date_text else None,
        "shiprocket_status": shiprocket_status,
        "per_page": _safe_int_choice(
            request.GET.get("per_page"),
            ORDER_MANAGEMENT_PER_PAGE_CHOICES,
            ORDER_MANAGEMENT_PER_PAGE_CHOICES[0],
        ),
        "auto_refresh_seconds": _safe_int_choice(
            request.GET.get("auto_refresh"),
            ORDER_MANAGEMENT_AUTO_REFRESH_CHOICES,
            0,
        ),
    }
    return filters


def _filter_order_management_queryset(queryset, filters):
    q = str(filters.get("q") or "").strip()
    order_id_filter = str(filters.get("order_id") or "").strip()
    phone_filter = str(filters.get("phone") or "").strip()
    from_date = filters.get("from_date_parsed")
    to_date = filters.get("to_date_parsed")
    shiprocket_status = str(filters.get("shiprocket_status") or "").strip()

    if q:
        queryset = queryset.filter(
            Q(shiprocket_order_id__icontains=q)
            | Q(channel_order_id__icontains=q)
            | Q(customer_name__icontains=q)
            | Q(customer_email__icontains=q)
            | Q(customer_phone__icontains=q)
            | Q(manual_customer_name__icontains=q)
            | Q(manual_customer_phone__icontains=q)
        )
    if order_id_filter:
        queryset = queryset.filter(
            Q(shiprocket_order_id__icontains=order_id_filter)
            | Q(channel_order_id__icontains=order_id_filter)
        )
    if phone_filter:
        queryset = queryset.filter(
            Q(customer_phone__icontains=phone_filter)
            | Q(manual_customer_phone__icontains=phone_filter)
            | Q(manual_customer_alternate_phone__icontains=phone_filter)
        )
    if from_date:
        queryset = queryset.filter(order_date__date__gte=from_date)
    if to_date:
        queryset = queryset.filter(order_date__date__lte=to_date)
    if shiprocket_status:
        queryset = queryset.filter(status__iexact=shiprocket_status)

    return queryset


def _apply_status_timestamps(order_obj):
    now = timezone.now()
    if order_obj.local_status == ShiprocketOrder.STATUS_SHIPPED and not order_obj.shipped_at:
        order_obj.shipped_at = now
    if order_obj.local_status == ShiprocketOrder.STATUS_OUT_FOR_DELIVERY and not order_obj.out_for_delivery_at:
        order_obj.out_for_delivery_at = now
    if order_obj.local_status == ShiprocketOrder.STATUS_DELIVERED and not order_obj.delivered_at:
        order_obj.delivered_at = now
    if order_obj.local_status == ShiprocketOrder.STATUS_COMPLETED and not order_obj.completed_at:
        order_obj.completed_at = now
    return order_obj


def _order_management_filter_payload_from_request(request):
    return {
        "q": (request.GET.get("q") or "").strip(),
        "order_id": (request.GET.get("order_id") or "").strip(),
        "phone": (request.GET.get("phone") or "").strip(),
        "shiprocket_status": (request.GET.get("shiprocket_status") or "").strip(),
        "from_date": (request.GET.get("from_date") or "").strip(),
        "to_date": (request.GET.get("to_date") or "").strip(),
        "per_page": str(
            _safe_int_choice(
                request.GET.get("per_page"),
                ORDER_MANAGEMENT_PER_PAGE_CHOICES,
                ORDER_MANAGEMENT_PER_PAGE_CHOICES[0],
            )
        ),
        "auto_refresh": str(
            _safe_int_choice(
                request.GET.get("auto_refresh"),
                ORDER_MANAGEMENT_AUTO_REFRESH_CHOICES,
                0,
            )
        ),
    }


def _get_order_management_saved_views(request):
    raw = request.session.get(ORDER_MANAGEMENT_SAVED_VIEWS_SESSION_KEY, {})
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for name, payload in raw.items():
        title = str(name or "").strip()
        if not title:
            continue
        if not isinstance(payload, dict):
            continue
        cleaned[title] = {
            "q": str(payload.get("q") or "").strip(),
            "order_id": str(payload.get("order_id") or "").strip(),
            "phone": str(payload.get("phone") or "").strip(),
            "shiprocket_status": str(payload.get("shiprocket_status") or "").strip(),
            "from_date": str(payload.get("from_date") or "").strip(),
            "to_date": str(payload.get("to_date") or "").strip(),
            "per_page": str(payload.get("per_page") or ORDER_MANAGEMENT_PER_PAGE_CHOICES[0]),
            "auto_refresh": str(payload.get("auto_refresh") or "0"),
        }
    return cleaned


def _set_order_management_saved_views(request, payload):
    request.session[ORDER_MANAGEMENT_SAVED_VIEWS_SESSION_KEY] = payload
    request.session.modified = True


def _saved_view_to_query_string(view_payload, *, active_tab):
    params = {"tab": active_tab}
    for key in ["q", "order_id", "phone", "shiprocket_status", "from_date", "to_date", "per_page", "auto_refresh"]:
        value = str(view_payload.get(key) or "").strip()
        if value:
            params[key] = value
    return urlencode(params)


def _set_order_management_undo_payload(request, payload):
    request.session[ORDER_MANAGEMENT_UNDO_SESSION_KEY] = payload
    request.session.modified = True


def _clear_order_management_undo_payload(request):
    if ORDER_MANAGEMENT_UNDO_SESSION_KEY in request.session:
        del request.session[ORDER_MANAGEMENT_UNDO_SESSION_KEY]
        request.session.modified = True


def _get_order_management_undo_context(request):
    raw = request.session.get(ORDER_MANAGEMENT_UNDO_SESSION_KEY)
    if not isinstance(raw, dict):
        return None
    expires_at = raw.get("expires_at")
    try:
        expires_at = datetime.fromisoformat(str(expires_at))
    except Exception:
        _clear_order_management_undo_payload(request)
        return None
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())

    now = timezone.now()
    if now >= expires_at:
        _clear_order_management_undo_payload(request)
        return None

    order_count = int(raw.get("order_count") or 0)
    seconds_left = max(1, int((expires_at - now).total_seconds()))
    return {
        "token": str(raw.get("token") or "").strip(),
        "order_count": order_count,
        "seconds_left": seconds_left,
        "summary": str(raw.get("summary") or "").strip(),
    }


def _build_orders_dashboard_context(request):
    can_edit_operations = _can_edit_operations(getattr(request, "user", None))
    projects = Project.objects.all()[:3]
    orders = ShiprocketOrder.objects.all()[:10]
    total_orders = ShiprocketOrder.objects.count()
    now = timezone.localtime(timezone.now())
    today = now.date()
    yesterday = today - timedelta(days=1)
    counters = get_operational_counters()
    failed_queue_count = counters["failed_queue_count"]
    pending_queue_count = counters["pending_queue_count"]
    last_webhook_received_at = counters["last_webhook_received_at"]
    webhook_delivery_status = counters["webhook_delivery_status"]
    today_whatsapp_sent_count = counters["today_whatsapp_sent_count"]
    today_whatsapp_failed_count = counters["today_whatsapp_failed_count"]
    today_whatsapp_retried_count = counters["today_whatsapp_retried_count"]
    webhook_freshness_minutes = counters["webhook_freshness_minutes"]
    webhook_is_stale = counters["webhook_is_stale"]
    webhook_stale_threshold_minutes = counters["webhook_stale_threshold_minutes"]
    show_webhook_stale_banner = bool(_is_ops_admin(getattr(request, "user", None)) and webhook_is_stale)
    system_status = get_dashboard_system_status()
    status_tabs = _order_status_tabs()
    tab_keys = [tab["key"] for tab in status_tabs]
    requested_tab = (request.GET.get("tab") or "").strip()
    active_tab = requested_tab if requested_tab in tab_keys else ""
    status_counts = {"total": total_orders}
    for tab in status_tabs:
        tab_orders = ShiprocketOrder.objects.filter(local_status=tab["key"])
        status_counts[tab["key"]] = tab_orders.count()
    if not active_tab:
        active_tab = next(
            (tab["key"] for tab in status_tabs if status_counts.get(tab["key"], 0) > 0),
            status_tabs[0]["key"],
        )

    whatsapp_diagnostics = _build_whatsapp_diagnostics()
    work_queues = _build_dashboard_work_queues()
    last_successful_send = whatsapp_diagnostics["last_success_log"]
    yesterday_sent_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SUCCESS,
        created_at__date=yesterday,
    ).count()
    yesterday_failed_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
        created_at__date=yesterday,
    ).count()
    yesterday_retried_count = OrderActivityLog.objects.filter(
        event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_RETRY,
        created_at__date=yesterday,
    ).count()

    shortcut_tabs = []
    shortcut_tones = {
        ShiprocketOrder.STATUS_NEW: "warning",
        ShiprocketOrder.STATUS_ACCEPTED: "info",
        ShiprocketOrder.STATUS_PACKED: "primary",
        ShiprocketOrder.STATUS_SHIPPED: "secondary",
        ShiprocketOrder.STATUS_DELIVERY_ISSUE: "danger",
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY: "dark",
        ShiprocketOrder.STATUS_DELIVERED: "success",
        ShiprocketOrder.STATUS_COMPLETED: "success",
        ShiprocketOrder.STATUS_CANCELLED: "light",
    }
    for tab in status_tabs:
        shortcut_tabs.append(
            {
                "label": tab["label"],
                "count": status_counts.get(tab["key"], 0),
                "url": _dashboard_status_url(tab["key"]),
                "tone": shortcut_tones.get(tab["key"], "light"),
            }
        )

    action_cards = [
        {
            "title": "Needs Acceptance",
            "count": status_counts.get(ShiprocketOrder.STATUS_NEW, 0),
            "description": "New orders waiting for intake review.",
            "action_label": "Open New Orders",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_NEW),
            "tone": "warning",
        },
        {
            "title": "Ready to Pack",
            "count": status_counts.get(ShiprocketOrder.STATUS_ACCEPTED, 0),
            "description": "Accepted orders ready for packing updates.",
            "action_label": "Open Accepted Orders",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_ACCEPTED),
            "tone": "info",
        },
        {
            "title": "Packing Checklist Blockers",
            "count": work_queues["packing_blockers"]["count"],
            "description": "Accepted orders missing mandatory packing details.",
            "action_label": "Review Blockers",
            "action_url": _dashboard_status_url(ShiprocketOrder.STATUS_ACCEPTED),
            "tone": "danger" if work_queues["packing_blockers"]["count"] else "success",
        },
        {
            "title": "Ready to Print",
            "count": work_queues["ready_to_print"]["count"],
            "description": "Packed orders waiting for their first label print.",
            "action_label": "Open Print Queue",
            "action_url": reverse("print_queue"),
            "tone": "primary",
        },
        {
            "title": "Queue Failures",
            "count": failed_queue_count,
            "description": "WhatsApp jobs that need retry or investigation.",
            "action_label": "Open WhatsApp Logs",
            "action_url": f"{reverse('whatsapp_delivery_logs')}?result=failed",
            "tone": "danger" if failed_queue_count else "success",
        },
    ]

    daily_whatsapp_cards = [
        {
            "title": "WhatsApp Sent",
            "count": today_whatsapp_sent_count,
            "delta": _format_dashboard_delta(today_whatsapp_sent_count, yesterday_sent_count),
            "tone": "success",
        },
        {
            "title": "WhatsApp Failed",
            "count": today_whatsapp_failed_count,
            "delta": _format_dashboard_delta(today_whatsapp_failed_count, yesterday_failed_count),
            "tone": "danger" if today_whatsapp_failed_count else "success",
        },
        {
            "title": "WhatsApp Retried",
            "count": today_whatsapp_retried_count,
            "delta": _format_dashboard_delta(today_whatsapp_retried_count, yesterday_retried_count),
            "tone": "primary",
        },
    ]

    low_stock_count = Product.objects.filter(
        is_active=True,
        stock_quantity__lte=F("reorder_level"),
    ).count()
    action_cards.append(
        {
            "title": "Low Stock Items",
            "count": low_stock_count,
            "description": "Products at or below reorder level that need a stock check.",
            "action_label": "Open Stock Management",
            "action_url": reverse("stock_management"),
            "tone": "danger" if low_stock_count else "success",
        }
    )

    dashboard_alerts = []
    if webhook_is_stale:
        dashboard_alerts.append(
            {
                "title": "Webhook callbacks are stale",
                "message": (
                    f"Last callback: {_describe_recent_timestamp(last_webhook_received_at)}. "
                    f"Threshold: {webhook_stale_threshold_minutes} minutes."
                ),
                "tone": "warning",
                "action_label": "Open WhatsApp Settings",
                "action_url": reverse("whatsapp_settings"),
            }
        )
    if failed_queue_count:
        dashboard_alerts.append(
            {
                "title": "WhatsApp queue has failed jobs",
                "message": (
                    f"{failed_queue_count} failed job(s) and {pending_queue_count} pending/retrying job(s) need attention."
                ),
                "tone": "danger",
                "action_label": "Open Delivery Logs",
                "action_url": f"{reverse('whatsapp_delivery_logs')}?result=failed",
            }
        )
    if not system_status["worker"]["is_recent"]:
        dashboard_alerts.append(
            {
                "title": "Queue worker heartbeat is stale",
                "message": f"Worker last ran: {system_status['worker']['last_run_text']}.",
                "tone": "warning",
                "action_label": "Open WhatsApp Logs",
                "action_url": reverse("whatsapp_delivery_logs"),
            }
        )

    health_snapshot = [
        {
            "title": "Webhook Health",
            "status": "Stale" if webhook_is_stale else "Healthy" if last_webhook_received_at else "Waiting",
            "tone": "danger" if webhook_is_stale else "success" if last_webhook_received_at else "warning",
            "primary": _describe_recent_timestamp(last_webhook_received_at) if last_webhook_received_at else "No webhook yet",
            "secondary": (
                f"Last delivery status: {webhook_delivery_status}"
                if webhook_delivery_status
                else "Waiting for webhook delivery updates."
            ),
            "action_label": "Open WhatsApp Settings",
            "action_url": reverse("whatsapp_settings"),
        },
        {
            "title": "Queue Health",
            "status": "Attention" if failed_queue_count else "Healthy",
            "tone": "danger" if failed_queue_count else "success",
            "primary": f"Failed: {failed_queue_count} | Pending/Retrying: {pending_queue_count}",
            "secondary": (
                f"Last successful send: {_describe_recent_timestamp(last_successful_send.created_at)}"
                if last_successful_send
                else "No successful WhatsApp sends yet."
            ),
            "action_label": "Open WhatsApp Logs",
            "action_url": reverse("whatsapp_delivery_logs"),
        },
        {
            "title": "System Status",
            "status": "Healthy" if system_status["worker"]["is_recent"] and system_status["alerts"]["is_recent"] else "Attention",
            "tone": "success" if system_status["worker"]["is_recent"] and system_status["alerts"]["is_recent"] else "warning",
            "primary": (
                f"Worker: {system_status['worker']['last_run_text']} | "
                f"Alerts: {system_status['alerts']['last_run_text']}"
            ),
            "secondary": f"Backups: {system_status['backup']['last_run_text']}",
            "action_label": "Open Order Management",
            "action_url": reverse("order_management"),
        },
    ]

    context = {
        "projects": projects,
        "project_count": Project.objects.count(),
        "message_count": ContactMessage.objects.count(),
        "orders": orders,
        "order_count": total_orders,
        "status_tabs": status_tabs,
        "active_tab": active_tab,
        "status_counts": status_counts,
        "failed_queue_count": failed_queue_count,
        "pending_queue_count": pending_queue_count,
        "last_webhook_received_at": last_webhook_received_at,
        "webhook_delivery_status": webhook_delivery_status,
        "today_whatsapp_sent_count": today_whatsapp_sent_count,
        "today_whatsapp_failed_count": today_whatsapp_failed_count,
        "today_whatsapp_retried_count": today_whatsapp_retried_count,
        "webhook_freshness_minutes": webhook_freshness_minutes,
        "webhook_is_stale": webhook_is_stale,
        "webhook_stale_threshold_minutes": webhook_stale_threshold_minutes,
        "show_webhook_stale_banner": show_webhook_stale_banner,
        "system_status": system_status,
        "can_edit_operations": can_edit_operations,
        "action_cards": action_cards,
        "daily_whatsapp_cards": daily_whatsapp_cards,
        "shortcut_tabs": shortcut_tabs,
        "dashboard_alerts": dashboard_alerts,
        "health_snapshot": health_snapshot,
        "work_queues": work_queues,
        "dashboard_work_queues": [
            work_queues["new_orders"],
            work_queues["ready_to_pack"],
            work_queues["packing_blockers"],
            work_queues["ready_to_print"],
        ],
        "whatsapp_diagnostics": whatsapp_diagnostics,
        "last_successful_send_text": _describe_recent_timestamp(
            last_successful_send.created_at if last_successful_send else None
        ),
    }
    return context


def home(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request, include_message=False)
    if restricted_response:
        return restricted_response
    context = _build_orders_dashboard_context(request)
    return render(request, "core/home.html", context)


def order_management(request):
    can_edit_operations = _can_edit_operations(getattr(request, "user", None))
    can_sync_orders = _can_sync_orders(getattr(request, "user", None))
    can_update_order_status = _can_update_order_status(getattr(request, "user", None))
    ops_mobile_mode = _is_ops_viewer(getattr(request, "user", None))
    status_tabs = _ops_viewer_status_tabs() if ops_mobile_mode else _order_status_tabs()
    active_tab = _resolve_active_tab(request, status_tabs)
    filters = _get_order_management_filters(request)
    status_counts = _build_ops_viewer_status_counts() if ops_mobile_mode else _build_status_counts(status_tabs)
    counters = get_operational_counters()
    system_status = get_dashboard_system_status()

    base_queryset = ShiprocketOrder.objects.defer("raw_payload", "order_items", "billing_address").order_by(
        "-order_date",
        "-updated_at",
    )
    if ops_mobile_mode:
        base_queryset = _ops_viewer_filter_queryset(base_queryset, active_tab)
    else:
        base_queryset = base_queryset.filter(local_status=active_tab)
    filtered_queryset = _filter_order_management_queryset(base_queryset, filters)
    quick_stats = filtered_queryset.aggregate(
        filtered_count=Count("id"),
        filtered_total_amount=Sum("total"),
    )

    paginator = Paginator(filtered_queryset, filters["per_page"])
    page_obj = paginator.get_page(request.GET.get("page"))
    tab_orders = []
    for order in page_obj.object_list:
        status_form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        tab_orders.append(
            {
                "order": order,
                "status_form": status_form,
                "missing_packing_fields": order.missing_fields_for_packing(),
                "primary_action": _build_ops_viewer_primary_action(order, status_form) if ops_mobile_mode else None,
            }
        )
    visible_order_ids = [order.pk for order in page_obj.object_list]
    activity_by_order_id = {}
    if visible_order_ids:
        recent_logs = (
            OrderActivityLog.objects.filter(order_id__in=visible_order_ids)
            .order_by("-created_at")[:500]
        )
        for log in recent_logs:
            if not log.order_id:
                continue
            bucket = activity_by_order_id.setdefault(log.order_id, [])
            if len(bucket) < 5:
                bucket.append(log)

    tab_query = request.GET.copy()
    tab_query.pop("tab", None)
    tab_query.pop("page", None)
    tab_filter_query = tab_query.urlencode()

    page_query = request.GET.copy()
    page_query["tab"] = active_tab
    page_query.pop("page", None)
    page_base_query = page_query.urlencode()

    export_query = request.GET.copy()
    export_query["tab"] = active_tab
    export_query.pop("page", None)
    export_query_string = export_query.urlencode()

    page_start = max(page_obj.number - 2, 1)
    page_end = min(page_obj.number + 2, paginator.num_pages)
    pagination_numbers = list(range(page_start, page_end + 1))

    shiprocket_status_values = (
        ShiprocketOrder.objects.exclude(status__isnull=True)
        .exclude(status__exact="")
        .values_list("status", flat=True)
        .distinct()
        .order_by("status")
    )
    saved_views = _get_order_management_saved_views(request)
    saved_view_rows = [
        {
            "name": name,
            "query": _saved_view_to_query_string(payload, active_tab=active_tab),
        }
        for name, payload in sorted(saved_views.items(), key=lambda item: item[0].lower())
    ]

    context = {
        "status_tabs": status_tabs,
        "status_counts": status_counts,
        "active_tab": active_tab,
        "tab_orders": tab_orders,
        "tab_filter_count": quick_stats.get("filtered_count") or 0,
        "tab_filter_total_amount": quick_stats.get("filtered_total_amount") or 0,
        "page_obj": page_obj,
        "pagination_numbers": pagination_numbers,
        "tab_filter_query": tab_filter_query,
        "page_base_query": page_base_query,
        "export_query_string": export_query_string,
        "filters": filters,
        "shiprocket_status_values": list(shiprocket_status_values)[:100],
        "can_edit_operations": can_edit_operations,
        "can_sync_orders": can_sync_orders,
        "can_update_order_status": can_update_order_status,
        "ops_mobile_mode": ops_mobile_mode,
        "failed_queue_count": counters["failed_queue_count"],
        "pending_queue_count": counters["pending_queue_count"],
        "last_webhook_received_at": counters["last_webhook_received_at"],
        "webhook_delivery_status": counters["webhook_delivery_status"],
        "webhook_is_stale": counters["webhook_is_stale"],
        "webhook_stale_threshold_minutes": counters["webhook_stale_threshold_minutes"],
        "system_status": system_status,
        "bulk_target_statuses": ShiprocketOrder.STATUS_CHOICES,
        "bulk_cancel_reason_choices": ShiprocketOrder.CANCELLATION_REASON_CHOICES,
        "activity_by_order_id": activity_by_order_id,
        "saved_view_rows": saved_view_rows,
        "undo_context": _get_order_management_undo_context(request),
        "advanced_filters_active": bool(
            filters.get("order_id")
            or filters.get("phone")
            or filters.get("shiprocket_status")
            or filters.get("from_date")
            or filters.get("to_date")
            or filters.get("per_page") != ORDER_MANAGEMENT_PER_PAGE_CHOICES[0]
        ),
    }
    template_name = "core/order_management_ops.html" if ops_mobile_mode else "core/order_management.html"
    return render(request, template_name, context)


@login_required
@require_POST
def order_management_save_view(request):
    active_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="order_management", active_tab=active_tab)
    view_name = str(request.POST.get("view_name") or "").strip()
    if not view_name:
        messages.warning(request, "Enter a name to save this view.")
        return redirect(redirect_url)

    saved_views = _get_order_management_saved_views(request)
    if len(saved_views) >= 12 and view_name not in saved_views:
        messages.warning(request, "Saved view limit reached (12). Delete one and try again.")
        return redirect(redirect_url)

    payload = {
        "q": str(request.POST.get("q") or "").strip(),
        "order_id": str(request.POST.get("order_id") or "").strip(),
        "phone": str(request.POST.get("phone") or "").strip(),
        "shiprocket_status": str(request.POST.get("shiprocket_status") or "").strip(),
        "from_date": str(request.POST.get("from_date") or "").strip(),
        "to_date": str(request.POST.get("to_date") or "").strip(),
        "per_page": str(
            _safe_int_choice(
                request.POST.get("per_page"),
                ORDER_MANAGEMENT_PER_PAGE_CHOICES,
                ORDER_MANAGEMENT_PER_PAGE_CHOICES[0],
            )
        ),
        "auto_refresh": str(
            _safe_int_choice(
                request.POST.get("auto_refresh"),
                ORDER_MANAGEMENT_AUTO_REFRESH_CHOICES,
                0,
            )
        ),
    }
    saved_views[view_name] = payload
    _set_order_management_saved_views(request, saved_views)
    messages.success(request, f"Saved view '{view_name}'.")
    return redirect(redirect_url)


@login_required
@require_POST
def order_management_delete_view(request):
    active_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="order_management", active_tab=active_tab)
    view_name = str(request.POST.get("view_name") or "").strip()
    if not view_name:
        messages.warning(request, "Select a saved view to delete.")
        return redirect(redirect_url)

    saved_views = _get_order_management_saved_views(request)
    if view_name in saved_views:
        del saved_views[view_name]
        _set_order_management_saved_views(request, saved_views)
        messages.success(request, f"Deleted saved view '{view_name}'.")
    else:
        messages.info(request, "Saved view not found.")
    return redirect(redirect_url)


@login_required
@require_POST
def order_management_undo_last_action(request):
    active_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="order_management", active_tab=active_tab)
    undo_raw = request.session.get(ORDER_MANAGEMENT_UNDO_SESSION_KEY)
    if not isinstance(undo_raw, dict):
        messages.info(request, "No recent action available to undo.")
        return redirect(redirect_url)

    expected_token = str(undo_raw.get("token") or "").strip()
    posted_token = str(request.POST.get("undo_token") or "").strip()
    if not expected_token or posted_token != expected_token:
        messages.warning(request, "Undo token mismatch. Refresh and try again.")
        return redirect(redirect_url)

    undo_context = _get_order_management_undo_context(request)
    if not undo_context:
        messages.info(request, "Undo window expired.")
        return redirect(redirect_url)

    entries = undo_raw.get("entries") if isinstance(undo_raw.get("entries"), list) else []
    if not entries:
        _clear_order_management_undo_payload(request)
        messages.info(request, "No undo entries found.")
        return redirect(redirect_url)

    actor = _request_actor(request)
    status_label_map = dict(ShiprocketOrder.STATUS_CHOICES)
    order_ids = [entry.get("order_id") for entry in entries if str(entry.get("order_id") or "").isdigit()]
    orders_by_id = {order.pk: order for order in ShiprocketOrder.objects.filter(pk__in=order_ids)}

    reverted = 0
    for entry in entries:
        try:
            order_id = int(entry.get("order_id"))
        except (TypeError, ValueError):
            continue
        order = orders_by_id.get(order_id)
        if not order:
            continue
        from_status = str(entry.get("from_status") or "").strip()
        to_status = str(entry.get("to_status") or "").strip()
        if not from_status or not to_status:
            continue
        if order.local_status != to_status:
            continue

        order.local_status = from_status
        order.save(update_fields=["local_status", "updated_at"])
        reverted += 1
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            title=(
                "Undo status change from "
                f"{status_label_map.get(to_status, to_status)} to "
                f"{status_label_map.get(from_status, from_status)}"
            ),
            previous_status=to_status,
            current_status=from_status,
            metadata={"undo": True},
            is_success=True,
            triggered_by=actor,
        )

    _clear_order_management_undo_payload(request)
    if reverted:
        messages.success(request, f"Undo completed for {reverted} order(s).")
    else:
        messages.info(request, "Nothing to undo. Status changed after last action.")
    return redirect(redirect_url)


@login_required
def order_management_export_csv(request):
    status_tabs = _order_status_tabs()
    active_tab = _resolve_active_tab(request, status_tabs)
    filters = _get_order_management_filters(request)
    queryset = (
        ShiprocketOrder.objects.filter(local_status=active_tab)
        .defer("raw_payload", "order_items", "billing_address")
        .order_by("-order_date", "-updated_at")
    )
    queryset = _filter_order_management_queryset(queryset, filters)

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="order_management_{active_tab}_{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "order_id",
            "channel_order_id",
            "customer_name",
            "customer_phone",
            "local_status",
            "shiprocket_status",
            "total",
            "order_date",
            "tracking_number",
            "missing_packing_fields",
        ]
    )

    for order in queryset[:5000]:
        shipping = order.display_shipping_address
        writer.writerow(
            [
                str(order.shiprocket_order_id or "").strip(),
                str(order.channel_order_id or "").strip(),
                str(order.customer_name or "").strip(),
                str(
                    shipping.get("phone")
                    or order.manual_customer_phone
                    or order.customer_phone
                    or ""
                ).strip(),
                str(order.local_status or "").strip(),
                str(order.status or "").strip(),
                str(order.total or "").strip(),
                timezone.localtime(order.order_date).strftime("%Y-%m-%d %H:%M:%S %Z") if order.order_date else "",
                str(order.tracking_number or "").strip(),
                ", ".join(order.missing_fields_for_packing()),
            ]
        )
    return response


@login_required
@require_POST
def bulk_update_shiprocket_order_status(request):
    redirect_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="home", active_tab=redirect_tab)

    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot run bulk status updates.")
        return redirect(redirect_url)

    selected_ids = request.POST.getlist("order_ids")
    selected_ids = [value for value in selected_ids if str(value).strip().isdigit()]
    if not selected_ids:
        messages.warning(request, "Select at least one order for bulk update.")
        return redirect(redirect_url)

    target_status = (request.POST.get("bulk_local_status") or "").strip()
    valid_target_statuses = {value for value, _ in ShiprocketOrder.STATUS_CHOICES}
    if target_status not in valid_target_statuses:
        messages.error(request, "Select a valid bulk target status.")
        return redirect(redirect_url)

    actor = _request_actor(request)
    bulk_phone = (request.POST.get("bulk_manual_customer_phone") or "").strip()
    bulk_tracking_number = (request.POST.get("bulk_tracking_number") or "").strip().upper()
    bulk_cancellation_reason = (request.POST.get("bulk_cancellation_reason") or "").strip()
    bulk_cancellation_note = (request.POST.get("bulk_cancellation_note") or "").strip()

    orders = list(
        ShiprocketOrder.objects.filter(pk__in=selected_ids)
        .order_by("-order_date", "-updated_at")
    )
    if not orders:
        messages.warning(request, "No matching orders found for bulk update.")
        return redirect(redirect_url)

    success_count = 0
    queued_count = 0
    failed_count = 0
    success_samples = []
    failed_samples = []
    undo_entries = []
    stock_adjusted_count = 0
    missing_stock_skus = set()

    status_label_map = dict(ShiprocketOrder.STATUS_CHOICES)

    for order in orders:
        previous_status = order.local_status
        prefix = f"order-{order.pk}"
        payload = {
            f"{prefix}-local_status": target_status,
            f"{prefix}-manual_customer_phone": bulk_phone or order.manual_customer_phone,
            f"{prefix}-tracking_number": bulk_tracking_number or order.tracking_number,
            f"{prefix}-cancellation_reason": bulk_cancellation_reason or order.cancellation_reason,
            f"{prefix}-cancellation_note": bulk_cancellation_note or order.cancellation_note,
        }

        form = ShiprocketOrderStatusForm(payload, instance=order, prefix=prefix)
        if not form.is_valid():
            failed_count += 1
            first_error = ""
            for errors in form.errors.values():
                if errors:
                    first_error = str(errors[0])
                    break
            if len(failed_samples) < 5:
                failed_samples.append(f"{order.shiprocket_order_id}: {first_error or 'validation failed'}")
            continue

        updated_order = form.save(commit=False)
        updated_order = _apply_status_timestamps(updated_order)
        updated_order.save()
        success_count += 1
        if len(success_samples) < 5:
            success_samples.append(str(updated_order.shiprocket_order_id or "").strip())
        stock_result = {}

        if previous_status != updated_order.local_status:
            stock_result = sync_stock_for_status_transition(
                order=updated_order,
                previous_status=previous_status,
                current_status=updated_order.local_status,
                actor=actor,
            )
            stock_adjusted_count += int(stock_result.get("movement_count") or 0)
            missing_stock_skus.update(stock_result.get("missing_skus") or [])
            undo_entries.append(
                {
                    "order_id": updated_order.pk,
                    "from_status": previous_status,
                    "to_status": updated_order.local_status,
                }
            )
            log_order_activity(
                order=updated_order,
                event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                title=(
                    "Bulk status moved from "
                    f"{status_label_map.get(previous_status, previous_status)} to "
                    f"{status_label_map.get(updated_order.local_status, updated_order.local_status)}"
                ),
                previous_status=previous_status,
                current_status=updated_order.local_status,
                metadata={
                    "bulk_update": True,
                    "tracking_number": updated_order.tracking_number,
                    "cancellation_reason": updated_order.cancellation_reason,
                    "cancellation_note": updated_order.cancellation_note,
                },
                is_success=True,
                triggered_by=actor,
            )
            try:
                enqueue_result = enqueue_whatsapp_notification(
                    order=updated_order,
                    trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
                    previous_status=previous_status,
                    current_status=updated_order.local_status,
                    initiated_by=actor,
                )
            except Exception as exc:
                log_order_activity(
                    order=updated_order,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
                    title="WhatsApp queueing failed for bulk update",
                    description=str(exc),
                    previous_status=previous_status,
                    current_status=updated_order.local_status,
                    metadata={"stage": "enqueue", "trigger": WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE},
                    is_success=False,
                    triggered_by=actor,
                )
            else:
                if enqueue_result.get("queued"):
                    queued_count += 1

    if undo_entries:
        now = timezone.now()
        _set_order_management_undo_payload(
            request,
            {
                "token": uuid4().hex,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ORDER_MANAGEMENT_UNDO_WINDOW_SECONDS)).isoformat(),
                "order_count": len(undo_entries),
                "summary": f"Bulk update to {status_label_map.get(target_status, target_status)}",
                "entries": undo_entries,
            },
        )
    else:
        _clear_order_management_undo_payload(request)

    if success_count:
        success_examples = ", ".join([sample for sample in success_samples if sample])
        examples_text = f" Examples: {success_examples}." if success_examples else ""
        messages.success(
            request,
            (
                f"Bulk update done. Updated {success_count} order(s). "
                f"WhatsApp queued for {queued_count} order(s). "
                f"Stock adjusted for {stock_adjusted_count} SKU movement(s).{examples_text}"
            ),
        )
    if failed_count:
        details = " | ".join(failed_samples)
        if details:
            messages.warning(
                request,
                f"Skipped/failed {failed_count} order(s). Examples: {details}",
            )
        else:
            messages.warning(request, f"Skipped/failed {failed_count} order(s).")
    if missing_stock_skus:
        messages.warning(
            request,
            f"Stock mapping missing for SKU(s): {', '.join(sorted(missing_stock_skus))}.",
        )
    if not success_count and not failed_count:
        messages.info(request, "No updates were applied.")

    return redirect(redirect_url)


def project_list(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    return render(request, "core/project_list.html", {"projects": Project.objects.all()})


def project_detail(request, pk):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    project = get_object_or_404(Project, pk=pk)
    return render(request, "core/project_detail.html", {"project": project})


def order_detail(request, pk):
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    form = ShiprocketOrderManualUpdateForm(instance=order)
    can_edit_operations = _can_edit_operations(getattr(request, "user", None))
    can_update_order_status = _can_update_order_status(getattr(request, "user", None))
    ops_mobile_mode = _is_ops_viewer(getattr(request, "user", None))
    can_view_raw_payload = _is_ops_admin(getattr(request, "user", None))
    status_form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
    whatsapp_timeline = order.whatsapp_logs.order_by("-created_at")[:100]
    latest_queue_job = order.whatsapp_queue_jobs.order_by("-updated_at", "-created_at").first()
    activity_queryset = order.activity_logs.all()
    activity_event = (request.GET.get("activity_event") or "").strip()
    activity_result = (request.GET.get("activity_result") or "").strip().lower()
    activity_from = (request.GET.get("activity_from") or "").strip()
    activity_to = (request.GET.get("activity_to") or "").strip()

    valid_events = {value for value, _ in OrderActivityLog.EVENT_CHOICES}
    if activity_event in valid_events:
        activity_queryset = activity_queryset.filter(event_type=activity_event)

    if activity_result == "success":
        activity_queryset = activity_queryset.filter(is_success=True)
    elif activity_result == "failed":
        activity_queryset = activity_queryset.filter(is_success=False)

    parsed_from = parse_date(activity_from) if activity_from else None
    parsed_to = parse_date(activity_to) if activity_to else None
    if parsed_from:
        activity_queryset = activity_queryset.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        activity_queryset = activity_queryset.filter(created_at__date__lte=parsed_to)

    activity_timeline = activity_queryset.order_by("-created_at")[:200]
    ops_mobile_actions = _build_ops_viewer_detail_actions(order, status_form)
    packing_scan_summary = build_packing_scan_requirements(order)
    pack_action_available = any(
        action.get("value") == ShiprocketOrder.STATUS_PACKED
        for action in ops_mobile_actions
    )
    return render(
        request,
        "core/order_detail_ops.html" if ops_mobile_mode else "core/order_detail.html",
        {
            "order": order,
            "form": form,
            "status_form": status_form,
            "whatsapp_timeline": whatsapp_timeline,
            "latest_queue_job": latest_queue_job,
            "activity_timeline": activity_timeline,
            "activity_event_choices": OrderActivityLog.EVENT_CHOICES,
            "can_edit_operations": can_edit_operations,
            "can_update_order_status": can_update_order_status,
            "can_view_raw_payload": can_view_raw_payload,
            "ops_mobile_mode": ops_mobile_mode,
            "ops_mobile_stage_key": _ops_viewer_stage_key(order.local_status),
            "ops_mobile_actions": ops_mobile_actions,
            "ops_pack_action_available": pack_action_available,
            "packing_scan_requirements": packing_scan_summary["requirements"],
            "packing_scan_unmatched_items": packing_scan_summary["unmatched_items"],
            "packing_scan_missing_barcodes": packing_scan_summary["missing_barcodes"],
            "packing_scan_total_expected_quantity": packing_scan_summary["total_expected_quantity"],
            "can_print_packing_list": order.local_status in {
                ShiprocketOrder.STATUS_ACCEPTED,
                ShiprocketOrder.STATUS_PACKED,
            },
            "can_print_shipping_label": order.local_status == ShiprocketOrder.STATUS_PACKED,
            "return_tab": (request.GET.get("tab") or "").strip(),
            "activity_filters": {
                "event": activity_event if activity_event in valid_events else "",
                "result": activity_result if activity_result in {"success", "failed"} else "",
                "from": activity_from,
                "to": activity_to,
            },
        },
    )


def packing_list(request, pk):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return redirect("login")
    if not (_can_update_order_status(request.user) or _is_ops_admin(request.user)):
        messages.error(request, "Your role cannot access packing list.")
        return redirect("order_management")
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    sender = SenderAddress.get_default()
    context = {
        "order": order,
        "sender": sender,
    }
    return render(request, "core/packing_list.html", context)


def packing_queue(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    search_query = (request.GET.get("q") or "").strip()
    orders_query = ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_ACCEPTED).order_by(
        "-order_date",
        "-updated_at",
    )
    orders = list(orders_query)
    if search_query:
        needle = search_query.lower()
        filtered_orders = []
        for order in orders:
            shipping = order.display_shipping_address
            haystack = [
                order.shiprocket_order_id,
                order.channel_order_id,
                order.customer_name,
                order.customer_phone,
                order.manual_customer_name,
                order.manual_customer_phone,
                shipping.get("name"),
                shipping.get("phone"),
                shipping.get("pincode"),
            ]
            if any(needle in str(value).lower() for value in haystack if value):
                filtered_orders.append(order)
        orders = filtered_orders

    context = {
        "orders": orders,
        "search_query": search_query,
    }
    return render(request, "core/packing_queue.html", context)


def bulk_packing_lists(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    sender = SenderAddress.get_default()
    orders_query = ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_ACCEPTED).order_by(
        "-order_date",
        "-updated_at",
    )

    order_ids = request.GET.getlist("order_id")
    if order_ids:
        orders_query = orders_query.filter(pk__in=order_ids)

    orders = list(orders_query)
    context = {
        "orders": orders,
        "sender": sender,
    }
    return render(request, "core/bulk_packing_lists.html", context)


def shipping_label_4x6(request, pk):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return redirect("login")
    if not (_can_update_order_status(request.user) or _is_ops_admin(request.user)):
        messages.error(request, "Your role cannot access shipping label.")
        return redirect("order_management")
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    if order.local_status != ShiprocketOrder.STATUS_PACKED:
        messages.error(request, "Shipping label is available only for packed orders.")
        return redirect("order_detail", pk=order.pk)

    start_position = _resolve_start_position((request.GET.get("start_position") or "").strip())
    label_slots = [None] * 4
    label_slots[START_POSITION_FLOW.index(start_position)] = order
    sender = SenderAddress.get_default()
    return render(
        request,
        "core/shipping_label_4x6.html",
        {
            "order": order,
            "sender": sender,
            "start_position": start_position,
            "start_position_choices": START_POSITION_CHOICES,
            "label_slots": label_slots,
        },
    )


def bulk_shipping_labels_4x6(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    sender = SenderAddress.get_default()
    orders_query = ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_PACKED).order_by(
        "-order_date",
        "-updated_at",
    )
    selected_status_label = dict(ShiprocketOrder.STATUS_CHOICES)[ShiprocketOrder.STATUS_PACKED]

    order_ids = request.GET.getlist("order_id")
    if order_ids:
        orders_query = orders_query.filter(pk__in=order_ids)

    orders = list(orders_query)
    start_position = _resolve_start_position((request.GET.get("start_position") or "").strip())
    pages = _build_bulk_pages(orders, start_position)

    context = {
        "orders": orders,
        "pages": pages,
        "sender": sender,
        "selected_status_label": selected_status_label,
        "start_position": start_position,
        "start_position_choices": START_POSITION_CHOICES,
    }
    return render(request, "core/bulk_shipping_labels_4x6.html", context)


def print_queue(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    skip_printed = _is_truthy(request.GET.get("skip_printed"))
    ready_only = _is_truthy(request.GET.get("ready_only"))
    search_query = (request.GET.get("q") or "").strip()

    orders_query = ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_PACKED).order_by(
        "-order_date",
        "-updated_at",
    )
    if skip_printed:
        orders_query = orders_query.filter(label_print_count=0)

    orders = list(orders_query)
    if ready_only:
        orders = [order for order in orders if not order.missing_fields_for_packing()]
    if search_query:
        needle = search_query.lower()
        filtered_orders = []
        for order in orders:
            shipping = order.display_shipping_address
            haystack = [
                order.shiprocket_order_id,
                order.channel_order_id,
                order.customer_name,
                order.customer_phone,
                order.manual_customer_name,
                order.manual_customer_phone,
                shipping.get("name"),
                shipping.get("phone"),
                shipping.get("pincode"),
            ]
            if any(needle in str(value).lower() for value in haystack if value):
                filtered_orders.append(order)
        orders = filtered_orders

    start_position = _resolve_start_position((request.GET.get("start_position") or "").strip())
    context = {
        "orders": orders,
        "start_position": start_position,
        "start_position_choices": START_POSITION_CHOICES,
        "skip_printed": skip_printed,
        "ready_only": ready_only,
        "search_query": search_query,
    }
    return render(request, "core/print_queue.html", context)


@login_required
def sender_address(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    if request.method == "POST" and not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access for operational settings.")
        return redirect("sender_address")
    sender = SenderAddress.get_default()
    if request.method == "POST":
        form = SenderAddressForm(request.POST, instance=sender)
        if form.is_valid():
            form.save()
            messages.success(request, "Sender address saved.")
            return redirect("sender_address")
        messages.error(request, "Unable to save sender address. Check the form fields.")
    else:
        form = SenderAddressForm(instance=sender)
    return render(request, "core/sender_address.html", {"form": form})


@login_required
def stock_management(request):
    if not _can_manage_stock(request.user):
        messages.error(request, "Your role cannot access stock management.")
        return redirect("order_management")
    can_edit_operations = _can_manage_stock(request.user)
    ops_mobile_mode = _is_ops_viewer(getattr(request, "user", None))
    actor = _request_actor(request)
    search_query = str(request.GET.get("q") or "").strip()
    low_only = _is_truthy(request.GET.get("low"))
    selected_category_id = str(request.GET.get("category") or "").strip()
    active_view = str(request.GET.get("view") or "list").strip().lower()
    if active_view not in {"list", "manage", "more"}:
        active_view = "list"
    edit_product = None
    edit_pk = str(request.GET.get("edit") or "").strip()
    if edit_pk.isdigit():
        edit_product = Product.objects.filter(pk=int(edit_pk)).first()
        active_view = "manage"

    if request.method == "POST" and not can_edit_operations:
        messages.error(request, "Your role has read-only access for stock management.")
        return redirect("stock_management")

    product_form = ProductForm(instance=edit_product)
    stock_form = StockAdjustmentForm()
    mapping_form = BulkSmartbizMappingForm()

    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        return_view = str(request.POST.get("return_view") or "").strip().lower()
        return_query = str(request.POST.get("return_query") or "").strip()
        if return_view not in {"list", "manage", "more"}:
            return_view = ""
        redirect_url = reverse("stock_management")
        if return_view == "manage":
            redirect_url = f"{redirect_url}?view={return_view}"
        elif return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if action == "save_product":
            product_id = str(request.POST.get("product_id") or "").strip()
            instance = Product.objects.filter(pk=int(product_id)).first() if product_id.isdigit() else None
            product_form = ProductForm(request.POST, instance=instance)
            if product_form.is_valid():
                product = product_form.save()
                messages.success(request, f"Saved product {product.name} ({product.sku}).")
                return redirect(redirect_url)
            messages.error(request, "Unable to save product. Check the product fields.")
        elif action == "adjust_stock":
            stock_form = StockAdjustmentForm(request.POST)
            if stock_form.is_valid():
                lookup_value = stock_form.cleaned_data["lookup_value"]
                product = find_product_by_lookup(lookup_value)
                if not product:
                    messages.error(request, f"No product found for '{lookup_value}'.")
                else:
                    quantity = int(stock_form.cleaned_data["quantity"] or 0)
                    notes = stock_form.cleaned_data["notes"]
                    movement_action = stock_form.cleaned_data["action"]
                    if movement_action == StockAdjustmentForm.ACTION_SET:
                        movement, _ = set_manual_stock_quantity(
                            product=product,
                            target_quantity=quantity,
                            actor=actor,
                            notes=notes,
                        )
                        if movement:
                            messages.success(
                                request,
                                (
                                    f"Set stock for {product.name} ({product.sku}) to {movement.quantity_after}. "
                                    f"Previous stock was {movement.quantity_before}."
                                ),
                            )
                        else:
                            messages.info(
                                request,
                                f"Stock for {product.name} ({product.sku}) is already {quantity}. No change needed.",
                            )
                    else:
                        movement_type = (
                            StockMovement.TYPE_MANUAL_ADD
                            if movement_action == StockAdjustmentForm.ACTION_ADD
                            else StockMovement.TYPE_MANUAL_REMOVE
                        )
                        movement, _ = apply_manual_stock_movement(
                            product=product,
                            movement_type=movement_type,
                            quantity=quantity,
                            actor=actor,
                            notes=notes,
                        )
                        direction = "added to" if movement.quantity_delta >= 0 else "removed from"
                        messages.success(
                            request,
                            (
                                f"{abs(movement.quantity_delta)} unit(s) {direction} {product.name} ({product.sku}). "
                                f"Stock is now {movement.quantity_after}."
                            ),
                        )
                    return redirect(redirect_url)
            messages.error(request, "Unable to adjust stock. Check the stock form fields.")
        elif action == "bulk_map_smartbiz":
            mapping_form = BulkSmartbizMappingForm(request.POST)
            if mapping_form.is_valid():
                updated_count = 0
                missing_skus = []
                duplicate_ids = []
                for row in mapping_form.parse_rows():
                    sku = str(row["sku"] or "").strip().upper()
                    smartbiz_product_id = str(row["smartbiz_product_id"] or "").strip()
                    product = Product.objects.filter(sku=sku).first()
                    if not product:
                        missing_skus.append(sku)
                        continue
                    conflict = (
                        Product.objects.exclude(pk=product.pk)
                        .filter(smartbiz_product_id__iexact=smartbiz_product_id)
                        .first()
                    )
                    if conflict:
                        duplicate_ids.append(f"{smartbiz_product_id} -> {conflict.sku}")
                        continue
                    if product.smartbiz_product_id != smartbiz_product_id:
                        product.smartbiz_product_id = smartbiz_product_id
                        product.save(update_fields=["smartbiz_product_id", "updated_at"])
                        updated_count += 1
                if updated_count:
                    messages.success(request, f"Updated SmartBiz mapping for {updated_count} product(s).")
                if missing_skus:
                    messages.warning(request, f"SKU not found: {', '.join(missing_skus)}.")
                if duplicate_ids:
                    messages.warning(
                        request,
                        f"SmartBiz ID already mapped to another product: {', '.join(duplicate_ids)}.",
                    )
                if not updated_count and not missing_skus and not duplicate_ids:
                    messages.info(request, "No SmartBiz mappings needed updating.")
                return redirect(redirect_url)
            messages.error(request, "Unable to apply bulk SmartBiz mappings. Check the pasted rows.")
        elif action == "reconcile_stock":
            summary = reconcile_missed_stock_deductions(actor=actor)
            if summary["movement_count"]:
                messages.success(
                    request,
                    (
                        f"Reconciled missing stock deductions for {summary['orders_changed']} order(s). "
                        f"Created {summary['movement_count']} stock movement(s)."
                    ),
                )
            else:
                messages.info(
                    request,
                    f"No missing stock deductions were found across {summary['orders_scanned']} eligible order(s).",
                )
            if summary["missing_skus"]:
                messages.warning(
                    request,
                    "Stock reconciliation still has unmapped item identifier(s): "
                    + ", ".join(summary["missing_skus"])
                    + ".",
                )
            return redirect(redirect_url)
        else:
            messages.error(request, "Invalid stock action.")
            return redirect(redirect_url)

    products = Product.objects.all().order_by("name", "sku")
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(category_master__name__icontains=search_query)
            | Q(sku__icontains=search_query)
            | Q(barcode__icontains=search_query)
            | Q(smartbiz_product_id__icontains=search_query)
        )
    if selected_category_id.isdigit():
        products = products.filter(category_master_id=int(selected_category_id))
    if low_only:
        products = products.filter(stock_quantity__lte=F("reorder_level"))

    low_stock_products = (
        Product.objects.filter(is_active=True, stock_quantity__lte=F("reorder_level"))
        .order_by("stock_quantity", "name")[:10]
    )
    recent_movements = StockMovement.objects.select_related("product", "order").order_by("-created_at")[:25]
    product_categories = ProductCategory.objects.filter(is_active=True).order_by("name")

    template_name = "core/stock_management_ops.html" if ops_mobile_mode else "core/stock_management.html"
    return render(
        request,
        template_name,
        {
            "product_form": product_form,
            "stock_form": stock_form,
            "mapping_form": mapping_form,
            "products": products[:200],
            "low_stock_products": low_stock_products,
            "recent_movements": recent_movements,
            "search_query": search_query,
            "low_only": low_only,
            "selected_category_id": selected_category_id,
            "product_categories": product_categories,
            "current_query_string": request.GET.urlencode(),
            "active_view": active_view,
            "editing_product": edit_product,
            "can_edit_operations": can_edit_operations,
            "ops_mobile_mode": ops_mobile_mode,
        },
    )


@login_required
def product_categories(request):
    if not _is_ops_admin(request.user):
        messages.error(request, "Your role cannot access product categories.")
        return redirect("order_management")

    edit_category = None
    edit_pk = str(request.GET.get("edit") or "").strip()
    if edit_pk.isdigit():
        edit_category = ProductCategory.objects.filter(pk=int(edit_pk)).first()

    form = ProductCategoryForm(instance=edit_category)

    if request.method == "POST":
        action = str(request.POST.get("form_action") or "").strip()
        if action != "save_category":
            messages.error(request, "Invalid category action.")
            return redirect("product_categories")

        category_id = str(request.POST.get("category_id") or "").strip()
        instance = ProductCategory.objects.filter(pk=int(category_id)).first() if category_id.isdigit() else None
        form = ProductCategoryForm(request.POST, instance=instance)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Saved product category {category.name}.")
            return redirect("product_categories")
        messages.error(request, "Unable to save product category. Check the form fields.")

    categories = ProductCategory.objects.annotate(product_count=Count("products")).order_by("name")
    active_count = categories.filter(is_active=True).count()

    return render(
        request,
        "core/product_categories.html",
        {
            "form": form,
            "editing_category": edit_category,
            "categories": categories,
            "active_count": active_count,
            "total_count": categories.count(),
        },
    )


@login_required
def whatsapp_settings(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    can_edit_operations = _can_edit_operations(request.user)
    webhook_token_configured = bool(str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip())
    settings_row = WhatsAppSettings.get_default()
    templates = WhatsAppTemplate.objects.all()[:100]
    template_placeholder_map = {}
    for item in templates:
        if item.name not in template_placeholder_map:
            template_placeholder_map[item.name] = _extract_template_placeholders(item)
    template_choices = [
        (
            item.name,
            f"{item.name} ({item.language})" if item.language else item.name,
        )
        for item in templates
    ]

    def _config_overrides_from_form(cleaned_data):
        return {
            "enabled": cleaned_data.get("enabled"),
            "base_url": cleaned_data.get("api_base_url"),
            "api_key": cleaned_data.get("api_key"),
        }

    def _config_overrides_from_saved():
        return {
            "enabled": settings_row.enabled,
            "base_url": settings_row.api_base_url,
            "api_key": settings_row.api_key,
        }

    settings_form = WhatsAppApiSettingsForm(instance=settings_row, prefix="settings")
    message_form = WhatsAppMessageTestForm(
        instance=settings_row,
        prefix="message",
        template_choices=template_choices,
    )
    diagnostics = _build_whatsapp_diagnostics()

    if request.method == "POST":
        if not can_edit_operations:
            messages.error(request, "Your role has read-only access for WhatsApp settings.")
            return redirect("whatsapp_settings")
        action = (request.POST.get("action") or "").strip()

        if action == "send_alert_test":
            worker_name = f"ui:{_request_actor(request) or 'manual'}"
            result = send_queue_alert_test(worker_name=worker_name)
            write_system_heartbeat(
                "queue_alerts",
                metadata={
                    "worker": worker_name,
                    "status": str(result.get("status") or ""),
                    "email_sent": int(result.get("email_sent") or 0),
                    "whatsapp_sent": int(result.get("whatsapp_sent") or 0),
                },
            )
            if result.get("status") == "sent":
                messages.success(
                    request,
                    (
                        "Queue alert test sent. "
                        f"Email: {int(result.get('email_sent') or 0)} "
                        f"WhatsApp: {int(result.get('whatsapp_sent') or 0)}."
                    ),
                )
            elif result.get("status") == "no_targets":
                messages.warning(request, "Queue alert test skipped: configure alert targets first.")
            else:
                messages.error(request, f"Queue alert test failed: {result.get('message') or 'Unknown error'}")
            return redirect("whatsapp_settings")

        if action == "process_queue_once":
            summary = process_whatsapp_notification_queue(
                limit=max(1, int(request.POST.get("limit") or 20)),
                worker_name=f"ui_settings:{_request_actor(request) or 'manual'}",
                include_not_due=bool(_is_truthy(request.POST.get("include_not_due"))),
            )
            write_system_heartbeat(
                "queue_worker",
                metadata={
                    "worker": summary["worker"],
                    "picked": int(summary.get("picked", 0)),
                    "processed": int(summary.get("processed", 0)),
                    "success": int(summary.get("success", 0)),
                    "retried": int(summary.get("retried", 0)),
                    "failed": int(summary.get("failed", 0)),
                },
            )
            messages.success(
                request,
                (
                    f"Queue processed. picked={summary['picked']} processed={summary['processed']} "
                    f"success={summary['success']} retried={summary['retried']} failed={summary['failed']}."
                ),
            )
            return redirect("whatsapp_settings")

        if action == "export_incident_snapshot":
            output = StringIO()
            call_command("export_incident_snapshot", "--hours", "24", "--limit", "100", stdout=output)
            lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
            tail = lines[-1] if lines else "Incident snapshot exported."
            messages.success(request, tail)
            return redirect("whatsapp_settings")

        if action in {"save_settings", "check_connection", "sync_templates", "send_webhook_test"}:
            settings_form = WhatsAppApiSettingsForm(request.POST, instance=settings_row, prefix="settings")
            message_form = WhatsAppMessageTestForm(
                instance=settings_row,
                prefix="message",
                template_choices=template_choices,
            )
            if settings_form.is_valid():
                settings_form.save()
                overrides = _config_overrides_from_form(settings_form.cleaned_data)

                if action == "save_settings":
                    messages.success(request, "WhatsApp settings saved.")
                    return redirect("whatsapp_settings")

                if action == "check_connection":
                    try:
                        check_api_connection(config_overrides=overrides)
                    except WhatomateNotificationError as exc:
                        messages.error(request, f"Connection failed: {exc}")
                    else:
                        messages.success(request, "WhatsApp API connection successful.")
                    return redirect("whatsapp_settings")

                if action == "sync_templates":
                    try:
                        sync_result = sync_templates_from_api(config_overrides=overrides)
                    except WhatomateNotificationError as exc:
                        messages.error(request, f"Template sync failed: {exc}")
                    else:
                        messages.success(request, f"Templates synced: {sync_result.get('synced_count', 0)}")
                    return redirect("whatsapp_settings")

                if action == "send_webhook_test":
                    sample_payload = _build_webhook_test_payload()
                    result = _send_internal_webhook_test(sample_payload, host=request.get_host())
                    status_code = int(result.get("status_code") or 0)
                    parsed_payload = result.get("payload") if isinstance(result, dict) else {}
                    if status_code == 200 and isinstance(parsed_payload, dict) and parsed_payload.get("ok"):
                        mapped_order = str(parsed_payload.get("mapped_order_id") or "").strip()
                        event_id = str(parsed_payload.get("webhook_event_id") or sample_payload.get("event_id") or "").strip()
                        messages.success(
                            request,
                            (
                                "Webhook test delivered successfully. "
                                f"Event: {event_id or '-'} "
                                f"Mapped Order: {mapped_order or 'none'}."
                            ),
                        )
                    else:
                        error_payload = parsed_payload if parsed_payload else (result.get("text") or "{}")
                        messages.error(
                            request,
                            f"Webhook test failed (HTTP {status_code}). Response: {error_payload}",
                        )
                    return redirect("whatsapp_settings")

            messages.error(request, "Unable to save WhatsApp settings. Check the settings form fields.")

        elif action in {"send_test_message", "send_test_template"}:
            settings_form = WhatsAppApiSettingsForm(instance=settings_row, prefix="settings")
            message_form = WhatsAppMessageTestForm(
                request.POST,
                instance=settings_row,
                prefix="message",
                template_choices=template_choices,
            )
            if message_form.is_valid():
                saved_settings = message_form.save()
                overrides = _config_overrides_from_saved()

                if action == "send_test_message":
                    test_phone = (saved_settings.test_phone_number or "").strip()
                    test_message = (saved_settings.test_message_text or "").strip()
                    try:
                        send_result = send_test_whatsapp_message(
                            phone_number=test_phone,
                            message_text=test_message,
                            config_overrides=overrides,
                        )
                    except WhatomateNotificationError as exc:
                        _create_whatsapp_log(
                            trigger=WhatsAppNotificationLog.TRIGGER_TEST_MESSAGE,
                            request=request,
                            result={"phone_number": test_phone, "mode": "text"},
                            is_success=False,
                            error_message=str(exc),
                        )
                        messages.error(request, f"Test message failed: {exc}")
                    else:
                        _create_whatsapp_log(
                            trigger=WhatsAppNotificationLog.TRIGGER_TEST_MESSAGE,
                            request=request,
                            result=send_result,
                            is_success=True,
                        )
                        messages.success(
                            request,
                            f"Test message sent to {send_result.get('phone_number', test_phone)}.",
                        )
                    return redirect("whatsapp_settings")

                if action == "send_test_template":
                    test_phone = (saved_settings.test_phone_number or "").strip()
                    template_name = (saved_settings.test_template_name or "").strip()
                    template_params = (saved_settings.test_template_params or "").strip()
                    try:
                        send_result = send_test_template_message(
                            phone_number=test_phone,
                            template_name=template_name,
                            template_params=template_params,
                            config_overrides=overrides,
                        )
                    except WhatomateNotificationError as exc:
                        _create_whatsapp_log(
                            trigger=WhatsAppNotificationLog.TRIGGER_TEST_TEMPLATE,
                            request=request,
                            result={
                                "phone_number": test_phone,
                                "mode": "template",
                                "template_name": template_name,
                            },
                            is_success=False,
                            error_message=str(exc),
                        )
                        messages.error(request, f"Template test failed: {exc}")
                    else:
                        _create_whatsapp_log(
                            trigger=WhatsAppNotificationLog.TRIGGER_TEST_TEMPLATE,
                            request=request,
                            result=send_result,
                            is_success=True,
                        )
                        messages.success(
                            request,
                            (
                                "Template test message sent to "
                                f"{send_result.get('phone_number', test_phone)} "
                                f"using {send_result.get('template_name', template_name)}."
                            ),
                        )
                    return redirect("whatsapp_settings")

            messages.error(request, "Unable to send test message. Check the template message form fields.")
        else:
            messages.error(request, "Invalid action.")
            return redirect("whatsapp_settings")

    return render(
        request,
        "core/whatsapp_settings.html",
        {
            "settings_form": settings_form,
            "message_form": message_form,
            "templates": templates,
            "template_placeholder_map": template_placeholder_map,
            "can_edit_operations": can_edit_operations,
            "webhook_token_configured": webhook_token_configured,
            "diagnostics": diagnostics,
        },
    )


def _filtered_whatsapp_notification_logs(result_filter, trigger_filter):
    logs = WhatsAppNotificationLog.objects.select_related("order").all()
    if result_filter == "success":
        logs = logs.filter(is_success=True)
    elif result_filter == "failed":
        logs = logs.filter(is_success=False)

    valid_triggers = {value for value, _ in WhatsAppNotificationLog.TRIGGER_CHOICES}
    if trigger_filter in valid_triggers:
        logs = logs.filter(trigger=trigger_filter)
    return logs


@login_required
def whatsapp_delivery_logs(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    can_view_raw_payload = _is_ops_admin(request.user)
    result_filter = (request.GET.get("result") or "").strip().lower()
    trigger_filter = (request.GET.get("trigger") or "").strip()

    logs = _filtered_whatsapp_notification_logs(result_filter=result_filter, trigger_filter=trigger_filter)

    context = {
        "logs": logs[:300],
        "result_filter": result_filter,
        "trigger_filter": trigger_filter,
        "trigger_choices": WhatsAppNotificationLog.TRIGGER_CHOICES,
        "can_view_raw_payload": can_view_raw_payload,
    }
    return render(request, "core/whatsapp_delivery_logs.html", context)


@login_required
def webhook_diagnostics(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    diagnostics = _build_webhook_diagnostics()
    can_view_raw_payload = _is_ops_admin(request.user)
    return render(
        request,
        "core/webhook_diagnostics.html",
        {
            "diagnostics": diagnostics,
            "can_view_raw_payload": can_view_raw_payload,
        },
    )


@login_required
def admin_utilities(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    if request.method == "POST" and not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot run admin utilities.")
        return redirect("admin_utilities")

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()
        actor = _request_actor(request) or "manual"

        if action == "process_queue_once":
            summary = process_whatsapp_notification_queue(
                limit=max(1, int(request.POST.get("limit") or 20)),
                worker_name=f"admin_utilities:{actor}",
                include_not_due=bool(_is_truthy(request.POST.get("include_not_due"))),
            )
            write_system_heartbeat(
                "queue_worker",
                metadata={
                    "worker": summary["worker"],
                    "picked": int(summary.get("picked", 0)),
                    "processed": int(summary.get("processed", 0)),
                    "success": int(summary.get("success", 0)),
                    "retried": int(summary.get("retried", 0)),
                    "failed": int(summary.get("failed", 0)),
                },
            )
            messages.success(
                request,
                (
                    f"Queue processed. picked={summary['picked']} processed={summary['processed']} "
                    f"success={summary['success']} retried={summary['retried']} failed={summary['failed']}."
                ),
            )
            return redirect("admin_utilities")

        if action == "export_incident_snapshot":
            output = StringIO()
            call_command("export_incident_snapshot", "--hours", "24", "--limit", "100", stdout=output)
            lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
            messages.success(request, lines[-1] if lines else "Incident snapshot exported.")
            return redirect("admin_utilities")

        if action == "cleanup_runtime_dry_run":
            output = StringIO()
            call_command("cleanup_runtime_files", "--dry-run", stdout=output)
            lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
            messages.info(request, " | ".join(lines[-5:]) if lines else "Dry-run complete.")
            return redirect("admin_utilities")

        if action == "clear_demo_data":
            deleted = _delete_demo_data()
            messages.success(
                request,
                (
                    f"Demo data cleared. orders={deleted['orders']} activity_logs={deleted['activity_logs']} "
                    f"queue_jobs={deleted['queue_jobs']} whatsapp_logs={deleted['whatsapp_logs']}."
                ),
            )
            return redirect("admin_utilities")

        if action == "send_webhook_test":
            sample_payload = _build_webhook_test_payload()
            result = _send_internal_webhook_test(sample_payload, host=request.get_host())
            status_code = int(result.get("status_code") or 0)
            parsed_payload = result.get("payload") if isinstance(result, dict) else {}
            if status_code == 200 and isinstance(parsed_payload, dict) and parsed_payload.get("ok"):
                messages.success(
                    request,
                    (
                        "Webhook test delivered successfully. "
                        f"Event: {parsed_payload.get('webhook_event_id') or sample_payload.get('event_id') or '-'}."
                    ),
                )
            else:
                messages.error(request, f"Webhook test failed (HTTP {status_code}).")
            return redirect("admin_utilities")

        messages.error(request, "Invalid admin utility action.")
        return redirect("admin_utilities")

    diagnostics = _build_whatsapp_diagnostics()
    webhook_diagnostics_context = _build_webhook_diagnostics()
    demo_counts = {
        "orders": ShiprocketOrder.objects.filter(shiprocket_order_id__startswith="DEMO-").count(),
        "activity_logs": OrderActivityLog.objects.filter(shiprocket_order_id__startswith="DEMO-").count(),
        "queue_jobs": WhatsAppNotificationQueue.objects.filter(shiprocket_order_id__startswith="DEMO-").count(),
        "whatsapp_logs": WhatsAppNotificationLog.objects.filter(shiprocket_order_id__startswith="DEMO-").count(),
    }
    return render(
        request,
        "core/admin_utilities.html",
        {
            "diagnostics": diagnostics,
            "webhook_diagnostics": webhook_diagnostics_context,
            "demo_counts": demo_counts,
            "can_edit_operations": _can_edit_operations(request.user),
        },
    )


@login_required
def whatsapp_delivery_logs_csv(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    can_view_raw_payload = _is_ops_admin(request.user)
    result_filter = (request.GET.get("result") or "").strip().lower()
    trigger_filter = (request.GET.get("trigger") or "").strip()
    logs = _filtered_whatsapp_notification_logs(result_filter=result_filter, trigger_filter=trigger_filter)[:1000]

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="whatsapp_delivery_logs_{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "time",
            "order_id",
            "trigger",
            "previous_status",
            "current_status",
            "phone_number",
            "mode",
            "template_name",
            "template_id",
            "delivery_status",
            "message_id",
            "result",
            "error_message",
            "webhook_event_id",
            "request_payload",
            "response_payload",
        ]
    )

    for log in logs:
        delivery_status = str(log.delivery_status or "").strip()
        if not delivery_status and log.is_success and log.trigger != WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS:
            delivery_status = "sent"
        if can_view_raw_payload:
            request_payload = json.dumps(log.request_payload or {}, ensure_ascii=True)
            response_payload = json.dumps(log.response_payload or {}, ensure_ascii=True)
        else:
            request_payload = ""
            response_payload = ""
        writer.writerow(
            [
                timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S %Z") if log.created_at else "",
                str(log.shiprocket_order_id or "").strip(),
                str(log.trigger or "").strip(),
                str(log.previous_status or "").strip(),
                str(log.current_status or "").strip(),
                str(log.phone_number or "").strip(),
                str(log.mode or "").strip(),
                str(log.template_name or "").strip(),
                str(log.template_id or "").strip(),
                delivery_status,
                str(log.external_message_id or "").strip(),
                "success" if log.is_success else "failed",
                str(log.error_message or "").strip(),
                str(log.webhook_event_id or "").strip(),
                request_payload,
                response_payload,
            ]
        )
    return response


@login_required
def audit_export_csv(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    from_date_raw = (request.GET.get("from_date") or "").strip()
    to_date_raw = (request.GET.get("to_date") or "").strip()
    from_date = parse_date(from_date_raw) if from_date_raw else None
    to_date = parse_date(to_date_raw) if to_date_raw else None

    activity_logs = OrderActivityLog.objects.filter(
        event_type__in=[
            OrderActivityLog.EVENT_STATUS_CHANGE,
            OrderActivityLog.EVENT_MANUAL_UPDATE,
        ]
    )
    resend_logs = WhatsAppNotificationLog.objects.filter(trigger=WhatsAppNotificationLog.TRIGGER_RESEND)
    if from_date:
        activity_logs = activity_logs.filter(created_at__date__gte=from_date)
        resend_logs = resend_logs.filter(created_at__date__gte=from_date)
    if to_date:
        activity_logs = activity_logs.filter(created_at__date__lte=to_date)
        resend_logs = resend_logs.filter(created_at__date__lte=to_date)

    rows = []
    for log in activity_logs.select_related("order")[:3000]:
        rows.append(
            {
                "created_at": log.created_at,
                "source": "order_activity",
                "order_id": str(log.shiprocket_order_id or ""),
                "event_type": str(log.event_type or ""),
                "trigger": "",
                "result": "success" if log.is_success else "failed",
                "previous_status": str(log.previous_status or ""),
                "current_status": str(log.current_status or ""),
                "phone_number": "",
                "message_id": "",
                "actor": str(log.triggered_by or ""),
                "description": str(log.description or ""),
                "metadata": json.dumps(log.metadata or {}, ensure_ascii=True),
            }
        )
    for log in resend_logs.select_related("order")[:3000]:
        rows.append(
            {
                "created_at": log.created_at,
                "source": "whatsapp_log",
                "order_id": str(log.shiprocket_order_id or ""),
                "event_type": "",
                "trigger": str(log.trigger or ""),
                "result": "success" if log.is_success else "failed",
                "previous_status": str(log.previous_status or ""),
                "current_status": str(log.current_status or ""),
                "phone_number": str(log.phone_number or ""),
                "message_id": str(log.external_message_id or ""),
                "actor": str(log.triggered_by or ""),
                "description": str(log.error_message or ""),
                "metadata": json.dumps(
                    {
                        "delivery_status": str(log.delivery_status or ""),
                        "template_name": str(log.template_name or ""),
                        "template_id": str(log.template_id or ""),
                        "webhook_event_id": str(log.webhook_event_id or ""),
                    },
                    ensure_ascii=True,
                ),
            }
        )

    rows.sort(key=lambda item: item.get("created_at") or timezone.now(), reverse=True)
    rows = rows[:5000]

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="audit_export_{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "time",
            "source",
            "order_id",
            "event_type",
            "trigger",
            "result",
            "previous_status",
            "current_status",
            "phone_number",
            "message_id",
            "actor",
            "description",
            "metadata",
        ]
    )
    for row in rows:
        created_at = row.get("created_at")
        writer.writerow(
            [
                timezone.localtime(created_at).strftime("%Y-%m-%d %H:%M:%S %Z") if created_at else "",
                row.get("source", ""),
                row.get("order_id", ""),
                row.get("event_type", ""),
                row.get("trigger", ""),
                row.get("result", ""),
                row.get("previous_status", ""),
                row.get("current_status", ""),
                row.get("phone_number", ""),
                row.get("message_id", ""),
                row.get("actor", ""),
                row.get("description", ""),
                row.get("metadata", ""),
            ]
        )
    return response


@require_http_methods(["GET"])
def metrics(request):
    if not _is_metrics_authorized(request):
        return HttpResponse("unauthorized\n", status=401, content_type="text/plain; charset=utf-8")

    health_payload = build_health_payload()
    counters = get_operational_counters()
    last_webhook = counters["last_webhook_received_at"]
    last_webhook_unix = int(last_webhook.timestamp()) if last_webhook else 0
    freshness_minutes = counters["webhook_freshness_minutes"]
    freshness_value = freshness_minutes if freshness_minutes is not None else -1
    lines = [
        "# TYPE mathukai_health_ok gauge",
        f"mathukai_health_ok {1 if health_payload.get('ok') else 0}",
        "# TYPE mathukai_queue_failed gauge",
        f"mathukai_queue_failed {int(counters['failed_queue_count'])}",
        "# TYPE mathukai_queue_pending gauge",
        f"mathukai_queue_pending {int(counters['pending_queue_count'])}",
        "# TYPE mathukai_whatsapp_today_sent gauge",
        f"mathukai_whatsapp_today_sent {int(counters['today_whatsapp_sent_count'])}",
        "# TYPE mathukai_whatsapp_today_failed gauge",
        f"mathukai_whatsapp_today_failed {int(counters['today_whatsapp_failed_count'])}",
        "# TYPE mathukai_whatsapp_today_retried gauge",
        f"mathukai_whatsapp_today_retried {int(counters['today_whatsapp_retried_count'])}",
        "# TYPE mathukai_webhook_last_received_unixtime gauge",
        f"mathukai_webhook_last_received_unixtime {last_webhook_unix}",
        "# TYPE mathukai_webhook_freshness_minutes gauge",
        f"mathukai_webhook_freshness_minutes {freshness_value}",
        "# TYPE mathukai_webhook_is_stale gauge",
        f"mathukai_webhook_is_stale {1 if counters['webhook_is_stale'] else 0}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4; charset=utf-8")


@require_http_methods(["GET"])
def healthz(request):
    payload = build_health_payload()
    return JsonResponse(payload, status=200 if payload.get("ok") else 503)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatomate_webhook(request):
    if request.method == "GET":
        return JsonResponse({"ok": True, "detail": "Whatomate webhook endpoint"})

    raw_body = request.body or b"{}"
    if not _is_webhook_authorized(request, raw_body=raw_body):
        return JsonResponse({"ok": False, "error": "Unauthorized webhook token."}, status=401)

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Payload must be a JSON object."}, status=400)

    webhook_event_id = _first_text_value(
        payload,
        [("event_id",), ("id",), ("data", "event_id"), ("data", "id")],
    )
    if webhook_event_id:
        duplicate = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            webhook_event_id=webhook_event_id,
        ).exists()
        if duplicate:
            return JsonResponse({"ok": True, "duplicate": True})

    event_type = _first_text_value(
        payload,
        [("event_type",), ("event",), ("type",), ("data", "event_type"), ("data", "event"), ("data", "type")],
    )
    delivery_status = _first_text_value(
        payload,
        [
            ("delivery_status",),
            ("message_status",),
            ("status",),
            ("data", "delivery_status"),
            ("data", "message_status"),
            ("data", "status"),
            ("message", "status"),
            ("data", "message", "status"),
        ],
    ).lower()
    if not delivery_status and event_type:
        delivery_status = str(event_type).strip().lower()

    external_message_id = _first_text_value(
        payload,
        [
            ("message_id",),
            ("data", "message_id"),
            ("message", "id"),
            ("data", "message", "id"),
            ("data", "message", "message_id"),
        ],
    )
    template_name = _first_text_value(
        payload,
        [
            ("template_name",),
            ("data", "template_name"),
            ("template", "name"),
            ("data", "template", "name"),
        ],
    )
    order_id_text = _first_text_value(
        payload,
        [
            ("order_id",),
            ("shiprocket_order_id",),
            ("data", "order_id"),
            ("data", "shiprocket_order_id"),
            ("metadata", "order_id"),
            ("context", "order_id"),
        ],
    )
    idempotency_key = _first_text_value(
        payload,
        [
            ("idempotency_key",),
            ("metadata", "idempotency_key"),
            ("context", "idempotency_key"),
            ("data", "idempotency_key"),
        ],
    )
    raw_phone = _first_text_value(
        payload,
        [
            ("phone_number",),
            ("phone",),
            ("data", "phone_number"),
            ("data", "phone"),
            ("contact", "phone_number"),
            ("data", "contact", "phone_number"),
            ("message", "to"),
            ("data", "message", "to"),
        ],
    )
    normalized_phone = _normalize_webhook_phone(raw_phone)

    order = _resolve_order_for_webhook(order_id_text, normalized_phone, idempotency_key)
    success_statuses = {"queued", "sent", "delivered", "read"}
    failure_statuses = {"failed", "undelivered", "error", "rejected"}
    if delivery_status in success_statuses:
        is_success = True
    elif delivery_status in failure_statuses:
        is_success = False
    else:
        is_success = True

    _create_whatsapp_log(
        trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
        request=request,
        order=order,
        previous_status=order.local_status if order else "",
        current_status=delivery_status or (order.local_status if order else ""),
        result={
            "phone_number": normalized_phone,
            "mode": "template" if template_name else "",
            "template_name": template_name,
            "external_message_id": external_message_id,
            "delivery_status": delivery_status,
            "webhook_event_id": webhook_event_id,
            "idempotency_key": idempotency_key,
            "response_payload": payload,
        },
        is_success=is_success,
        error_message="" if is_success else f"Webhook reported status: {delivery_status or 'unknown'}",
    )
    log_order_activity(
        order=order,
        shiprocket_order_id=order.shiprocket_order_id if order else order_id_text,
        event_type=OrderActivityLog.EVENT_WHATSAPP_WEBHOOK,
        title="WhatsApp webhook update received",
        description=f"Delivery status: {delivery_status or 'unknown'}",
        previous_status=order.local_status if order else "",
        current_status=delivery_status or (order.local_status if order else ""),
        metadata={
            "webhook_event_id": webhook_event_id,
            "external_message_id": external_message_id,
            "template_name": template_name,
            "phone_number": normalized_phone,
        },
        is_success=is_success,
        triggered_by="whatomate_webhook",
    )

    return JsonResponse(
        {
            "ok": True,
            "mapped_order_id": order.shiprocket_order_id if order else "",
            "delivery_status": delivery_status,
            "phone_number": normalized_phone,
            "webhook_event_id": webhook_event_id,
        }
    )


@login_required
def order_notification_config(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    can_edit_operations = _can_edit_operations(request.user)
    template_rows = list(WhatsAppTemplate.objects.all()[:200])
    template_choices = [
        (row.name, f"{row.name} ({row.language})" if row.language else row.name)
        for row in template_rows
    ]
    template_placeholder_map = {}
    template_preview_text_map = {}
    for template in template_rows:
        placeholders = _extract_template_placeholders(template)
        preview_text = _extract_template_preview_text(template)
        if not placeholders:
            placeholders = []
        existing = template_placeholder_map.get(template.name, [])
        for token in placeholders:
            if token not in existing:
                existing.append(token)
        template_placeholder_map[template.name] = existing
        if preview_text:
            existing_preview = str(template_preview_text_map.get(template.name) or "").strip()
            if not existing_preview:
                template_preview_text_map[template.name] = preview_text
            elif preview_text not in existing_preview:
                template_preview_text_map[template.name] = f"{existing_preview}\n{preview_text}".strip()
    status_rows = []
    status_sample_context_map = {}
    status_sample_info_map = {}

    for status_key, status_label in ShiprocketOrder.STATUS_CHOICES:
        sample_order = (
            ShiprocketOrder.objects.filter(local_status=status_key)
            .order_by("-order_date", "-updated_at")
            .first()
        )
        if sample_order:
            status_sample_context_map[status_key] = build_order_template_context(sample_order)
            status_sample_info_map[status_key] = {
                "has_real_order": True,
                "order_id": sample_order.shiprocket_order_id,
                "customer_name": sample_order.display_shipping_address.get("name") or sample_order.customer_name or "",
            }
        else:
            status_sample_context_map[status_key] = _default_preview_context(status_key, status_label)
            status_sample_info_map[status_key] = {"has_real_order": False, "order_id": "", "customer_name": ""}

    if request.method == "POST":
        if not can_edit_operations:
            messages.error(request, "Your role has read-only access for notification configuration.")
            return redirect("order_notification_config")
        has_error = False
        for status_key, status_label in ShiprocketOrder.STATUS_CHOICES:
            config, _ = WhatsAppStatusTemplateConfig.get_or_create_for_status(status_key)
            form = WhatsAppStatusTemplateConfigForm(
                request.POST,
                instance=config,
                prefix=f"status-{status_key}",
                template_choices=template_choices,
            )
            if form.is_valid():
                form.instance.local_status = status_key
                form.save()
            else:
                has_error = True
            status_rows.append({"status_key": status_key, "status_label": status_label, "form": form})

        if has_error:
            messages.error(request, "Unable to save some status template configurations. Please review the form.")
        else:
            messages.success(request, "Order status notification templates saved.")
            return redirect("order_notification_config")
    else:
        for status_key, status_label in ShiprocketOrder.STATUS_CHOICES:
            config, _ = WhatsAppStatusTemplateConfig.get_or_create_for_status(status_key)
            form = WhatsAppStatusTemplateConfigForm(
                instance=config,
                prefix=f"status-{status_key}",
                template_choices=template_choices,
            )
            status_rows.append({"status_key": status_key, "status_label": status_label, "form": form})

    return render(
        request,
        "core/order_notification_config.html",
        {
            "status_rows": status_rows,
            "template_count": len(template_rows),
            "template_placeholder_map": template_placeholder_map,
            "template_preview_text_map": template_preview_text_map,
            "status_sample_context_map": status_sample_context_map,
            "status_sample_info_map": status_sample_info_map,
            "order_field_choices": ORDER_TEMPLATE_FIELD_CHOICES,
            "can_edit_operations": can_edit_operations,
        },
    )


def contact(request):
    restricted_response = _redirect_ops_viewer_to_order_management(request)
    if restricted_response:
        return restricted_response
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Your message was submitted successfully.")
            return redirect("contact")
    else:
        form = ContactForm()

    return render(request, "core/contact.html", {"form": form})


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your account has been created.")
            return redirect("home")
    else:
        form = SignUpForm()

    return render(request, "registration/signup.html", {"form": form})


@login_required
def sync_shiprocket_orders(request):
    redirect_url = _resolve_ops_redirect(request, default_name="home")
    if not _can_sync_orders(request.user):
        messages.error(request, "Your role cannot run Shiprocket sync.")
        return redirect(redirect_url)
    if request.method != "POST":
        return redirect(redirect_url)

    try:
        synced = sync_orders()
    except ShiprocketAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Shiprocket sync completed. {synced} orders refreshed.")

    return redirect(redirect_url)


@login_required
def update_shiprocket_order(request, pk):
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    if request.method != "POST":
        return redirect("order_detail", pk=pk)
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot edit orders.")
        return redirect("order_detail", pk=pk)
    if order.is_manual_edit_locked:
        messages.error(request, "Manual shipping edits are locked after the order reaches shipped.")
        return redirect("order_detail", pk=pk)

    form = ShiprocketOrderManualUpdateForm(request.POST, instance=order)
    if form.is_valid():
        changed_fields = list(form.changed_data)
        form.save()
        if changed_fields:
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
                title="Manual shipping/contact details updated",
                description=f"Updated fields: {', '.join(changed_fields)}",
                previous_status=order.local_status,
                current_status=order.local_status,
                metadata={"changed_fields": changed_fields},
                is_success=True,
                triggered_by=_request_actor(request),
            )
        messages.success(request, "Order contact details updated.")
    else:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
            title="Manual update failed",
            description="Manual shipping/contact details validation failed.",
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"errors": form.errors.get_json_data()},
            is_success=False,
            triggered_by=_request_actor(request),
        )
        messages.error(request, "Unable to update the order details. Check the form fields.")

    return redirect("order_detail", pk=pk)


@login_required
def update_shiprocket_order_status(request, pk):
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    previous_status = order.local_status
    actor = _request_actor(request)
    redirect_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="home", active_tab=redirect_tab)

    if request.method != "POST":
        return redirect(redirect_url)
    if not _can_update_order_status(request.user):
        messages.error(request, "Your role cannot move order status.")
        return redirect(redirect_url)

    requested_status = str(request.POST.get(f"order-{order.pk}-local_status") or "").strip()
    if requested_status:
        lock_key = _status_update_soft_lock_key(
            order_id=order.pk,
            actor=actor,
            target_status=requested_status,
            session_key=getattr(request.session, "session_key", ""),
        )
        if not cache.add(lock_key, "1", timeout=STATUS_UPDATE_SOFT_LOCK_SECONDS):
            messages.warning(request, "Duplicate status update blocked. Please wait a moment before retrying.")
            return redirect(redirect_url)

    if order.local_status in ShiprocketOrder.LOCKED_STATUSES:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            title="Status update blocked",
            description="Completed or cancelled orders cannot be updated.",
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"blocked": True},
            is_success=False,
            triggered_by=actor,
        )
        messages.error(request, "Completed or cancelled orders cannot be updated.")
        return redirect(redirect_url)

    form = ShiprocketOrderStatusForm(request.POST, instance=order, prefix=f"order-{order.pk}")
    if form.is_valid():
        updated_order = form.save(commit=False)
        target_status = updated_order.local_status
        if (
            _is_ops_viewer(request.user)
            and previous_status == ShiprocketOrder.STATUS_ACCEPTED
            and target_status == ShiprocketOrder.STATUS_PACKED
        ):
            raw_scan_payload = str(request.POST.get(f"order-{order.pk}-packing_scan_payload") or "").strip()
            try:
                scanned_barcodes = json.loads(raw_scan_payload) if raw_scan_payload else []
            except json.JSONDecodeError:
                scanned_barcodes = []

            packing_validation = validate_packing_scans(order, scanned_barcodes)
            validation_error = ""
            if packing_validation["unmatched_items"]:
                validation_error = (
                    "Packing scan setup is incomplete. Product mapping missing for: "
                    + ", ".join(
                        f"{item['name']} x{item['quantity']}"
                        for item in packing_validation["unmatched_items"][:5]
                    )
                    + "."
                )
            elif packing_validation["missing_barcodes"]:
                validation_error = (
                    "Packing scan setup is incomplete. SKU missing for: "
                    + ", ".join(
                        f"{item['sku']}"
                        for item in packing_validation["missing_barcodes"][:5]
                    )
                    + "."
                )
            elif packing_validation["unexpected_barcodes"]:
                validation_error = (
                    "Product is not matched for this order. Unexpected barcode(s): "
                    + ", ".join(packing_validation["unexpected_barcodes"][:5])
                    + "."
                )
            elif packing_validation["over_scanned"]:
                validation_error = (
                    "Some products were scanned more times than ordered: "
                    + ", ".join(
                        f"{item['sku']} ({item['scanned_quantity']}/{item['expected_quantity']})"
                        for item in packing_validation["over_scanned"][:5]
                    )
                    + "."
                )
            elif packing_validation["missing_scans"]:
                validation_error = (
                    "Scan all products before packing. Remaining: "
                    + ", ".join(
                        f"{item['sku']} x{item['remaining_quantity']}"
                        for item in packing_validation["missing_scans"][:5]
                    )
                    + "."
                )

            if validation_error:
                log_order_activity(
                    order=order,
                    event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                    title="Packing verification failed",
                    description=validation_error,
                    previous_status=order.local_status,
                    current_status=order.local_status,
                    metadata={
                        "packing_scan_validation": packing_validation,
                    },
                    is_success=False,
                    triggered_by=actor,
                )
                messages.error(request, validation_error)
                detail_redirect_url = reverse("order_detail", args=[order.pk])
                if redirect_tab:
                    detail_redirect_url = f"{detail_redirect_url}?tab={redirect_tab}"
                return redirect(detail_redirect_url)

        updated_order = _apply_status_timestamps(updated_order)
        updated_order.save()
        success_message = "Order moved to the selected tab."
        stock_result = {}
        if previous_status != updated_order.local_status:
            stock_result = sync_stock_for_status_transition(
                order=updated_order,
                previous_status=previous_status,
                current_status=updated_order.local_status,
                actor=actor,
            )
            status_label_map = dict(ShiprocketOrder.STATUS_CHOICES)
            now = timezone.now()
            _set_order_management_undo_payload(
                request,
                {
                    "token": uuid4().hex,
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=ORDER_MANAGEMENT_UNDO_WINDOW_SECONDS)).isoformat(),
                    "order_count": 1,
                    "summary": (
                        "Status moved to "
                        f"{status_label_map.get(updated_order.local_status, updated_order.local_status)}"
                    ),
                    "entries": [
                        {
                            "order_id": updated_order.pk,
                            "from_status": previous_status,
                            "to_status": updated_order.local_status,
                        }
                    ],
                },
            )
            log_order_activity(
                order=updated_order,
                event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                title=(
                    "Status moved from "
                    f"{status_label_map.get(previous_status, previous_status)} to "
                    f"{status_label_map.get(updated_order.local_status, updated_order.local_status)}"
                ),
                previous_status=previous_status,
                current_status=updated_order.local_status,
                metadata={
                    "tracking_number": updated_order.tracking_number,
                    "cancellation_reason": updated_order.cancellation_reason,
                    "cancellation_note": updated_order.cancellation_note,
                    "packing_scan_verified": (
                        _is_ops_viewer(request.user)
                        and previous_status == ShiprocketOrder.STATUS_ACCEPTED
                        and updated_order.local_status == ShiprocketOrder.STATUS_PACKED
                    ),
                },
                is_success=True,
                triggered_by=actor,
            )
            try:
                enqueue_result = enqueue_whatsapp_notification(
                    order=updated_order,
                    trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
                    previous_status=previous_status,
                    current_status=updated_order.local_status,
                    initiated_by=actor,
                )
            except Exception as exc:
                log_order_activity(
                    order=updated_order,
                    event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
                    title="WhatsApp queueing failed",
                    description=str(exc),
                    previous_status=previous_status,
                    current_status=target_status,
                    metadata={"stage": "enqueue", "trigger": WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE},
                    is_success=False,
                    triggered_by=actor,
                )
                messages.warning(request, f"Order moved, but WhatsApp queueing failed: {exc}")
            else:
                if enqueue_result.get("queued"):
                    queue_job = enqueue_result.get("job")
                    processed_job = _attempt_inline_queue_send(queue_job, actor=actor, source="status_change")
                    if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_SUCCESS:
                        success_message = (
                            f"Order moved to the selected tab. WhatsApp update sent successfully (Job #{processed_job.pk})."
                        )
                    elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_FAILED:
                        error_text = str(processed_job.last_error or "Unknown error").strip()
                        messages.warning(
                            request,
                            f"Order moved, but WhatsApp send failed for Job #{processed_job.pk}: {error_text}",
                        )
                    elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
                        messages.warning(
                            request,
                            f"Order moved, but WhatsApp send failed for Job #{processed_job.pk}. Retry is scheduled.",
                        )
                    elif queue_job:
                        success_message = f"Order moved to the selected tab. WhatsApp update queued (Job #{queue_job.pk})."
                else:
                    reason = str(enqueue_result.get("reason") or "").strip()
                    queue_job = enqueue_result.get("job")
                    if reason == "duplicate_pending" and queue_job:
                        processed_job = _attempt_inline_queue_send(queue_job, actor=actor, source="status_change_duplicate")
                        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_SUCCESS:
                            success_message = (
                                "Order moved to the selected tab. "
                                f"Queued WhatsApp update sent successfully (Job #{processed_job.pk})."
                            )
                        elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_FAILED:
                            error_text = str(processed_job.last_error or "Unknown error").strip()
                            messages.warning(
                                request,
                                f"Order moved, but queued WhatsApp send failed for Job #{processed_job.pk}: {error_text}",
                            )
                        elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
                            messages.warning(
                                request,
                                f"Order moved, but matching WhatsApp update is still retrying (Job #{processed_job.pk}).",
                            )
                        else:
                            success_message = (
                                "Order moved to the selected tab. "
                                f"Matching WhatsApp update is already queued (Job #{queue_job.pk})."
                            )
                    elif reason == "already_sent":
                        success_message = "Order moved to the selected tab. Duplicate WhatsApp update was skipped."
                    elif reason not in {"not_configured", "disabled"} and reason:
                        messages.warning(request, f"Order moved, but WhatsApp queueing skipped: {reason}")
        else:
            _clear_order_management_undo_payload(request)
        messages.success(request, success_message)
        _emit_stock_sync_messages(request, stock_result, context_label=f"Order {updated_order.shiprocket_order_id}")
    else:
        first_error = None
        for errors in form.errors.values():
            if errors:
                first_error = errors[0]
                break
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            title="Status update failed",
            description=str(first_error or "Unable to update the order status."),
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"errors": form.errors.get_json_data()},
            is_success=False,
            triggered_by=actor,
        )
        messages.error(request, first_error or "Unable to update the order status.")

    return redirect(redirect_url)


@login_required
@require_POST
def resend_shiprocket_order_whatsapp(request, pk):
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    actor = _request_actor(request)
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot resend WhatsApp updates.")
        return redirect("order_detail", pk=order.pk)
    redirect_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="home", active_tab=redirect_tab)

    try:
        enqueue_result = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=order.local_status,
            current_status=order.local_status,
            initiated_by=actor,
        )
    except Exception as exc:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
            title="WhatsApp resend queueing failed",
            description=str(exc),
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"stage": "enqueue", "trigger": WhatsAppNotificationLog.TRIGGER_RESEND},
            is_success=False,
            triggered_by=actor,
        )
        messages.error(request, f"WhatsApp resend queueing failed: {exc}")
        return redirect(redirect_url)

    if enqueue_result.get("queued"):
        queue_job = enqueue_result.get("job")
        processed_job = _attempt_inline_queue_send(queue_job, actor=actor, source="resend")
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_SUCCESS:
            messages.success(request, f"WhatsApp resend sent successfully (Job #{processed_job.pk}).")
            return redirect(redirect_url)
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_FAILED:
            error_text = str(processed_job.last_error or "Unknown error").strip()
            messages.warning(
                request,
                f"WhatsApp resend failed for Job #{processed_job.pk}: {error_text}",
            )
            return redirect(redirect_url)
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
            messages.warning(
                request,
                f"WhatsApp resend queued (Job #{processed_job.pk}) but send failed. Retry is scheduled.",
            )
            return redirect(redirect_url)
        if queue_job:
            messages.success(request, f"WhatsApp resend queued (Job #{queue_job.pk}).")
        else:
            messages.success(request, "WhatsApp resend queued.")
        return redirect(redirect_url)

    reason = str(enqueue_result.get("reason") or "").strip()
    queue_job = enqueue_result.get("job")
    if reason == "duplicate_pending" and queue_job:
        processed_job = _attempt_inline_queue_send(queue_job, actor=actor, source="resend_duplicate")
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_SUCCESS:
            messages.success(request, f"Queued WhatsApp resend sent successfully (Job #{processed_job.pk}).")
        elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_FAILED:
            error_text = str(processed_job.last_error or "Unknown error").strip()
            messages.warning(
                request,
                f"Queued WhatsApp resend failed for Job #{processed_job.pk}: {error_text}",
            )
        elif processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
            messages.warning(
                request,
                f"Matching WhatsApp resend is still retrying (Job #{processed_job.pk}).",
            )
        else:
            messages.warning(request, f"Matching WhatsApp resend is already queued (Job #{queue_job.pk}).")
    elif reason == "already_sent":
        messages.info(request, "Same status notification was already sent. Duplicate resend skipped.")
    elif reason == "not_configured":
        messages.warning(request, "No WhatsApp template configured for this status.")
    elif reason == "disabled":
        messages.warning(request, "WhatsApp updates are disabled in settings.")
    else:
        messages.warning(request, "WhatsApp resend skipped.")
    return redirect(redirect_url)


@login_required
@require_POST
def bulk_resend_shiprocket_order_whatsapp(request):
    redirect_tab = (request.POST.get("active_tab") or "").strip()
    redirect_url = _resolve_ops_redirect(request, default_name="home", active_tab=redirect_tab)
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot bulk resend WhatsApp updates.")
        return redirect(redirect_url)

    selected_ids = [value for value in request.POST.getlist("order_ids") if str(value).strip().isdigit()]
    if not selected_ids:
        messages.warning(request, "Select at least one order for bulk resend.")
        return redirect(redirect_url)

    actor = _request_actor(request)
    orders = list(ShiprocketOrder.objects.filter(pk__in=selected_ids).order_by("-order_date", "-updated_at"))
    if not orders:
        messages.warning(request, "No matching orders found for bulk resend.")
        return redirect(redirect_url)

    queued_count = 0
    sent_count = 0
    retrying_count = 0
    failed_count = 0
    skipped_count = 0
    examples = []

    for order in orders:
        try:
            enqueue_result = enqueue_whatsapp_notification(
                order=order,
                trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
                previous_status=order.local_status,
                current_status=order.local_status,
                initiated_by=actor,
            )
        except Exception as exc:
            failed_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: {exc}")
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
                title="WhatsApp bulk resend queueing failed",
                description=str(exc),
                previous_status=order.local_status,
                current_status=order.local_status,
                metadata={"stage": "enqueue", "trigger": WhatsAppNotificationLog.TRIGGER_RESEND, "bulk_resend": True},
                is_success=False,
                triggered_by=actor,
            )
            continue

        queue_job = enqueue_result.get("job")
        processed_job = None
        if queue_job:
            processed_job = _attempt_inline_queue_send(queue_job, actor=actor, source="bulk_resend")

        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_SUCCESS:
            sent_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: sent")
            continue
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_RETRYING:
            retrying_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: retry scheduled")
            continue
        if processed_job and processed_job.status == WhatsAppNotificationQueue.STATUS_FAILED:
            failed_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: {processed_job.last_error or 'failed'}")
            continue

        if enqueue_result.get("queued"):
            queued_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: queued")
            continue

        reason = str(enqueue_result.get("reason") or "").strip()
        if reason == "already_sent":
            skipped_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: already sent")
        elif reason == "duplicate_pending":
            retrying_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: already queued")
        else:
            skipped_count += 1
            if len(examples) < 5:
                examples.append(f"{order.shiprocket_order_id}: {reason or 'skipped'}")

    summary = (
        f"Bulk resend done. Sent={sent_count} queued={queued_count} retrying={retrying_count} "
        f"failed={failed_count} skipped={skipped_count}."
    )
    if examples:
        summary = f"{summary} Examples: {' | '.join(examples)}"

    if failed_count:
        messages.warning(request, summary)
    else:
        messages.success(request, summary)
    return redirect(redirect_url)


@login_required
@require_POST
def process_whatsapp_queue_now(request):
    redirect_url = _resolve_ops_redirect(
        request,
        default_name="home",
        active_tab=(request.POST.get("active_tab") or "").strip(),
    )
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot process the WhatsApp queue.")
        return redirect(redirect_url)

    actor = _request_actor(request) or "manual"
    summary = _process_queue_once(
        limit=request.POST.get("limit") or 20,
        worker_name=f"ui_queue_now:{actor}",
        include_not_due=_is_truthy(request.POST.get("include_not_due")),
    )
    messages.success(
        request,
        (
            f"Queue processed. picked={summary['picked']} processed={summary['processed']} "
            f"success={summary['success']} retried={summary['retried']} failed={summary['failed']}."
        ),
    )
    return redirect(redirect_url)


@login_required
@require_POST
def retry_failed_whatsapp_queue(request):
    redirect_url = _resolve_ops_redirect(request, default_name="home")
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot retry failed queue jobs.")
        return redirect(redirect_url)

    actor = _request_actor(request)
    failed_jobs = WhatsAppNotificationQueue.objects.filter(status=WhatsAppNotificationQueue.STATUS_FAILED)
    failed_count = failed_jobs.count()
    if not failed_count:
        messages.info(request, "No failed WhatsApp queue jobs to retry.")
        return redirect(redirect_url)

    failed_jobs.update(
        status=WhatsAppNotificationQueue.STATUS_PENDING,
        attempt_count=0,
        next_retry_at=None,
        locked_at=None,
        processed_at=None,
        last_error="",
        result_payload={},
    )
    process_limit = max(1, int(request.POST.get("limit") or 50))
    summary = process_whatsapp_notification_queue(
        limit=process_limit,
        worker_name=f"retry_failed:{actor or 'manual'}",
    )
    messages.success(
        request,
        (
            f"Retried failed WhatsApp jobs: reset={failed_count} "
            f"processed={summary['processed']} success={summary['success']} "
            f"retried={summary['retried']} failed={summary['failed']}."
        ),
    )
    return redirect(redirect_url)


@require_POST
@login_required
def run_integration_smoke(request):
    redirect_url = _resolve_ops_redirect(request, default_name="home")
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot run smoke checks.")
        return redirect(redirect_url)
    output = StringIO()
    try:
        call_command("integration_smoke", "--skip-webhook-http", stdout=output)
    except CommandError as exc:
        error_message = str(exc).strip() or "Smoke check failed."
        messages.error(request, f"Integration smoke failed: {error_message}")
        return redirect(redirect_url)

    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    tail = " | ".join(lines[-3:]) if lines else "Integration smoke passed."
    messages.success(request, f"Integration smoke completed: {tail}")
    return redirect(redirect_url)


@require_POST
@login_required
def run_restore_dry_run(request):
    redirect_url = _resolve_ops_redirect(request, default_name="home")
    if not _can_edit_operations(request.user):
        messages.error(request, "Your role has read-only access and cannot run restore dry-run.")
        return redirect(redirect_url)

    backups_dir = getattr(settings, "BASE_DIR") / "backups"
    archive_files = sorted(backups_dir.glob("local_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archive_files:
        messages.warning(request, "No backup archive found for restore dry-run.")
        return redirect(redirect_url)

    latest_archive = archive_files[0]
    output = StringIO()
    try:
        call_command("restore_local_data", "--archive", str(latest_archive), "--dry-run", stdout=output)
    except CommandError as exc:
        error_message = str(exc).strip() or "Restore dry-run failed."
        messages.error(request, f"Restore dry-run failed: {error_message}")
        return redirect(redirect_url)

    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    tail = " | ".join(lines[-2:]) if lines else "Restore dry-run completed."
    messages.success(request, f"Restore dry-run completed: {tail}")
    return redirect(redirect_url)


@require_POST
def track_shipping_label_print(request, pk):
    order = get_object_or_404(ShiprocketOrder, pk=pk)
    if order.local_status != ShiprocketOrder.STATUS_PACKED:
        return JsonResponse({"ok": False, "error": "Only packed orders can be tracked."}, status=400)

    printed_at = timezone.now()
    ShiprocketOrder.objects.filter(pk=order.pk).update(
        label_print_count=F("label_print_count") + 1,
        last_label_printed_at=printed_at,
    )
    order.refresh_from_db(fields=["label_print_count", "last_label_printed_at"])
    log_order_activity(
        order=order,
        event_type=OrderActivityLog.EVENT_LABEL_PRINTED,
        title="Shipping label printed",
        description=f"Label print count updated to {order.label_print_count}.",
        previous_status=order.local_status,
        current_status=order.local_status,
        metadata={
            "label_print_count": order.label_print_count,
            "last_label_printed_at": order.last_label_printed_at.isoformat() if order.last_label_printed_at else "",
        },
        is_success=True,
        triggered_by=_request_actor(request),
    )
    return JsonResponse(
        {
            "ok": True,
            "order_id": order.shiprocket_order_id,
            "label_print_count": order.label_print_count,
            "last_label_printed_at": order.last_label_printed_at.isoformat()
            if order.last_label_printed_at
            else None,
        }
    )


@require_POST
def track_bulk_shipping_labels_print(request):
    order_ids = [order_id for order_id in request.POST.getlist("order_id") if order_id]
    if not order_ids:
        return JsonResponse({"ok": False, "error": "No orders selected."}, status=400)

    printed_at = timezone.now()
    updated_count = ShiprocketOrder.objects.filter(
        pk__in=order_ids,
        local_status=ShiprocketOrder.STATUS_PACKED,
    ).update(
        label_print_count=F("label_print_count") + 1,
        last_label_printed_at=printed_at,
    )
    updated_orders = ShiprocketOrder.objects.filter(
        pk__in=order_ids,
        local_status=ShiprocketOrder.STATUS_PACKED,
    ).only("shiprocket_order_id", "local_status", "label_print_count", "last_label_printed_at")
    actor = _request_actor(request)
    for order in updated_orders:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_LABEL_PRINTED,
            title="Bulk shipping label printed",
            description=f"Label print count updated to {order.label_print_count}.",
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={
                "mode": "bulk",
                "label_print_count": order.label_print_count,
                "last_label_printed_at": order.last_label_printed_at.isoformat() if order.last_label_printed_at else "",
            },
            is_success=True,
            triggered_by=actor,
        )
    return JsonResponse({"ok": True, "updated_count": updated_count})
