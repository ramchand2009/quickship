"""Transactional order mutations for the Android API."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from core.activity import log_order_activity
from core.models import (
    MobileMutationReceipt,
    OrderActivityLog,
    ShiprocketOrder,
    WhatsAppNotificationLog,
)
from core.stock import sync_stock_for_status_transition
from core.whatsapp_queue import enqueue_whatsapp_notification
from core.woocommerce import WooCommerceAPIError, update_order_status as update_woocommerce_order_status

from .exceptions import BusinessRuleError, ConflictError
from .order_serializers import OrderDetailSerializer
from .order_services import mobile_order_detail


def _fingerprint(*, operation, order_id, payload):
    canonical = json.dumps(
        {"operation": operation, "order_id": order_id, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _begin_receipt(*, session, tenant, idempotency_key, request_hash):
    try:
        with transaction.atomic():
            receipt = MobileMutationReceipt.objects.create(
                session=session,
                tenant=tenant,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        return receipt, None
    except IntegrityError:
        receipt = MobileMutationReceipt.objects.filter(
            session=session,
            idempotency_key=idempotency_key,
        ).first()
        if receipt is None:
            raise ConflictError("This action is already being processed.", code="operation_in_progress")
        if receipt.request_hash != request_hash:
            raise ConflictError(
                "This idempotency key was already used for a different request.",
                code="idempotency_key_reused",
            )
        if receipt.status == MobileMutationReceipt.STATUS_COMPLETED and receipt.response_payload:
            return receipt, dict(receipt.response_payload)
        raise ConflictError("This action is already being processed.", code="operation_in_progress")


def _complete_receipt(receipt, payload):
    receipt.status = MobileMutationReceipt.STATUS_COMPLETED
    receipt.response_payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
    receipt.completed_at = timezone.now()
    receipt.save(update_fields=["status", "response_payload", "completed_at"])


def _delete_failed_receipt(receipt):
    if receipt and receipt.status == MobileMutationReceipt.STATUS_PROCESSING:
        receipt.delete()


def _serialize_result(*, tenant, order_id, role, effects, replayed=False):
    order, activity = mobile_order_detail(tenant=tenant, order_id=order_id)
    data = OrderDetailSerializer(
        order,
        context={"role": role, "activity": activity},
    ).data
    return {"data": {"order": data, "effects": effects, "replayed": replayed}}


def _apply_status_timestamp(order):
    now = timezone.now()
    timestamp_fields = {
        ShiprocketOrder.STATUS_SHIPPED: "shipped_at",
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY: "out_for_delivery_at",
        ShiprocketOrder.STATUS_DELIVERED: "delivered_at",
        ShiprocketOrder.STATUS_COMPLETED: "completed_at",
    }
    field = timestamp_fields.get(order.local_status)
    if field and not getattr(order, field):
        setattr(order, field, now)
        return field
    return None


def _status_side_effects(order, *, previous_status, actor):
    effects = []
    if order.source != ShiprocketOrder.SOURCE_WOOCOMMERCE:
        effects.append({"code": "woocommerce_sync", "state": "skipped", "message": "Not a WooCommerce order."})
    else:
        try:
            result = update_woocommerce_order_status(order)
        except WooCommerceAPIError as exc:
            effects.append(
                {
                    "code": "woocommerce_sync",
                    "state": "warning",
                    "message": "The order was updated locally, but WooCommerce sync needs attention.",
                }
            )
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                title="WooCommerce status sync failed",
                description=str(exc),
                previous_status=previous_status,
                current_status=order.local_status,
                metadata={"stage": "mobile_woocommerce_status_sync"},
                is_success=False,
                triggered_by=actor,
            )
        else:
            state = "skipped" if result.get("skipped") else "completed"
            message = result.get("reason") or f"WooCommerce status updated to {result.get('status') or order.local_status}."
            effects.append({"code": "woocommerce_sync", "state": state, "message": str(message)})

    try:
        result = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            previous_status=previous_status,
            current_status=order.local_status,
            initiated_by=actor,
        )
    except Exception as exc:
        effects.append(
            {
                "code": "whatsapp_notification",
                "state": "warning",
                "message": "The order was updated, but the WhatsApp notification could not be queued.",
            }
        )
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
            title="WhatsApp queueing failed",
            description=str(exc),
            previous_status=previous_status,
            current_status=order.local_status,
            metadata={"stage": "mobile_enqueue"},
            is_success=False,
            triggered_by=actor,
        )
    else:
        if result.get("queued"):
            job = result.get("job")
            message = f"WhatsApp update queued{f' as Job #{job.pk}' if job else ''}."
            effects.append({"code": "whatsapp_notification", "state": "queued", "message": message})
        else:
            effects.append(
                {
                    "code": "whatsapp_notification",
                    "state": "skipped",
                    "message": str(result.get("reason") or "WhatsApp update skipped."),
                }
            )
    return effects


def update_order_status(*, session, tenant, role, actor, order_id, idempotency_key, values):
    request_hash = _fingerprint(operation="status", order_id=order_id, payload=values)
    receipt, replay = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return _serialize_result(
            tenant=tenant,
            order_id=order_id,
            role=role,
            effects=replay.get("effects") or [],
            replayed=True,
        )

    try:
        with transaction.atomic():
            order = ShiprocketOrder.objects.select_for_update().filter(tenant=tenant, pk=order_id).first()
            if order is None:
                raise NotFound("The requested resource is unavailable.")
            if str(order.version) != values["expected_version"]:
                raise ConflictError(fields={"expected_version": ["Refresh the order and try again."]})

            previous_status = order.local_status
            target_status = values["target_status"]
            if target_status == ShiprocketOrder.STATUS_PACKED:
                raise BusinessRuleError("Packing is available in Phase 2.")
            if target_status not in ShiprocketOrder.ALLOWED_STATUS_TRANSITIONS.get(previous_status, []):
                raise BusinessRuleError("This status change is not available for the order.")

            if target_status == ShiprocketOrder.STATUS_ACCEPTED:
                phone = str(values.get("customer_phone") or order.resolved_customer_phone or "").strip()
                if sum(character.isdigit() for character in phone) < 10:
                    raise ValidationError({"customer_phone": ["Enter a valid customer mobile number."]})
                order.manual_customer_phone = phone
            if target_status == ShiprocketOrder.STATUS_SHIPPED:
                required = {
                    "courier_name": values.get("courier_name"),
                    "tracking_number": values.get("tracking_number"),
                    "shipping_base_amount": values.get("shipping_base_amount"),
                }
                missing = {key: ["This field is required."] for key, value in required.items() if value in (None, "")}
                if missing:
                    raise ValidationError(missing)
                order.courier_name = values["courier_name"]
                order.tracking_number = values["tracking_number"]
                order.shipping_base_amount = values["shipping_base_amount"]
            if target_status == ShiprocketOrder.STATUS_CANCELLED:
                if not values.get("cancellation_reason"):
                    raise ValidationError({"cancellation_reason": ["Select a cancellation reason."]})
                order.cancellation_reason = values["cancellation_reason"]
                order.cancellation_note = values.get("cancellation_note") or ""

            order.local_status = target_status
            timestamp_field = _apply_status_timestamp(order)
            order.version += 1
            update_fields = [
                "local_status",
                "manual_customer_phone",
                "raw_payload",
                "tracking_number",
                "shipping_base_amount",
                "cancellation_reason",
                "cancellation_note",
                "version",
                "updated_at",
            ]
            if timestamp_field:
                update_fields.append(timestamp_field)
            order.save(update_fields=update_fields)

            stock_result = sync_stock_for_status_transition(
                order=order,
                previous_status=previous_status,
                current_status=target_status,
                actor=actor,
            )
            effects = [
                {
                    "code": "stock_sync",
                    "state": "completed" if stock_result.get("changed") else "skipped",
                    "message": (
                        f"{stock_result.get('movement_count', 0)} stock movement(s) recorded."
                        if stock_result.get("changed")
                        else "No stock movement was required."
                    ),
                }
            ]
            labels = dict(ShiprocketOrder.STATUS_CHOICES)
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                title=f"Status moved from {labels.get(previous_status)} to {labels.get(target_status)}",
                previous_status=previous_status,
                current_status=target_status,
                metadata={"source": "mobile_api", "idempotency_key": idempotency_key},
                is_success=True,
                triggered_by=actor,
            )

        effects.extend(_status_side_effects(order, previous_status=previous_status, actor=actor))
        payload = _serialize_result(tenant=tenant, order_id=order_id, role=role, effects=effects)
        _complete_receipt(receipt, {"order_id": order_id, "effects": effects})
        return payload
    except Exception:
        _delete_failed_receipt(receipt)
        raise


def mark_payment_received(*, session, tenant, role, actor, order_id, idempotency_key, values):
    request_hash = _fingerprint(operation="payment_received", order_id=order_id, payload=values)
    receipt, replay = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return _serialize_result(
            tenant=tenant,
            order_id=order_id,
            role=role,
            effects=replay.get("effects") or [],
            replayed=True,
        )
    try:
        with transaction.atomic():
            order = ShiprocketOrder.objects.select_for_update().filter(tenant=tenant, pk=order_id).first()
            if order is None:
                raise NotFound("The requested resource is unavailable.")
            if str(order.version) != values["expected_version"]:
                raise ConflictError(fields={"expected_version": ["Refresh the order and try again."]})
            if order.local_status not in {ShiprocketOrder.STATUS_ACCEPTED, ShiprocketOrder.STATUS_PACKED}:
                raise BusinessRuleError("Payment can be marked received after the order is accepted.")
            if order.payment_received_at is not None:
                raise BusinessRuleError("Payment is already marked as received.")

            order.payment_received_at = timezone.now()
            order.version += 1
            order.save(update_fields=["payment_received_at", "version", "updated_at"])
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
                title="Payment marked received",
                description="Customer payment was marked as received from the mobile app.",
                previous_status=order.local_status,
                current_status=order.local_status,
                metadata={"source": "mobile_api", "idempotency_key": idempotency_key},
                is_success=True,
                triggered_by=actor,
            )

        payload = _serialize_result(tenant=tenant, order_id=order_id, role=role, effects=[])
        _complete_receipt(receipt, {"order_id": order_id, "effects": []})
        return payload
    except Exception:
        _delete_failed_receipt(receipt)
        raise
