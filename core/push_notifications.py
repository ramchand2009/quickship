import json
import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import WebPushSubscription

logger = logging.getLogger(__name__)


def web_push_is_configured():
    return bool(
        str(getattr(settings, "PWA_VAPID_PUBLIC_KEY", "") or "").strip()
        and str(getattr(settings, "PWA_VAPID_PRIVATE_KEY", "") or "").strip()
    )


def build_order_push_payload(order):
    order_id = order.channel_order_id or order.shiprocket_order_id or "New order"
    customer = order.customer_name or order.display_shipping_address.get("name") or "WooCommerce customer"
    return {
        "title": "New WooCommerce order",
        "body": f"{order_id} - {customer} - Rs {order.total or '0'}",
        "tag": f"woocommerce-order-{order.pk}",
        "url": reverse("order_detail", args=[order.pk]),
        "order_id": order_id,
        "customer_name": customer,
    }


def send_web_push_notification(subscription, payload):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush is not installed; skipping Web Push notification.")
        return False

    try:
        webpush(
            subscription_info=subscription.to_subscription_info(),
            data=json.dumps(payload),
            vapid_private_key=settings.PWA_VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": getattr(settings, "PWA_VAPID_SUBJECT", "mailto:admin@localhost"),
            },
        )
    except WebPushException as error:
        subscription.last_error = str(error)
        if getattr(error, "response", None) is not None and error.response.status_code in {404, 410}:
            subscription.is_active = False
        subscription.save(update_fields=["last_error", "is_active", "updated_at"])
        logger.warning("Web Push notification failed for subscription %s: %s", subscription.pk, error)
        return False

    subscription.last_sent_at = timezone.now()
    subscription.last_error = ""
    subscription.save(update_fields=["last_sent_at", "last_error", "updated_at"])
    return True


def send_new_order_push_notification(order):
    if not web_push_is_configured():
        return {"enabled": False, "sent": 0}

    payload = build_order_push_payload(order)
    sent = 0
    for subscription in WebPushSubscription.objects.filter(is_active=True).iterator():
        if send_web_push_notification(subscription, payload):
            sent += 1
    return {"enabled": True, "sent": sent}
