"""Tenant-scoped notification inbox and Expo Push delivery."""

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import (
    MobileDevice,
    MobileNotification,
    MobileNotificationPreference,
    TenantMembership,
)


logger = logging.getLogger(__name__)

MANDATORY_NOTIFICATION_CATEGORIES = {
    MobileNotification.CATEGORY_ORDER_ATTENTION,
    MobileNotification.CATEGORY_INTEGRATION_ALERT,
}


def effective_notification_preferences(*, user, tenant):
    stored = {
        row.category: row.enabled
        for row in MobileNotificationPreference.objects.filter(user=user, tenant=tenant)
    }
    return [
        {
            "category": category,
            "enabled": True if category in MANDATORY_NOTIFICATION_CATEGORIES else stored.get(category, True),
            "mandatory": category in MANDATORY_NOTIFICATION_CATEGORIES,
        }
        for category, _label in MobileNotification.CATEGORY_CHOICES
    ]


@transaction.atomic
def update_notification_preferences(*, user, tenant, preferences):
    for preference in preferences:
        category = preference["category"]
        enabled = preference["enabled"]
        if category in MANDATORY_NOTIFICATION_CATEGORIES and not enabled:
            continue
        MobileNotificationPreference.objects.update_or_create(
            user=user,
            tenant=tenant,
            category=category,
            defaults={"enabled": enabled},
        )
    return effective_notification_preferences(user=user, tenant=tenant)


def notification_category_enabled(*, user, tenant, category):
    if category in MANDATORY_NOTIFICATION_CATEGORIES:
        return True
    preference = MobileNotificationPreference.objects.filter(
        user=user,
        tenant=tenant,
        category=category,
    ).only("enabled").first()
    return True if preference is None else preference.enabled


@transaction.atomic
def create_new_order_notifications(order):
    memberships = list(
        TenantMembership.objects.select_related("user").filter(
            tenant=order.tenant,
            tenant__is_active=True,
            user__is_active=True,
            is_active=True,
        )
    )
    title = "New order received"
    reference = order.channel_order_id or order.shiprocket_order_id
    customer = order.customer_name or order.display_shipping_address.get("name") or "Customer"
    message = f"Order {reference} from {customer} for Rs {order.total or '0'}."
    created_notifications = []
    for membership in memberships:
        if not notification_category_enabled(
            user=membership.user,
            tenant=order.tenant,
            category=MobileNotification.CATEGORY_NEW_ORDER,
        ):
            continue
        notification, created = MobileNotification.objects.get_or_create(
            tenant=order.tenant,
            user=membership.user,
            category=MobileNotification.CATEGORY_NEW_ORDER,
            order=order,
            defaults={
                "title": title,
                "message": message,
                "destination": f"/orders/{order.pk}",
            },
        )
        if created:
            created_notifications.append(notification)
    return created_notifications


def _expo_messages(notifications):
    messages = []
    devices = []
    for notification in notifications:
        user_devices = MobileDevice.objects.filter(
            tenant=notification.tenant,
            user=notification.user,
            enabled=True,
        )
        for device in user_devices:
            devices.append(device)
            messages.append(
                {
                    "to": device.expo_push_token,
                    "title": notification.title,
                    "body": notification.message,
                    "sound": "default",
                    "channelId": "orders",
                    "data": {
                        "notification_id": notification.pk,
                        "destination": notification.destination,
                        "order_id": notification.order_id,
                        "category": notification.category,
                    },
                }
            )
    return messages, devices


def send_expo_notifications(notifications):
    messages, devices = _expo_messages(notifications)
    if not getattr(settings, "MOBILE_PUSH_ENABLED", False) or not messages:
        return {"enabled": bool(getattr(settings, "MOBILE_PUSH_ENABLED", False)), "sent": 0, "attempted": len(messages)}

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = str(getattr(settings, "EXPO_PUSH_ACCESS_TOKEN", "") or "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(
        str(getattr(settings, "EXPO_PUSH_API_URL", "https://exp.host/--/api/v2/push/send")),
        data=json.dumps(messages).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        logger.warning("Expo Push delivery failed: %s", exc)
        return {"enabled": True, "sent": 0, "attempted": len(messages), "warning": True}

    tickets = payload.get("data") if isinstance(payload, dict) else []
    tickets = tickets if isinstance(tickets, list) else [tickets]
    sent = 0
    for index, ticket in enumerate(tickets):
        ticket = ticket if isinstance(ticket, dict) else {}
        if ticket.get("status") == "ok":
            sent += 1
            continue
        details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
        if details.get("error") == "DeviceNotRegistered" and index < len(devices):
            MobileDevice.objects.filter(pk=devices[index].pk).update(enabled=False, updated_at=timezone.now())
    return {"enabled": True, "sent": sent, "attempted": len(messages)}


def deliver_new_order_notification(order):
    notifications = create_new_order_notifications(order)
    result = send_expo_notifications(notifications)
    result["notification_count"] = len(notifications)
    return result
