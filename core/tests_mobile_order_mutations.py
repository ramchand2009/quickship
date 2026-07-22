import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import MobileMutationReceipt, OrderActivityLog, ShiprocketOrder, Tenant, TenantMembership


@override_settings(MOBILE_API_ENABLED=True, MOBILE_WRITE_API_ENABLED=True)
class MobileOrderMutationApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mobile-writer")
        self.tenant = Tenant.objects.create(name="Mutation Tenant", slug="mutation-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Mutation Tenant", slug="other-mutation-tenant")
        self.membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        token, _ = issue_access_token(self.session)
        self.headers = {"Authorization": f"Bearer {token}"}

    def order(self, suffix, *, tenant=None, status=ShiprocketOrder.STATUS_NEW, **values):
        return ShiprocketOrder.objects.create(
            tenant=tenant or self.tenant,
            shiprocket_order_id=f"MUTATION-{suffix}",
            local_status=status,
            customer_name="Mobile Customer",
            customer_phone=values.pop("customer_phone", "9876543210"),
            order_items=[],
            **values,
        )

    def post_status(self, order, payload, key="status-key"):
        return self.client.post(
            f"/api/v1/orders/{order.pk}/status",
            payload,
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": key},
        )

    def post_payment(self, order, payload, key="payment-key"):
        return self.client.post(
            f"/api/v1/orders/{order.pk}/payment-received",
            payload,
            content_type="application/json",
            headers={**self.headers, "Idempotency-Key": key},
        )

    def test_accept_is_versioned_and_same_idempotency_key_replays(self):
        order = self.order("ACCEPT")
        payload = {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "1"}

        first = self.post_status(order, payload)
        replay = self.post_status(order, payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"]["order"]["status"]["code"], ShiprocketOrder.STATUS_ACCEPTED)
        self.assertEqual(first.json()["data"]["order"]["version"], "2")
        self.assertFalse(first.json()["data"]["replayed"])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["data"]["replayed"])
        self.assertEqual(MobileMutationReceipt.objects.count(), 1)
        receipt_payload = MobileMutationReceipt.objects.get().response_payload
        self.assertEqual(receipt_payload["order_id"], order.pk)
        self.assertNotIn("order", receipt_payload)
        self.assertEqual(
            OrderActivityLog.objects.filter(order=order, event_type=OrderActivityLog.EVENT_STATUS_CHANGE).count(),
            1,
        )

    def test_same_idempotency_key_cannot_be_reused_for_another_request(self):
        order = self.order("KEY-REUSE")
        accepted = self.post_status(
            order,
            {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "1"},
            key="same-key",
        )
        reused = self.post_status(
            order,
            {
                "target_status": ShiprocketOrder.STATUS_CANCELLED,
                "expected_version": "2",
                "cancellation_reason": ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST,
            },
            key="same-key",
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(reused.status_code, 409)
        self.assertEqual(reused.json()["error"]["code"], "idempotency_key_reused")

    def test_stale_version_is_rejected_without_changing_order(self):
        order = self.order("STALE", version=3)

        response = self.post_status(
            order,
            {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "2"},
        )

        self.assertEqual(response.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)
        self.assertEqual(MobileMutationReceipt.objects.count(), 0)

    def test_shipped_requires_courier_tracking_and_cost(self):
        order = self.order("SHIP", status=ShiprocketOrder.STATUS_ACCEPTED)

        missing = self.post_status(
            order,
            {"target_status": ShiprocketOrder.STATUS_SHIPPED, "expected_version": "1"},
            key="ship-missing",
        )
        shipped = self.post_status(
            order,
            {
                "target_status": ShiprocketOrder.STATUS_SHIPPED,
                "expected_version": "1",
                "courier_name": "India Post",
                "tracking_number": "AA123456789AA",
                "shipping_base_amount": "80.00",
            },
            key="ship-success",
        )

        self.assertEqual(missing.status_code, 400)
        self.assertIn("courier_name", missing.json()["error"]["fields"])
        self.assertEqual(shipped.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, "AA123456789AA")
        self.assertEqual(str(order.shipping_base_amount), "80.00")
        self.assertIsNotNone(order.shipped_at)

    def test_payment_requires_accepted_state_and_confirmation(self):
        new_order = self.order("PAY-NEW")
        accepted = self.order("PAY-ACCEPTED", status=ShiprocketOrder.STATUS_ACCEPTED)

        unavailable = self.post_payment(new_order, {"expected_version": "1", "confirmed": True}, key="pay-new")
        unconfirmed = self.post_payment(accepted, {"expected_version": "1", "confirmed": False}, key="pay-no")
        received = self.post_payment(accepted, {"expected_version": "1", "confirmed": True}, key="pay-yes")

        self.assertEqual(unavailable.status_code, 422)
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(received.status_code, 200)
        self.assertEqual(received.json()["data"]["order"]["payment_state"]["code"], "received")
        self.assertEqual(received.json()["data"]["order"]["version"], "2")

    def test_role_tenant_and_write_switch_are_enforced(self):
        own = self.order("ROLE")
        other = self.order("OTHER", tenant=self.other_tenant)
        self.membership.role = TenantMembership.ROLE_VENDOR_VIEWER
        self.membership.save(update_fields=["role"])

        forbidden = self.post_status(
            own,
            {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "1"},
            key="role-key",
        )
        self.membership.role = TenantMembership.ROLE_VENDOR_OWNER
        self.membership.save(update_fields=["role"])
        hidden = self.post_status(
            other,
            {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "1"},
            key="tenant-key",
        )
        with override_settings(MOBILE_WRITE_API_ENABLED=False):
            disabled = self.post_status(
                own,
                {"target_status": ShiprocketOrder.STATUS_ACCEPTED, "expected_version": "1"},
                key="disabled-key",
            )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(disabled.status_code, 404)

    def test_packed_status_is_reserved_for_phase_two(self):
        order = self.order("PACKED", status=ShiprocketOrder.STATUS_ACCEPTED)

        response = self.post_status(
            order,
            {"target_status": ShiprocketOrder.STATUS_PACKED, "expected_version": "1"},
        )

        self.assertEqual(response.status_code, 422)
        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
