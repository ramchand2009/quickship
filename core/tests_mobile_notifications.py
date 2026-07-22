import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.api.v1.notification_services import (
    create_new_order_notifications,
    send_expo_notifications,
)
from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import (
    MobileDevice,
    MobileNotification,
    MobileNotificationPreference,
    ShiprocketOrder,
    Tenant,
    TenantMembership,
)


@override_settings(
    MOBILE_API_ENABLED=True,
    MOBILE_READ_API_ENABLED=True,
    MOBILE_WRITE_API_ENABLED=True,
    MOBILE_PUSH_ENABLED=False,
)
class MobileNotificationApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="notification-user")
        self.other_user = get_user_model().objects.create_user(username="notification-other")
        self.tenant = Tenant.objects.create(name="Notification Tenant", slug="notification-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Notification", slug="other-notification")
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.installation_id = uuid.uuid4()
        self.session = create_mobile_session(
            user=self.user,
            installation_id=self.installation_id,
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        token, _ = issue_access_token(self.session)
        self.headers = {"Authorization": f"Bearer {token}"}

    def order(self, suffix="NEW"):
        return ShiprocketOrder.objects.create(
            tenant=self.tenant,
            shiprocket_order_id=f"NOTIFY-{suffix}",
            channel_order_id=f"WC-{suffix}",
            customer_name="Notification Customer",
            total="450.00",
        )

    def notification(self, *, user=None, tenant=None, title="New order", is_read=False):
        return MobileNotification.objects.create(
            user=user or self.user,
            tenant=tenant or self.tenant,
            category=MobileNotification.CATEGORY_NEW_ORDER,
            title=title,
            message="A new order is ready.",
            destination="/orders/1",
            is_read=is_read,
        )

    def test_inbox_is_user_and_tenant_scoped_with_unread_count(self):
        own_unread = self.notification(title="Own unread")
        self.notification(title="Own read", is_read=True)
        self.notification(user=self.other_user, title="Other user")
        self.notification(tenant=self.other_tenant, title="Other tenant")

        response = self.client.get(
            "/api/v1/notifications?unread_only=true",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["data"]], [own_unread.pk])
        self.assertEqual(response.json()["meta"]["unread_count"], 1)
        self.assertIn("request_id", response.json()["meta"])

    def test_mark_read_is_scoped_and_requires_idempotency_header(self):
        own = self.notification()
        other = self.notification(user=self.other_user, title="Other")

        missing_key = self.client.post(
            f"/api/v1/notifications/{own.pk}/read",
            {},
            content_type="application/json",
            headers=self.headers,
        )
        hidden = self.client.post(
            f"/api/v1/notifications/{other.pk}/read",
            {},
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "notification-hidden"},
        )
        first = self.client.post(
            f"/api/v1/notifications/{own.pk}/read",
            {},
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "notification-read"},
        )
        replay = self.client.post(
            f"/api/v1/notifications/{own.pk}/read",
            {},
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "notification-read"},
        )

        self.assertEqual(missing_key.status_code, 400)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        own.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertIsNotNone(own.read_at)

    def test_preferences_allow_optional_categories_but_keep_mandatory_categories(self):
        initial = self.client.get("/api/v1/notification-preferences", headers=self.headers)
        updated = self.client.patch(
            "/api/v1/notification-preferences",
            {
                "preferences": [
                    {"category": MobileNotification.CATEGORY_NEW_ORDER, "enabled": False},
                    {"category": MobileNotification.CATEGORY_ORDER_ATTENTION, "enabled": False},
                ]
            },
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "notification-preferences"},
        )

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        preferences = {row["category"]: row for row in updated.json()["data"]}
        self.assertFalse(preferences[MobileNotification.CATEGORY_NEW_ORDER]["enabled"])
        self.assertTrue(preferences[MobileNotification.CATEGORY_ORDER_ATTENTION]["enabled"])
        self.assertTrue(preferences[MobileNotification.CATEGORY_ORDER_ATTENTION]["mandatory"])

    def test_push_token_registration_is_bound_to_authenticated_installation(self):
        payload = {
            "installation_id": str(self.installation_id),
            "platform": "android",
            "expo_push_token": "ExponentPushToken[notification-device-123]",
            "app_version": "1.0.0",
            "device_name": "Test phone",
        }
        created = self.client.post(
            "/api/v1/devices/push-token",
            payload,
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "push-device-create"},
        )
        payload["installation_id"] = str(uuid.uuid4())
        mismatched = self.client.post(
            "/api/v1/devices/push-token",
            payload,
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": "push-device-mismatch"},
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(mismatched.status_code, 403)
        self.assertNotIn("expo_push_token", created.json()["data"])
        self.assertNotIn("device_name", created.json()["data"])
        device = MobileDevice.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertEqual(device.tenant, self.tenant)

        disabled = self.client.delete(f"/api/v1/devices/{device.pk}", headers=self.headers)
        self.assertEqual(disabled.status_code, 204)
        device.refresh_from_db()
        self.assertFalse(device.enabled)

    @override_settings(MOBILE_READ_API_ENABLED=False)
    def test_read_switch_hides_notification_routes(self):
        self.assertEqual(
            self.client.get("/api/v1/notifications", headers=self.headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/v1/notification-preferences", headers=self.headers).status_code,
            404,
        )

    def test_new_order_notifications_respect_preferences_and_do_not_duplicate(self):
        TenantMembership.objects.create(
            user=self.other_user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )
        MobileNotificationPreference.objects.create(
            user=self.other_user,
            tenant=self.tenant,
            category=MobileNotification.CATEGORY_NEW_ORDER,
            enabled=False,
        )
        order = self.order("DEDUPE")

        first = create_new_order_notifications(order)
        replay = create_new_order_notifications(order)

        rows = MobileNotification.objects.filter(order=order)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(replay, [])
        self.assertEqual(rows.get().user, self.user)
        self.assertEqual(rows.get().destination, f"/orders/{order.pk}")

    @override_settings(MOBILE_PUSH_ENABLED=True)
    @patch("core.api.v1.notification_services.urlopen")
    def test_invalid_expo_token_is_disabled(self, mocked_urlopen):
        notification = self.notification()
        device = MobileDevice.objects.create(
            user=self.user,
            tenant=self.tenant,
            installation_id=self.installation_id,
            expo_push_token="ExponentPushToken[invalid-device-123]",
            app_version="1.0.0",
        )

        class ExpoResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "data": [
                            {
                                "status": "error",
                                "details": {"error": "DeviceNotRegistered"},
                            }
                        ]
                    }
                ).encode("utf-8")

        mocked_urlopen.return_value = ExpoResponse()
        result = send_expo_notifications([notification])

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sent"], 0)
        device.refresh_from_db()
        self.assertFalse(device.enabled)
