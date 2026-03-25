import json
from io import StringIO
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management.base import CommandError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import ShiprocketOrderStatusForm, StockAdjustmentForm
from .models import (
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
from .system_status import write_system_heartbeat
from .whatomate import WhatomateNotificationError, _build_template_params_for_status
from .views import _build_webhook_test_payload, _send_internal_webhook_test
from .whatsapp_queue import enqueue_whatsapp_notification, process_whatsapp_notification_queue


class ShiprocketOrderStatusFormTests(TestCase):
    def test_status_form_excludes_current_and_previous_statuses(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]

        self.assertEqual(choices[0], ShiprocketOrder.STATUS_DELIVERY_ISSUE)
        self.assertIn(ShiprocketOrder.STATUS_OUT_FOR_DELIVERY, choices)
        self.assertNotIn(ShiprocketOrder.STATUS_NEW, choices)
        self.assertNotIn(ShiprocketOrder.STATUS_SHIPPED, choices)

    def test_delivery_issue_moves_only_to_out_for_delivery_or_cancel(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-DI-1",
            local_status=ShiprocketOrder.STATUS_DELIVERY_ISSUE,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]
        self.assertEqual(
            choices,
            [ShiprocketOrder.STATUS_OUT_FOR_DELIVERY, ShiprocketOrder.STATUS_CANCELLED],
        )

    def test_completed_order_has_no_status_choices(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-2",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]
        self.assertEqual(choices, [])

    def test_new_order_has_accept_and_cancel_choices(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-3",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]
        self.assertEqual(
            choices,
            [ShiprocketOrder.STATUS_ACCEPTED, ShiprocketOrder.STATUS_CANCELLED],
        )


class LoginRateLimitTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user(username="ratelimit", password="validpass123")

    @override_settings(
        LOGIN_LOCKOUT_ATTEMPTS=2,
        LOGIN_LOCKOUT_WINDOW_SECONDS=300,
        LOGIN_LOCKOUT_DURATION_SECONDS=300,
    )
    def test_login_is_locked_after_repeated_failed_attempts(self):
        login_url = reverse("login")
        self.client.post(login_url, {"username": "ratelimit", "password": "badpass"})
        self.client.post(login_url, {"username": "ratelimit", "password": "badpass"})
        response = self.client.post(login_url, {"username": "ratelimit", "password": "validpass123"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)


class ShiprocketOrderStatusUpdateViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="tester", password="testpass123")
        self.client.force_login(user)

    def test_cannot_move_order_backwards_via_post(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BACKWARD-1",
            local_status=ShiprocketOrder.STATUS_DELIVERED,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_SHIPPED},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_DELIVERED)

    def test_can_move_order_forward_via_post(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-FORWARD-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertIsNone(order.shipped_at)
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
                previous_status=ShiprocketOrder.STATUS_NEW,
                current_status=ShiprocketOrder.STATUS_ACCEPTED,
                is_success=True,
            ).exists()
        )

    @patch("core.views.enqueue_whatsapp_notification")
    def test_accept_status_deducts_stock_by_matching_sku(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        product = Product.objects.create(
            name="Moringa Powder",
            sku="SKU-ACCEPT-1",
            barcode="890000000001",
            stock_quantity=12,
            reorder_level=2,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STOCK-ACCEPT-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            order_items=[
                {"sku": "sku-accept-1", "quantity": 3},
                {"sku": "SKU-ACCEPT-1", "quantity": 2},
            ],
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        product.refresh_from_db()
        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertEqual(product.stock_quantity, 7)
        movement = StockMovement.objects.get(
            order=order,
            product=product,
            movement_type=StockMovement.TYPE_ORDER_ACCEPTED,
        )
        self.assertEqual(movement.quantity_delta, -5)
        self.assertContains(response, "stock deducted for 1 SKU")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_accept_status_deducts_stock_by_matching_smartbiz_product_id(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        product = Product.objects.create(
            name="Goat Milk Soap",
            sku="MTHKS01",
            smartbiz_product_id="06d3d905-2768-4f8c-8ce5-22c7fed3c54d",
            stock_quantity=10,
            reorder_level=2,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STOCK-SMARTBIZ-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            order_items=[
                {"sku": "06d3d905-2768-4f8c-8ce5-22c7fed3c54d", "quantity": 2},
            ],
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        product.refresh_from_db()
        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertEqual(product.stock_quantity, 8)
        self.assertTrue(
            StockMovement.objects.filter(
                order=order,
                product=product,
                movement_type=StockMovement.TYPE_ORDER_ACCEPTED,
                quantity_delta=-2,
            ).exists()
        )
        self.assertContains(response, "stock deducted for 1 SKU")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_cancelled_status_restores_stock_after_previous_accept(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        product = Product.objects.create(
            name="Cold Pressed Oil",
            sku="SKU-CANCEL-1",
            barcode="890000000002",
            stock_quantity=20,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STOCK-CANCEL-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            order_items=[{"sku": "SKU-CANCEL-1", "quantity": 4}],
        )

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_CANCELLED,
                f"order-{order.pk}-cancellation_reason": ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST,
            },
            follow=True,
        )

        product.refresh_from_db()
        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_CANCELLED)
        self.assertEqual(product.stock_quantity, 20)
        self.assertTrue(
            StockMovement.objects.filter(
                order=order,
                product=product,
                movement_type=StockMovement.TYPE_ORDER_CANCELLED,
                quantity_delta=4,
            ).exists()
        )
        self.assertContains(response, "stock restored for 1 SKU")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_cancel_from_new_order_does_not_restore_without_prior_accept(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        product = Product.objects.create(
            name="Turmeric",
            sku="SKU-CANCEL-NEW-1",
            stock_quantity=9,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STOCK-CANCEL-NEW-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            order_items=[{"sku": "SKU-CANCEL-NEW-1", "quantity": 2}],
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_CANCELLED,
                f"order-{order.pk}-cancellation_reason": ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST,
            },
            follow=True,
        )

        product.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(product.stock_quantity, 9)
        self.assertFalse(
            StockMovement.objects.filter(
                order=order,
                product=product,
                movement_type=StockMovement.TYPE_ORDER_CANCELLED,
            ).exists()
        )

    @patch("core.views.enqueue_whatsapp_notification")
    def test_accept_status_triggers_whatsapp_queue(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 101})(),
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-ACCEPT-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        mock_enqueue_whatsapp_notification.assert_called_once()
        self.assertContains(response, "WhatsApp update queued")

    @patch("core.views._attempt_inline_queue_send")
    @patch("core.views.enqueue_whatsapp_notification")
    def test_accept_status_sends_whatsapp_inline_when_job_succeeds(
        self,
        mock_enqueue_whatsapp_notification,
        mock_attempt_inline_queue_send,
    ):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 111})(),
        }
        mock_attempt_inline_queue_send.return_value = type(
            "ProcessedJob",
            (),
            {"pk": 111, "status": WhatsAppNotificationQueue.STATUS_SUCCESS, "last_error": ""},
        )()
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-INLINE-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        mock_enqueue_whatsapp_notification.assert_called_once()
        mock_attempt_inline_queue_send.assert_called_once()
        self.assertContains(response, "WhatsApp update sent successfully")

    @patch("core.views._attempt_inline_queue_send")
    @patch("core.views.enqueue_whatsapp_notification")
    def test_accept_status_shows_warning_when_inline_whatsapp_send_fails(
        self,
        mock_enqueue_whatsapp_notification,
        mock_attempt_inline_queue_send,
    ):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 112})(),
        }
        mock_attempt_inline_queue_send.return_value = type(
            "ProcessedJob",
            (),
            {
                "pk": 112,
                "status": WhatsAppNotificationQueue.STATUS_FAILED,
                "last_error": "network timeout",
            },
        )()
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-INLINE-FAIL-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        mock_enqueue_whatsapp_notification.assert_called_once()
        mock_attempt_inline_queue_send.assert_called_once()
        self.assertContains(response, "Order moved, but WhatsApp send failed")
        self.assertContains(response, "network timeout")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_non_accept_transition_queues_whatsapp_status_update(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 102})(),
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-NON-ACCEPT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Receiver Name",
                "phone": "9000012345",
                "address_1": "Street 1",
                "pincode": "600001",
            },
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED,
                "active_tab": ShiprocketOrder.STATUS_ACCEPTED,
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, f"{reverse('home')}?tab={ShiprocketOrder.STATUS_ACCEPTED}")
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_PACKED)
        mock_enqueue_whatsapp_notification.assert_called_once()
        self.assertContains(response, "WhatsApp update queued")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_whatsapp_queue_failure_does_not_block_accept_transition(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.side_effect = Exception("queue offline")
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-FAIL-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertContains(response, "WhatsApp queueing failed")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_resend_whatsapp_update_queues_job(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 201})(),
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-RESEND-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="TRK1234567890",
            shipping_address={
                "name": "Receiver Name",
                "phone": "9000012345",
                "address_1": "Street 1",
                "pincode": "600001",
            },
        )

        response = self.client.post(
            reverse("resend_shiprocket_order_whatsapp", args=[order.pk]),
            {"active_tab": ShiprocketOrder.STATUS_SHIPPED},
            follow=True,
        )

        self.assertRedirects(response, f"{reverse('home')}?tab={ShiprocketOrder.STATUS_SHIPPED}")
        mock_enqueue_whatsapp_notification.assert_called_once()
        self.assertContains(response, "WhatsApp resend queued")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_resend_whatsapp_update_handles_queue_failure(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.side_effect = Exception("queue offline")
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-RESEND-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Receiver Name",
                "phone": "9000012345",
                "address_1": "Street 1",
                "pincode": "600001",
            },
        )

        response = self.client.post(
            reverse("resend_shiprocket_order_whatsapp", args=[order.pk]),
            {"active_tab": ShiprocketOrder.STATUS_PACKED},
            follow=True,
        )

        self.assertRedirects(response, f"{reverse('home')}?tab={ShiprocketOrder.STATUS_PACKED}")
        mock_enqueue_whatsapp_notification.assert_called_once()
        self.assertContains(response, "WhatsApp resend queueing failed")

    @patch("core.views._attempt_inline_queue_send")
    @patch("core.views.enqueue_whatsapp_notification")
    def test_bulk_resend_whatsapp_for_selected_orders(
        self,
        mock_enqueue_whatsapp_notification,
        mock_attempt_inline_queue_send,
    ):
        first_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-RESEND-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={"phone": "9000011111"},
        )
        second_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-RESEND-2",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={"phone": "9000011112"},
        )
        mock_enqueue_whatsapp_notification.side_effect = [
            {"queued": True, "reason": "queued", "job": type("Job", (), {"pk": 301})()},
            {"queued": True, "reason": "queued", "job": type("Job", (), {"pk": 302})()},
        ]
        mock_attempt_inline_queue_send.side_effect = [
            type("ProcessedJob", (), {"pk": 301, "status": WhatsAppNotificationQueue.STATUS_SUCCESS, "last_error": ""})(),
            type("ProcessedJob", (), {"pk": 302, "status": WhatsAppNotificationQueue.STATUS_RETRYING, "last_error": ""})(),
        ]

        response = self.client.post(
            reverse("bulk_resend_shiprocket_order_whatsapp"),
            {
                "active_tab": ShiprocketOrder.STATUS_ACCEPTED,
                "order_ids": [str(first_order.pk), str(second_order.pk)],
            },
            follow=True,
        )

        self.assertRedirects(response, f"{reverse('home')}?tab={ShiprocketOrder.STATUS_ACCEPTED}")
        self.assertEqual(mock_enqueue_whatsapp_notification.call_count, 2)
        self.assertEqual(mock_attempt_inline_queue_send.call_count, 2)
        self.assertContains(response, "Bulk resend done.")
        self.assertContains(response, "Sent=1")
        self.assertContains(response, "retrying=1")

    def test_completed_order_cannot_be_updated(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-COMPLETED-1",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_DELIVERED},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_COMPLETED)

    def test_can_cancel_from_new_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-CANCEL-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_CANCELLED,
                f"order-{order.pk}-cancellation_reason": ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST,
                f"order-{order.pk}-cancellation_note": "Asked by customer on call",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_CANCELLED)
        self.assertEqual(order.cancellation_reason, ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST)

    def test_can_cancel_from_shipped_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-CANCEL-SHIPPED-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_CANCELLED,
                f"order-{order.pk}-cancellation_reason": ShiprocketOrder.CANCEL_REASON_COURIER_ISSUE,
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_CANCELLED)
        self.assertEqual(order.cancellation_reason, ShiprocketOrder.CANCEL_REASON_COURIER_ISSUE)

    def test_can_move_from_shipped_to_delivery_issue(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-DELIVERY-ISSUE-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="1234567890123",
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_DELIVERY_ISSUE,
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_DELIVERY_ISSUE)

    def test_cannot_cancel_without_reason(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-CANCEL-NO-REASON-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_CANCELLED},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)

    def test_cannot_move_to_packed_without_required_shipping_fields(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PACK-MISSING-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Receiver Name",
                "phone": "",
                "address_1": "Street 1",
                "pincode": "",
            },
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)

    def test_status_update_redirects_back_to_submitted_tab(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-TAB-REDIRECT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Receiver Name",
                "phone": "9000012345",
                "address_1": "Street 1",
                "pincode": "600001",
            },
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED,
                "active_tab": ShiprocketOrder.STATUS_ACCEPTED,
            },
        )

        order.refresh_from_db()
        self.assertRedirects(response, f"{reverse('home')}?tab={ShiprocketOrder.STATUS_ACCEPTED}", fetch_redirect_response=False)
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_PACKED)


class ShippingLabelViewTests(TestCase):
    def test_shipping_label_page_renders_with_4x6_size(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-1",
            channel_order_id="CH-1001",
            local_status=ShiprocketOrder.STATUS_PACKED,
            customer_name="Local Name",
            total="299.00",
            shipping_address={
                "name": "Synced Name",
                "phone": "9000000000",
                "address_1": "Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
            manual_customer_name="Manual Name",
            manual_customer_phone="9999999999",
            manual_shipping_address_1="Manual Street 10",
            manual_shipping_city="Coimbatore",
            manual_shipping_state="TN",
            manual_shipping_country="India",
            manual_shipping_pincode="641001",
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            phone="8888888888",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]))
        order.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "size: A4 portrait;")
        self.assertContains(response, "SR-LABEL-1")
        self.assertContains(response, "Manual Name")
        self.assertContains(response, "Manual Street 10")
        self.assertContains(response, "Warehouse Sender")
        self.assertContains(response, "Sender Street 5")
        self.assertEqual(order.label_print_count, 0)
        self.assertIsNone(order.last_label_printed_at)

    def test_track_shipping_label_print_increments_count(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-TRACK-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
        )

        response = self.client.post(reverse("track_shipping_label_print", args=[order.pk]))
        order.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(order.label_print_count, 1)
        self.assertIsNotNone(order.last_label_printed_at)
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_LABEL_PRINTED,
                is_success=True,
            ).exists()
        )

    def test_shipping_label_redirects_for_non_packed_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-NOT-PACKED-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]), follow=True)

        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))

    def test_shipping_label_respects_start_position_slot(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-SLOT-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Slot Receiver",
                "phone": "9000011111",
                "address_1": "Slot Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600010",
            },
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(
            reverse("shipping_label_4x6", args=[order.pk]),
            {"start_position": "bottom_right"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["start_position"], "bottom_right")
        slots = response.context["label_slots"]
        self.assertIsNone(slots[0])
        self.assertIsNone(slots[1])
        self.assertIsNone(slots[2])
        self.assertEqual(slots[3].pk, order.pk)


class BulkShippingLabelsViewTests(TestCase):
    def test_bulk_labels_page_filters_orders_by_status(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-NEW-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={
                "name": "New Receiver",
                "phone": "9000000001",
                "address_1": "New Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
        )
        packed_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-PACKED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Packed Receiver",
                "phone": "9000000002",
                "address_1": "Packed Street 1",
                "city": "Madurai",
                "state": "TN",
                "country": "India",
                "pincode": "625001",
            },
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(
            reverse("bulk_shipping_labels_4x6"),
            {"status": ShiprocketOrder.STATUS_NEW},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Shipping Labels ST-4 (Order Packed)")
        self.assertNotContains(response, "New Receiver")
        self.assertContains(response, "Packed Receiver")
        self.assertContains(response, "Print ST-4 (A4)")
        self.assertContains(response, reverse("home"))
        self.assertNotContains(response, "SR-BULK-NEW-1")
        packed_order.refresh_from_db()
        self.assertEqual(packed_order.label_print_count, 0)
        self.assertIsNone(packed_order.last_label_printed_at)

        track_response = self.client.post(
            reverse("track_bulk_shipping_labels_print"),
            {"order_id": [str(packed_order.pk)]},
        )
        packed_order.refresh_from_db()

        self.assertEqual(track_response.status_code, 200)
        self.assertEqual(packed_order.label_print_count, 1)
        self.assertIsNotNone(packed_order.last_label_printed_at)

    def test_home_includes_bulk_label_link_for_status_tab(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-LINK-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-LINK-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
        )

        response = self.client.get(reverse("home"))
        packed_bulk_link = f"{reverse('bulk_shipping_labels_4x6')}?status={ShiprocketOrder.STATUS_PACKED}"
        new_bulk_link = f"{reverse('bulk_shipping_labels_4x6')}?status={ShiprocketOrder.STATUS_NEW}"

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, packed_bulk_link)
        self.assertNotContains(response, new_bulk_link)
        self.assertContains(response, reverse("print_queue"))
        self.assertContains(response, "Order Accepted")
        self.assertContains(response, "Order Packed")
        self.assertContains(response, "Order Cancelled")

    def test_bulk_labels_respect_start_position_for_first_sheet(self):
        first_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-SLOT-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Receiver 1",
                "phone": "9000000001",
                "address_1": "Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
        )
        second_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-SLOT-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Receiver 2",
                "phone": "9000000002",
                "address_1": "Street 2",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600002",
            },
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(
            reverse("bulk_shipping_labels_4x6"),
            {"start_position": "bottom_right"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["start_position"], "bottom_right")
        pages = response.context["pages"]
        self.assertEqual(len(pages), 2)
        first_slots = pages[0]["slots"]
        second_slots = pages[1]["slots"]
        self.assertIsNone(first_slots[0])
        self.assertIsNone(first_slots[1])
        self.assertIsNone(first_slots[2])
        self.assertIsNotNone(first_slots[3])
        self.assertIsNotNone(second_slots[0])
        page_order_ids = {
            first_slots[3].shiprocket_order_id,
            second_slots[0].shiprocket_order_id,
        }
        self.assertEqual(page_order_ids, {first_order.shiprocket_order_id, second_order.shiprocket_order_id})

    def test_home_shows_packing_checklist_pending_for_accepted_order(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-CHECKLIST-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Receiver Name",
                "phone": "",
                "address_1": "Street 10",
                "pincode": "",
            },
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Packing Checklist")
        self.assertContains(response, "Pending")
        self.assertContains(response, "Phone, Pincode")

    def test_home_shows_system_status_card(self):
        write_system_heartbeat("queue_worker", {"source": "test"})
        write_system_heartbeat("queue_alerts", {"source": "test"})
        write_system_heartbeat("nightly_backup", {"source": "test"})

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Status")
        self.assertContains(response, "Worker")
        self.assertContains(response, "Alerts")
        self.assertContains(response, "Backups")
        self.assertNotContains(response, "Never")

    def test_home_shows_action_sections_and_work_queues(self):
        Product.objects.create(
            name="Low Stock Powder",
            sku="SKU-HOME-LOW-1",
            stock_quantity=2,
            reorder_level=5,
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-NEW-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={
                "name": "New Receiver",
                "phone": "9000001001",
                "address_1": "New Street 1",
                "pincode": "600001",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-ACCEPT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Accepted Receiver",
                "phone": "",
                "address_1": "Accepted Street 1",
                "pincode": "",
            },
        )
        packed_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-PACKED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Packed Receiver",
                "phone": "9000001003",
                "address_1": "Packed Street 1",
                "pincode": "600003",
            },
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Status Shortcuts")
        self.assertContains(response, "Action Center")
        self.assertContains(response, "Needs Acceptance")
        self.assertContains(response, "Low Stock Items")
        self.assertContains(response, "Open Stock Management")
        self.assertContains(response, "Packing Checklist Blockers")
        self.assertContains(response, "Ready to Print")
        self.assertContains(response, "SR-HOME-NEW-1")
        self.assertContains(response, "SR-HOME-ACCEPT-1")
        self.assertContains(response, "Packing Checklist Pending")
        self.assertContains(response, "Missing: Phone, Pincode")
        self.assertContains(response, packed_order.shiprocket_order_id)
        self.assertContains(response, "orders-dashboard-shell")
        self.assertContains(response, "dashboard-shortcuts-row")
        self.assertContains(response, reverse("stock_management"))

    def test_home_shows_queue_diagnostics_widgets(self):
        WhatsAppNotificationQueue.objects.create(
            shiprocket_order_id="SR-QUEUE-FAIL-1",
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
            last_error="Connection failed",
        )
        WhatsAppNotificationLog.objects.create(
            shiprocket_order_id="SR-QUEUE-FAIL-LOG-1",
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            is_success=False,
            error_message="Connection failed",
        )
        pending_job = WhatsAppNotificationQueue.objects.create(
            shiprocket_order_id="SR-QUEUE-OLD-1",
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            status=WhatsAppNotificationQueue.STATUS_PENDING,
        )
        WhatsAppNotificationQueue.objects.filter(pk=pending_job.pk).update(
            created_at=timezone.now() - timedelta(hours=2),
            updated_at=timezone.now() - timedelta(hours=2),
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Oldest Open Queue Job")
        self.assertContains(response, "SR-QUEUE-OLD-1")
        self.assertContains(response, "Top Failure Reasons (24h)")
        self.assertContains(response, "(2) Connection failed")

    @patch("core.views.process_whatsapp_notification_queue")
    def test_process_whatsapp_queue_now_action_from_home(self, mock_process_queue):
        user = get_user_model().objects.create_user(username="homeops", password="testpass123")
        self.client.force_login(user)
        mock_process_queue.return_value = {
            "picked": 3,
            "processed": 3,
            "success": 2,
            "retried": 1,
            "failed": 0,
            "worker": "ui_queue_now:homeops",
        }

        response = self.client.post(
            reverse("process_whatsapp_queue_now"),
            {"return_to": "home", "limit": "20"},
            follow=True,
        )

        self.assertRedirects(response, reverse("home"))
        mock_process_queue.assert_called_once_with(
            limit=20,
            worker_name="ui_queue_now:homeops",
            include_not_due=False,
        )
        self.assertContains(response, "Queue processed. picked=3 processed=3 success=2 retried=1 failed=0.")


class OrderManagementViewTests(TestCase):
    def test_order_management_hides_shiprocket_status_column(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ORDER-MGMT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            tracking_number="1234567890123",
            shipping_address={
                "name": "Receiver Name",
                "phone": "",
                "address_1": "Street 10",
                "pincode": "",
            },
        )

        response = self.client.get(
            reverse("order_management"),
            {"tab": ShiprocketOrder.STATUS_ACCEPTED},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<th>Shiprocket Status</th>", html=True)
        self.assertContains(response, order.shiprocket_order_id)
        self.assertContains(response, "Workflow: Order Accepted")
        self.assertContains(response, "Tracking: 1234567890123")
        self.assertContains(response, "Packing Checklist")
        self.assertContains(response, "order-status-tabs")
        self.assertContains(response, 'data-label="Order ID"', html=False)
        self.assertContains(response, 'data-label="Move Order"', html=False)


class PackingQueueViewTests(TestCase):
    def test_packing_queue_lists_only_accepted_orders_and_has_mobile_hooks(self):
        accepted_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PACK-QUEUE-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Pack Receiver",
                "phone": "9000000401",
                "city": "Chennai",
                "pincode": "600401",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PACK-QUEUE-2",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={
                "name": "Skip Receiver",
                "phone": "9000000402",
                "city": "Chennai",
                "pincode": "600402",
            },
        )

        response = self.client.get(reverse("packing_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Packing Queue")
        self.assertContains(response, accepted_order.shiprocket_order_id)
        self.assertNotContains(response, "SR-PACK-QUEUE-2")
        self.assertContains(response, "queue-results-table")
        self.assertContains(response, 'data-label="Order ID"', html=False)
        self.assertContains(response, "queue-mobile-select-cell")


class PrintQueueViewTests(TestCase):
    def test_print_queue_lists_only_packed_orders(self):
        packed_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-PACKED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Queue Packed Receiver",
                "phone": "9000000011",
                "address_1": "Queue Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600011",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-NEW-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={
                "name": "Queue New Receiver",
                "phone": "9000000099",
                "address_1": "New Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600099",
            },
        )

        response = self.client.get(reverse("print_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Packed Orders Print Queue")
        self.assertContains(response, "Queue Packed Receiver")
        self.assertContains(response, packed_order.shiprocket_order_id)
        self.assertNotContains(response, "Queue New Receiver")
        self.assertContains(response, reverse("bulk_shipping_labels_4x6"))
        self.assertContains(response, "name=\"order_id\"")
        self.assertContains(response, "queue-results-table")
        self.assertContains(response, 'data-label="Order ID"', html=False)
        self.assertContains(response, "queue-mobile-select-cell")

    def test_print_queue_skip_printed_filter_hides_reprinted_orders(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-SKIP-PRINTED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=2,
            shipping_address={
                "name": "Printed Receiver",
                "phone": "9000000101",
                "address_1": "Printed Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600101",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-SKIP-PRINTED-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=0,
            shipping_address={
                "name": "Fresh Receiver",
                "phone": "9000000102",
                "address_1": "Fresh Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600102",
            },
        )

        response = self.client.get(reverse("print_queue"), {"skip_printed": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fresh Receiver")
        self.assertNotContains(response, "Printed Receiver")
        self.assertContains(response, "id=\"skipPrintedToggle\"")
        self.assertContains(response, "checked")

    def test_print_queue_ready_only_filter_hides_incomplete_addresses(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-READY-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Incomplete Packed",
                "phone": "",
                "address_1": "Ready Street",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-READY-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Complete Packed",
                "phone": "9000000202",
                "address_1": "Ready Street 2",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600202",
            },
        )

        response = self.client.get(reverse("print_queue"), {"ready_only": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete Packed")
        self.assertNotContains(response, "Incomplete Packed")
        self.assertContains(response, "id=\"readyOnlyToggle\"")

    def test_print_queue_search_filters_orders(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-SEARCH-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Alpha Receiver",
                "phone": "9000000301",
                "address_1": "Search Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600301",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-SEARCH-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Beta Receiver",
                "phone": "9000000302",
                "address_1": "Search Street 2",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600302",
            },
        )

        response = self.client.get(reverse("print_queue"), {"q": "600302"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beta Receiver")
        self.assertNotContains(response, "Alpha Receiver")
        self.assertContains(response, "value=\"600302\"")


class ShiprocketOrderManualUpdateViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="manualadmin", password="testpass123")
        self.client.force_login(user)

    def test_manual_update_is_blocked_after_shipped(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-MANUAL-LOCK-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            manual_customer_name="Before Lock",
        )

        response = self.client.post(
            reverse("update_shiprocket_order", args=[order.pk]),
            {"manual_customer_name": "After Lock"},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.manual_customer_name, "Before Lock")

    def test_manual_update_works_before_shipped(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-MANUAL-OPEN-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            manual_customer_name="Before Edit",
        )

        response = self.client.post(
            reverse("update_shiprocket_order", args=[order.pk]),
            {
                "manual_customer_name": "After Edit",
                "manual_customer_email": "",
                "manual_customer_phone": "",
                "manual_customer_alternate_phone": "",
                "manual_shipping_address_1": "",
                "manual_shipping_address_2": "",
                "manual_shipping_city": "",
                "manual_shipping_state": "",
                "manual_shipping_country": "",
                "manual_shipping_pincode": "",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.manual_customer_name, "After Edit")


class SenderAddressViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="senderadmin", password="testpass123")
        self.client.force_login(user)

    def test_sender_address_page_has_responsive_actions(self):
        response = self.client.get(reverse("sender_address"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "responsive-form-card")
        self.assertContains(response, "responsive-form-actions")

    def test_sender_address_saved_from_tab(self):
        response = self.client.post(
            reverse("sender_address"),
            {
                "name": "Main Warehouse",
                "email": "warehouse@example.com",
                "phone": "7777777777",
                "address_1": "Plot 1",
                "address_2": "Industrial Area",
                "city": "Salem",
                "state": "TN",
                "country": "India",
                "pincode": "636001",
            },
            follow=True,
        )

        sender = SenderAddress.get_default()
        self.assertRedirects(response, reverse("sender_address"))
        self.assertEqual(sender.name, "Main Warehouse")
        self.assertEqual(sender.address_1, "Plot 1")

    def test_sender_address_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("sender_address"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class WhatsAppSettingsViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="whatsappadmin", password="testpass123")
        self.client.force_login(user)

    def test_whatsapp_settings_saved_from_tab(self):
        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "settings-enabled": "on",
                "settings-api_base_url": "http://127.0.0.1:8080",
                "settings-api_key": "whm_test_key_123",
                "action": "save_settings",
            },
            follow=True,
        )

        settings_row = WhatsAppSettings.get_default()
        self.assertRedirects(response, reverse("whatsapp_settings"))
        self.assertTrue(settings_row.enabled)
        self.assertEqual(settings_row.api_base_url, "http://127.0.0.1:8080")
        self.assertEqual(settings_row.api_key, "whm_test_key_123")

    @patch("core.views.check_api_connection")
    def test_whatsapp_check_api_action_calls_service(self, mock_check_api_connection):
        mock_check_api_connection.return_value = {"ok": True}

        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "settings-enabled": "on",
                "settings-api_base_url": "http://127.0.0.1:8080",
                "settings-api_key": "whm_test_key_123",
                "action": "check_connection",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_check_api_connection.assert_called_once()
        self.assertContains(response, "connection successful")

    @patch("core.views.sync_templates_from_api")
    def test_whatsapp_sync_templates_action_calls_service(self, mock_sync_templates_from_api):
        mock_sync_templates_from_api.return_value = {"synced_count": 5}

        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "settings-enabled": "on",
                "settings-api_base_url": "http://127.0.0.1:8080",
                "settings-api_key": "whm_test_key_123",
                "action": "sync_templates",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_sync_templates_from_api.assert_called_once()
        self.assertContains(response, "Templates synced: 5")

    @patch("core.views.send_test_whatsapp_message")
    def test_whatsapp_send_test_action_calls_service(self, mock_send_test_whatsapp_message):
        mock_send_test_whatsapp_message.return_value = {"sent": True, "phone_number": "919876543210"}

        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "message-test_phone_number": "919876543210",
                "message-test_message_text": "Test ping",
                "message-test_template_name": "",
                "message-test_template_params": "{}",
                "action": "send_test_message",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_send_test_whatsapp_message.assert_called_once()
        self.assertContains(response, "Test message sent to 919876543210")

    @patch("core.views.send_test_template_message")
    def test_whatsapp_send_template_test_action_calls_service(self, mock_send_test_template_message):
        mock_send_test_template_message.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "template_name": "order_accepted_template",
        }
        WhatsAppTemplate.objects.create(name="order_accepted_template", language="en")

        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "message-test_phone_number": "919876543210",
                "message-test_message_text": "Test ping",
                "message-test_template_name": "order_accepted_template",
                "message-test_template_params": '{"name":"Mathukai","order_id":"SR1001"}',
                "action": "send_test_template",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_send_test_template_message.assert_called_once()
        self.assertContains(response, "Template test message sent")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_send_webhook_test_action_returns_success_message(self):
        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "settings-enabled": "on",
                "settings-api_base_url": "http://127.0.0.1:8080",
                "settings-api_key": "whm_test_key_123",
                "action": "send_webhook_test",
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("whatsapp_settings"))
        self.assertContains(response, "Webhook test delivered successfully")

    def test_whatsapp_settings_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("whatsapp_settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    @override_settings(WHATOMATE_WEBHOOK_TOKEN="")
    def test_whatsapp_settings_shows_missing_webhook_token_status(self):
        response = self.client.get(reverse("whatsapp_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Webhook Token: Missing")

    @override_settings(WHATOMATE_WEBHOOK_TOKEN="demo-webhook-token")
    def test_whatsapp_settings_shows_configured_webhook_token_status(self):
        response = self.client.get(reverse("whatsapp_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Webhook Token: Configured")

    @patch("core.views.send_queue_alert_test")
    def test_whatsapp_send_alert_test_action(self, mock_send_alert_test):
        mock_send_alert_test.return_value = {
            "status": "sent",
            "email_sent": 1,
            "whatsapp_sent": 1,
            "message": "Queue alert test sent.",
        }
        response = self.client.post(
            reverse("whatsapp_settings"),
            {
                "action": "send_alert_test",
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_send_alert_test.assert_called_once()
        self.assertContains(response, "Queue alert test sent")

    @patch("core.views.process_whatsapp_notification_queue")
    def test_whatsapp_process_queue_once_action(self, mock_process_queue):
        mock_process_queue.return_value = {
            "picked": 2,
            "processed": 2,
            "success": 1,
            "retried": 1,
            "failed": 0,
            "worker": "ui_settings:test",
        }
        response = self.client.post(
            reverse("whatsapp_settings"),
            {"action": "process_queue_once", "limit": "10"},
            follow=True,
        )

        self.assertRedirects(response, reverse("whatsapp_settings"))
        mock_process_queue.assert_called_once()
        self.assertContains(response, "Queue processed")

    def test_whatsapp_settings_shows_diagnostics_section(self):
        WhatsAppNotificationQueue.objects.create(
            shiprocket_order_id="SR-DIAG-1",
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
            last_error="Connection failed",
        )

        response = self.client.get(reverse("whatsapp_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WhatsApp Diagnostics")
        self.assertContains(response, "Last API Error")
        self.assertContains(response, "Process Queue Once")
        self.assertContains(response, "ops-settings-action-row")
        self.assertContains(response, "ops-admin-table")


class WhatsAppDeliveryLogsViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="logadmin", password="testpass123")
        self.client.force_login(user)

    def test_delivery_logs_page_renders(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LOG-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            previous_status=ShiprocketOrder.STATUS_PACKED,
            current_status=ShiprocketOrder.STATUS_SHIPPED,
            phone_number="919876543210",
            mode="template",
            template_name="shipment_confirmation_1",
            is_success=True,
            request_payload={"contact_id": "abc123"},
            response_payload={"status": "success"},
        )

        response = self.client.get(reverse("whatsapp_delivery_logs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WhatsApp Delivery Logs")
        self.assertContains(response, "SR-LOG-1")
        self.assertContains(response, "shipment_confirmation_1")
        self.assertContains(response, "ops-log-filter-form")
        self.assertContains(response, "whatsapp-log-table")
        self.assertContains(response, 'data-label="Order"', html=False)

    def test_delivery_logs_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("whatsapp_delivery_logs"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_delivery_logs_csv_export_respects_filters(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-CSV-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            is_success=True,
            phone_number="919900000001",
            template_name="order_confirmed_1",
            delivery_status="delivered",
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            is_success=False,
            phone_number="919900000002",
            template_name="order_confirmed_1",
            error_message="Failed",
        )

        response = self.client.get(
            reverse("whatsapp_delivery_logs_csv"),
            {"result": "success"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        self.assertIn("SR-CSV-1", body)
        self.assertIn("success", body)
        self.assertNotIn("919900000002", body)


class AuditExportViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="auditadmin", password="testpass123")
        self.client.force_login(user)

    def test_audit_export_contains_status_manual_and_resend_rows(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-AUDIT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )
        OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            previous_status=ShiprocketOrder.STATUS_NEW,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            title="Status updated",
            description="Moved to accepted",
            is_success=True,
            triggered_by="tester",
        )
        OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
            title="Manual update",
            description="Address corrected",
            is_success=True,
            triggered_by="tester",
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            phone_number="919900000100",
            is_success=True,
            triggered_by="tester",
            external_message_id="msg_123",
            delivery_status="sent",
        )

        today = timezone.localdate().isoformat()
        response = self.client.get(
            reverse("audit_export_csv"),
            {"from_date": today, "to_date": today},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode("utf-8")
        self.assertIn("SR-AUDIT-1", body)
        self.assertIn("status_change", body)
        self.assertIn("manual_update", body)
        self.assertIn("resend", body)


class WebhookTestHelperTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["localhost"])
    def test_internal_webhook_test_uses_explicit_allowed_host(self):
        result = _send_internal_webhook_test(_build_webhook_test_payload(), host="localhost")
        self.assertEqual(result.get("status_code"), 200)
        self.assertTrue(result.get("payload", {}).get("ok"))


class WhatsAppWebhookSyncTests(TestCase):
    def test_webhook_creates_delivery_status_log_for_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_phone="919876543210",
            shipping_address={"phone": "919876543210"},
        )
        payload = {
            "event_id": "evt_001",
            "event_type": "message_status",
            "delivery_status": "delivered",
            "message_id": "msg_abc_123",
            "phone_number": "919876543210",
            "order_id": order.shiprocket_order_id,
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            shiprocket_order_id=order.shiprocket_order_id,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.delivery_status, "delivered")
        self.assertEqual(log.external_message_id, "msg_abc_123")
        self.assertEqual(log.webhook_event_id, "evt_001")
        self.assertTrue(log.is_success)
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_WEBHOOK,
                is_success=True,
            ).exists()
        )

    def test_webhook_duplicate_event_id_is_ignored(self):
        payload = {
            "event_id": "evt_dup_001",
            "delivery_status": "read",
            "phone_number": "919999999999",
        }

        first = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        second = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            WhatsAppNotificationLog.objects.filter(
                trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
                webhook_event_id="evt_dup_001",
            ).count(),
            1,
        )
        self.assertContains(second, '"duplicate": true', status_code=200)

    def test_order_detail_shows_whatsapp_timeline(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-TL-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            current_status=ShiprocketOrder.STATUS_SHIPPED,
            delivery_status="read",
            phone_number="919876543210",
            external_message_id="msg_timeline_1",
            is_success=True,
        )

        response = self.client.get(reverse("order_detail", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Activity Timeline")
        self.assertContains(response, "WhatsApp Timeline")
        self.assertContains(response, "read")


class WebhookDiagnosticsViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="webhookdiag", password="testpass123")
        self.client.force_login(user)

    def test_webhook_diagnostics_page_renders_recent_events(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-DIAG-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            webhook_event_id="evt_diag_1",
            delivery_status="delivered",
            is_success=True,
            request_payload={"event_id": "evt_diag_1"},
        )

        response = self.client.get(reverse("webhook_diagnostics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Webhook Diagnostics")
        self.assertContains(response, "Recent Webhook Events")
        self.assertContains(response, "evt_diag_1")
        self.assertContains(response, "SR-WEBHOOK-DIAG-1")
        self.assertContains(response, "ops-admin-action-group")
        self.assertContains(response, "ops-admin-table")
        self.assertContains(response, 'data-label="Event ID"', html=False)


    def test_order_detail_activity_filters_apply_event_result_and_date(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ACTIVITY-FILTER-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )
        old_log = OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            title="Old status event",
            previous_status=ShiprocketOrder.STATUS_PACKED,
            current_status=ShiprocketOrder.STATUS_SHIPPED,
            is_success=True,
        )
        OrderActivityLog.objects.filter(pk=old_log.pk).update(created_at=timezone.now() - timedelta(days=5))

        OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
            title="Recent queue failure",
            current_status=ShiprocketOrder.STATUS_SHIPPED,
            is_success=False,
        )

        today = timezone.localdate().isoformat()
        response = self.client.get(
            reverse("order_detail", args=[order.pk]),
            {
                "activity_event": OrderActivityLog.EVENT_WHATSAPP_QUEUE_FAILED,
                "activity_result": "failed",
                "activity_from": today,
                "activity_to": today,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent queue failure")
        self.assertNotContains(response, "Old status event")


class AdminUtilitiesViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="utiladmin", password="testpass123")
        self.client.force_login(user)

    def test_admin_utilities_page_renders(self):
        response = self.client.get(reverse("admin_utilities"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Utilities")
        self.assertContains(response, "Queue Operations")
        self.assertContains(response, "Demo Data Cleanup")
        self.assertContains(response, "ops-admin-action-group")

    @patch("core.views.process_whatsapp_notification_queue")
    def test_admin_utilities_process_queue_action(self, mock_process_queue):
        mock_process_queue.return_value = {
            "picked": 1,
            "processed": 1,
            "success": 1,
            "retried": 0,
            "failed": 0,
            "worker": "admin_utilities:test",
        }

        response = self.client.post(
            reverse("admin_utilities"),
            {"action": "process_queue_once", "limit": "5"},
            follow=True,
        )

        self.assertRedirects(response, reverse("admin_utilities"))
        mock_process_queue.assert_called_once()
        self.assertContains(response, "Queue processed")

    def test_admin_utilities_clear_demo_data_action(self):
        ShiprocketOrder.objects.create(shiprocket_order_id="DEMO-UTIL-1", local_status=ShiprocketOrder.STATUS_NEW)
        response = self.client.post(
            reverse("admin_utilities"),
            {"action": "clear_demo_data"},
            follow=True,
        )

        self.assertRedirects(response, reverse("admin_utilities"))
        self.assertFalse(ShiprocketOrder.objects.filter(shiprocket_order_id="DEMO-UTIL-1").exists())
        self.assertContains(response, "Demo data cleared")


class WhatsAppQueueProcessingTests(TestCase):
    def setUp(self):
        settings_row = WhatsAppSettings.get_default()
        settings_row.enabled = True
        settings_row.save(update_fields=["enabled", "updated_at"])

    @patch("core.whatsapp_queue.send_order_status_update")
    def test_queue_processor_marks_success_and_creates_log(self, mock_send_order_status_update):
        mock_send_order_status_update.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "template",
            "template_name": "shipment_confirmation_1",
            "request_payload": {"template_name": "shipment_confirmation_1"},
            "response_payload": {"status": "success"},
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-OK-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543210",
            shipping_address={"phone": "9876543210"},
        )
        enqueue_result = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            previous_status=ShiprocketOrder.STATUS_NEW,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )
        self.assertTrue(enqueue_result["queued"])
        job = enqueue_result["job"]

        summary = process_whatsapp_notification_queue(limit=5, worker_name="test-worker")

        job.refresh_from_db()
        self.assertEqual(summary["success"], 1)
        self.assertEqual(job.status, WhatsAppNotificationQueue.STATUS_SUCCESS)
        self.assertEqual(job.attempt_count, 1)
        self.assertTrue(
            WhatsAppNotificationLog.objects.filter(
                shiprocket_order_id=order.shiprocket_order_id,
                trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
                is_success=True,
            ).exists()
        )
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SUCCESS,
                is_success=True,
            ).exists()
        )

    @patch("core.whatsapp_queue.send_order_status_update")
    def test_queue_processor_retries_then_fails(self, mock_send_order_status_update):
        mock_send_order_status_update.side_effect = WhatomateNotificationError("network timeout")
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-FAIL-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543211",
            shipping_address={"phone": "9876543211"},
        )
        enqueue_result = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
            max_attempts=2,
        )
        self.assertTrue(enqueue_result["queued"])
        job = enqueue_result["job"]

        first_summary = process_whatsapp_notification_queue(limit=5, worker_name="test-worker")
        job.refresh_from_db()
        self.assertEqual(first_summary["retried"], 1)
        self.assertEqual(job.status, WhatsAppNotificationQueue.STATUS_RETRYING)
        self.assertEqual(job.attempt_count, 1)
        self.assertIsNotNone(job.next_retry_at)

        job.next_retry_at = None
        job.save(update_fields=["next_retry_at"])
        second_summary = process_whatsapp_notification_queue(limit=5, worker_name="test-worker")
        job.refresh_from_db()
        self.assertEqual(second_summary["failed"], 1)
        self.assertEqual(job.status, WhatsAppNotificationQueue.STATUS_FAILED)
        self.assertEqual(job.attempt_count, 2)
        self.assertIn("network timeout", job.last_error)

    def test_enqueue_prevents_duplicate_pending_jobs(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-DUP-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543212",
            shipping_address={"phone": "9876543212"},
        )
        first = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )
        second = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )

        self.assertTrue(first["queued"])
        self.assertFalse(second["queued"])
        self.assertEqual(second["reason"], "duplicate_pending")

    def test_enqueue_skips_when_same_notification_already_sent(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-QUEUE-SENT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543213",
            shipping_address={"phone": "9876543213"},
        )
        first = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )
        self.assertTrue(first["queued"])
        job = first["job"]
        job.status = WhatsAppNotificationQueue.STATUS_FAILED
        job.save(update_fields=["status"])

        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            phone_number=job.phone_number,
            mode=job.mode,
            template_name=job.template_name,
            template_id=job.template_id,
            idempotency_key=job.idempotency_key,
            is_success=True,
        )

        second = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )
        self.assertFalse(second["queued"])
        self.assertEqual(second["reason"], "already_sent")


class OrderNotificationConfigViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="notificationadmin", password="testpass123")
        self.client.force_login(user)

    def test_page_renders_with_status_rows(self):
        WhatsAppTemplate.objects.create(name="order_accepted_template", language="en_US")

        response = self.client.get(reverse("order_notification_config"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Status Notification Templates")
        self.assertContains(response, "Order Accepted")
        self.assertContains(response, "Shipped")
        self.assertContains(response, "Template Preview")
        self.assertContains(response, "order-notification-config-table")
        self.assertContains(response, "template-id-cell")
        self.assertContains(response, "ops-config-action-row")
        self.assertContains(response, 'data-label="Order Status"', html=False)
        self.assertContains(response, "const enabledCheckbox = row.querySelector")
        self.assertIn("function renderPreview()", html)
        self.assertLess(
            html.index("function renderPreview()"),
            html.index("if (!tokens.length)"),
        )

    def test_save_status_template_mapping(self):
        WhatsAppTemplate.objects.create(name="order_accepted_template", language="en_US")

        payload = {}
        for status_key, _ in ShiprocketOrder.STATUS_CHOICES:
            prefix = f"status-{status_key}"
            payload[f"{prefix}-enabled"] = ""
            payload[f"{prefix}-template_name"] = ""
            payload[f"{prefix}-template_id"] = ""
            payload[f"{prefix}-template_param_mapping"] = "{}"
        payload["status-order_accepted-enabled"] = "on"
        payload["status-order_accepted-template_name"] = "order_accepted_template"
        payload["status-order_accepted-template_param_mapping"] = '{"1":"customer_name","2":"order_id"}'

        response = self.client.post(reverse("order_notification_config"), payload, follow=True)

        self.assertRedirects(response, reverse("order_notification_config"))
        config = WhatsAppStatusTemplateConfig.objects.get(local_status=ShiprocketOrder.STATUS_ACCEPTED)
        self.assertTrue(config.enabled)
        self.assertEqual(config.template_name, "order_accepted_template")
        self.assertEqual(config.template_param_mapping, {"1": "customer_name", "2": "order_id"})

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("order_notification_config"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class GeneralPageResponsiveViewTests(TestCase):
    def test_contact_page_has_responsive_form_shell(self):
        response = self.client.get(reverse("contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "responsive-form-card")
        self.assertContains(response, "responsive-form-actions")

    def test_auth_pages_have_responsive_form_shell(self):
        login_response = self.client.get(reverse("login"))
        signup_response = self.client.get(reverse("signup"))

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(signup_response.status_code, 200)
        self.assertContains(login_response, "responsive-form-card")
        self.assertContains(signup_response, "responsive-form-card")

    def test_project_pages_have_responsive_layout_hooks(self):
        project = Project.objects.create(name="Mobile Test Project", description="Responsive check")

        list_response = self.client.get(reverse("project_list"))
        detail_response = self.client.get(reverse("project_detail", args=[project.pk]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(list_response, "responsive-page-header")
        self.assertContains(list_response, "responsive-project-card")
        self.assertContains(detail_response, "responsive-form-actions")
        self.assertContains(detail_response, "responsive-form-card")


class SeedDemoOrdersCommandTests(TestCase):
    def test_seed_demo_orders_creates_expected_rows(self):
        call_command("seed_demo_orders", "--count", "5")

        self.assertEqual(
            ShiprocketOrder.objects.filter(shiprocket_order_id__startswith="DEMO-ST4-").count(),
            5,
        )
        self.assertTrue(ShiprocketOrder.objects.filter(local_status=ShiprocketOrder.STATUS_PACKED).exists())
        self.assertTrue(SenderAddress.objects.exists())


class PruneOpsDataCommandTests(TestCase):
    def test_prune_ops_data_dry_run_reports_counts(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PRUNE-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )
        old_time = timezone.now() - timedelta(days=120)
        success_log = WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            is_success=True,
        )
        failure_log = WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            is_success=False,
            error_message="Old failure",
        )
        queue_job = WhatsAppNotificationQueue.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            status=WhatsAppNotificationQueue.STATUS_SUCCESS,
        )
        OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_WHATSAPP_QUEUE_SUCCESS,
            is_success=True,
        )
        WhatsAppNotificationLog.objects.filter(pk__in=[success_log.pk, failure_log.pk]).update(created_at=old_time)
        OrderActivityLog.objects.all().update(created_at=old_time)
        WhatsAppNotificationQueue.objects.filter(pk=queue_job.pk).update(updated_at=old_time)

        stdout = StringIO()
        call_command("prune_ops_data", "--dry-run", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Ops prune complete", output)
        self.assertTrue(WhatsAppNotificationLog.objects.filter(pk=success_log.pk).exists())
        self.assertTrue(WhatsAppNotificationLog.objects.filter(pk=failure_log.pk).exists())


class BackupRestoreCommandTests(TestCase):
    def test_restore_local_data_dry_run(self):
        base_dir = Path(__file__).resolve().parents[1]
        backup_dir = base_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / "test_restore_archive.zip"
        with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("db.sqlite3", b"fake-db")
            archive.writestr("logs/app.log", b"log-line")

        stdout = StringIO()
        call_command(
            "restore_local_data",
            "--archive",
            str(archive_path),
            "--dry-run",
            stdout=stdout,
        )

        self.assertIn("Restore plan", stdout.getvalue())


class RuntimeCleanupCommandTests(TestCase):
    def test_cleanup_runtime_files_dry_run(self):
        stdout = StringIO()
        call_command(
            "cleanup_runtime_files",
            "--heartbeat-days",
            "1",
            "--log-days",
            "1",
            "--dry-run",
            stdout=stdout,
        )
        self.assertIn("Runtime cleanup done", stdout.getvalue())


class PreflightCheckCommandTests(TestCase):
    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["ops.example.com"],
        CSRF_TRUSTED_ORIGINS=["https://ops.example.com"],
        SHIPROCKET_EMAIL="shiprocket@example.com",
        SHIPROCKET_PASSWORD="secret",
        WHATOMATE_API_KEY="whm_key",
        WHATOMATE_ACCESS_TOKEN="",
        WHATOMATE_WEBHOOK_TOKEN="token123",
    )
    def test_preflight_check_passes_with_valid_config(self):
        stdout = StringIO()
        call_command("preflight_check", stdout=stdout)
        self.assertIn("Preflight passed", stdout.getvalue())

    @override_settings(
        DEBUG=True,
        ALLOWED_HOSTS=[],
        SHIPROCKET_EMAIL="",
        SHIPROCKET_PASSWORD="",
        WHATOMATE_API_KEY="",
        WHATOMATE_ACCESS_TOKEN="",
        WHATOMATE_WEBHOOK_TOKEN="",
    )
    def test_preflight_check_strict_fails(self):
        with self.assertRaises(CommandError):
            call_command("preflight_check", "--strict")


class WhatsAppStatusTemplateMappingTests(TestCase):
    def test_numeric_placeholders_use_configured_field_mapping(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-MAP-1",
            channel_order_id="CH-1001",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_name="Fallback Name",
            customer_phone="9000001111",
            tracking_number="TRK1234567890",
            shipping_address={
                "name": "Mapped Customer",
                "phone": "9000009999",
            },
        )
        placeholders = ["1", "2", "3"]
        mapping = {
            "1": "customer_name",
            "2": "tracking_number",
            "3": "phone",
        }

        params = _build_template_params_for_status(placeholders, order, field_mapping=mapping)

        self.assertEqual(
            params,
            {
                "1": "Mapped Customer",
                "2": "TRK1234567890",
                "3": "9000009999",
            },
        )


class RoleAccessTests(TestCase):
    def setUp(self):
        self.viewer_group, _ = Group.objects.get_or_create(name="ops_viewer")
        self.admin_group, _ = Group.objects.get_or_create(name="admin")
        self.viewer = get_user_model().objects.create_user(username="viewer", password="testpass123")
        self.viewer.groups.add(self.viewer_group)
        self.admin = get_user_model().objects.create_user(username="opsadmin", password="testpass123")
        self.admin.groups.add(self.admin_group)

    @patch("core.views.enqueue_whatsapp_notification")
    def test_ops_viewer_can_update_order_status(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertContains(response, "Order moved to the selected tab.")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_admin_group_can_update_order_status(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-ADMIN-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        self.client.force_login(self.admin)

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)

    def test_ops_viewer_home_redirects_to_order_management(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("order_management"))

    def test_ops_viewer_sidebar_shows_order_management_and_stock_management_only(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-LIST-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            customer_name="Viewer Mobile",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Viewer Mobile", "address_1": "Sample Street 1"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("order_management"))
        self.assertContains(response, "My Orders")
        self.assertContains(response, "ops-desktop-shell")
        self.assertContains(response, "Sort by: Latest Order")
        self.assertContains(response, "Completed")
        self.assertContains(response, f'order-{order.pk}-manual_customer_phone')
        self.assertContains(response, "Customer phone for Accept action")
        self.assertContains(response, "ops-order-card")
        self.assertContains(response, reverse("order_detail", args=[order.pk]))
        self.assertNotContains(response, 'data-row-update-form', html=False)
        self.assertContains(response, reverse("stock_management"))
        self.assertContains(response, "Stock Management")
        self.assertNotContains(response, reverse("print_queue"))
        self.assertNotContains(response, reverse("admin_utilities"))

    def test_ops_viewer_order_detail_uses_mobile_workflow_ui(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Mobile Detail",
                "phone": "9876543210",
                "email": "mobile@example.com",
                "address_1": "42 Demo Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Details")
        self.assertContains(response, "ops-detail-step")
        self.assertContains(response, "Accept Order")
        self.assertContains(response, "Reject Order")

    def test_ops_viewer_can_access_stock_management_screen(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock Management")
        self.assertContains(response, "Stock List")
        self.assertContains(response, "Manage Stock")
        self.assertContains(response, "ops-stock-shell")
        self.assertContains(response, "Low Stock Products")
        self.assertContains(response, "Stock Qty Table")

    def test_ops_viewer_manage_stock_tab_shows_management_tools(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"), {"view": "manage"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Stock Update")
        self.assertContains(response, "Reconcile Accepted Orders")
        self.assertContains(response, 'name="return_view" value="manage"', html=False)

    def test_ops_viewer_completed_tab_shows_delivered_orders(self):
        delivered_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-COMPLETE-1",
            local_status=ShiprocketOrder.STATUS_DELIVERED,
            customer_name="Completed Customer",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Completed Customer", "address_1": "Completed Street"},
        )
        shipped_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-SHIPPED-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_name="Shipped Customer",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Shipped Customer", "address_1": "Shipped Street"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": "completed"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, delivered_order.shiprocket_order_id)
        self.assertNotContains(response, shipped_order.shiprocket_order_id)

    def test_ops_viewer_can_update_stock_from_stock_management(self):
        product = Product.objects.create(
            name="Viewer Stock Product",
            sku="VIEW-STOCK-1",
            stock_quantity=5,
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "VIEW-STOCK-1",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 2,
                "notes": "viewer stock update",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 7)
        self.assertContains(response, "2 unit(s) added to Viewer Stock Product")

    def test_admin_sidebar_shows_full_navigation(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("stock_management"))
        self.assertContains(response, reverse("product_categories"))
        self.assertContains(response, "Product Categories")
        self.assertContains(response, reverse("print_queue"))
        self.assertContains(response, reverse("admin_utilities"))

    def test_admin_can_manage_product_categories_in_app_ui(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("product_categories"),
            {
                "form_action": "save_category",
                "name": "Soap",
                "is_active": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("product_categories"))
        self.assertTrue(ProductCategory.objects.filter(name="Soap", is_active=True).exists())
        self.assertContains(response, "Saved product category Soap.")

    def test_ops_viewer_cannot_access_product_categories(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("product_categories"), follow=True)

        self.assertRedirects(response, reverse("order_management"))
        self.assertContains(response, "cannot access product categories")

    @patch("core.views.sync_orders")
    def test_ops_viewer_can_run_shiprocket_sync_from_order_management(self, mock_sync_orders):
        mock_sync_orders.return_value = 3
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("sync_shiprocket_orders"),
            {
                "return_to": "order_management",
                "return_query": "tab=new_order",
            },
            follow=True,
        )

        mock_sync_orders.assert_called_once()
        self.assertRedirects(response, f"{reverse('order_management')}?tab=new_order#order-management-section")
        self.assertContains(response, "Shiprocket sync completed. 3 orders refreshed.")

    def test_ops_viewer_sees_sync_button_on_order_management(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sync")


class StockManagementViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="stockuser", password="testpass123")
        self.client.force_login(self.user)

    def test_stock_management_can_create_product(self):
        category = ProductCategory.objects.create(name="Herbal Drink")
        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "save_product",
                "name": "Amla Juice",
                "category_master": category.pk,
                "sku": "sku-create-1",
                "smartbiz_product_id": "smartbiz-product-1",
                "barcode": "890000000010",
                "stock_quantity": 14,
                "reorder_level": 3,
                "is_active": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product = Product.objects.get(sku="SKU-CREATE-1")
        self.assertEqual(product.category_master, category)
        self.assertEqual(product.category, "Herbal Drink")
        self.assertEqual(product.barcode, "890000000010")
        self.assertEqual(product.smartbiz_product_id, "smartbiz-product-1")
        self.assertEqual(product.stock_quantity, 14)
        self.assertContains(response, "Saved product Amla Juice")

    def test_stock_management_shows_category_in_product_table(self):
        Product.objects.create(
            name="Goat Milk Soap",
            category="Soap",
            sku="SKU-CATEGORY-1",
            stock_quantity=8,
        )

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Category")
        self.assertContains(response, "Soap")

    def test_stock_management_can_filter_products_by_category(self):
        soap = ProductCategory.objects.create(name="Soap")
        drink = ProductCategory.objects.create(name="Drink")
        Product.objects.create(
            name="Goat Milk Soap",
            category_master=soap,
            sku="SKU-SOAP-1",
            stock_quantity=8,
        )
        Product.objects.create(
            name="Amla Juice",
            category_master=drink,
            sku="SKU-DRINK-1",
            stock_quantity=5,
        )

        response = self.client.get(reverse("stock_management"), {"category": soap.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Goat Milk Soap")
        self.assertNotContains(response, "Amla Juice")

    def test_stock_management_can_adjust_stock_by_barcode(self):
        product = Product.objects.create(
            name="Neem Powder",
            sku="SKU-BARCODE-1",
            barcode="890000000011",
            stock_quantity=5,
        )

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "890000000011",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 4,
                "notes": "Barcode scan add",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 9)
        self.assertContains(response, "4 unit(s) added to Neem Powder")

    def test_stock_management_can_adjust_stock_by_smartbiz_product_id(self):
        product = Product.objects.create(
            name="SmartBiz Product",
            sku="SKU-SMARTBIZ-1",
            smartbiz_product_id="06d3d905-2768-4f8c-8ce5-22c7fed3c54d",
            stock_quantity=7,
        )

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "06d3d905-2768-4f8c-8ce5-22c7fed3c54d",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 2,
                "notes": "SmartBiz mapping lookup",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 9)
        self.assertContains(response, "2 unit(s) added to SmartBiz Product")

    def test_stock_management_can_bulk_map_smartbiz_ids_by_sku(self):
        first_product = Product.objects.create(
            name="Goat Milk Soap",
            sku="MTHKS01",
            stock_quantity=5,
        )
        second_product = Product.objects.create(
            name="Aloe Vera Soap",
            sku="MTHKS02",
            stock_quantity=6,
        )

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "bulk_map_smartbiz",
                "mapping_text": (
                    "MTHKS01,06d3d905-2768-4f8c-8ce5-22c7fed3c54d\n"
                    "MTHKS02\tsecond-smartbiz-id"
                ),
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        first_product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(first_product.smartbiz_product_id, "06d3d905-2768-4f8c-8ce5-22c7fed3c54d")
        self.assertEqual(second_product.smartbiz_product_id, "second-smartbiz-id")
        self.assertContains(response, "Updated SmartBiz mapping for 2 product(s).")

    def test_stock_management_can_reconcile_missing_stock_deduction_for_accepted_order(self):
        product = Product.objects.create(
            name="Goat Milk Soap",
            sku="MTHKS01",
            smartbiz_product_id="06d3d905-2768-4f8c-8ce5-22c7fed3c54d",
            stock_quantity=10,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-RECON-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            order_items=[
                {"sku": "06d3d905-2768-4f8c-8ce5-22c7fed3c54d", "quantity": 2},
            ],
        )

        response = self.client.post(
            reverse("stock_management"),
            {"form_action": "reconcile_stock"},
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 8)
        self.assertTrue(
            StockMovement.objects.filter(
                order=order,
                product=product,
                movement_type=StockMovement.TYPE_ORDER_ACCEPTED,
                quantity_delta=-2,
            ).exists()
        )
        self.assertContains(response, "Reconciled missing stock deductions for 1 order(s).")

    def test_stock_management_add_remove_and_set_by_lookup(self):
        product = Product.objects.create(
            name="Herbal Tea",
            sku="SKU-LOOKUP-1",
            barcode="890000000012",
            stock_quantity=8,
        )

        add_response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "SKU-LOOKUP-1",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 3,
                "notes": "Manual inbound",
            },
            follow=True,
        )
        self.assertRedirects(add_response, reverse("stock_management"))

        remove_response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "890000000012",
                "action": StockAdjustmentForm.ACTION_REMOVE,
                "quantity": 2,
                "notes": "Damaged units",
            },
            follow=True,
        )
        self.assertRedirects(remove_response, reverse("stock_management"))

        set_response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "SKU-LOOKUP-1",
                "action": StockAdjustmentForm.ACTION_SET,
                "quantity": 15,
                "notes": "Cycle count",
            },
            follow=True,
        )
        self.assertRedirects(set_response, reverse("stock_management"))

        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 15)
        self.assertContains(add_response, "3 unit(s) added to Herbal Tea")
        self.assertContains(remove_response, "2 unit(s) removed from Herbal Tea")
        self.assertContains(set_response, "Set stock for Herbal Tea")
        self.assertEqual(
            list(
                StockMovement.objects.filter(product=product).values_list("movement_type", flat=True)
            ),
            [
                StockMovement.TYPE_MANUAL_SET,
                StockMovement.TYPE_MANUAL_REMOVE,
                StockMovement.TYPE_MANUAL_ADD,
            ],
        )

    def test_stock_management_set_same_quantity_shows_no_change_message(self):
        product = Product.objects.create(
            name="Spice Mix",
            sku="SKU-NOCHANGE-1",
            stock_quantity=6,
        )

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "SKU-NOCHANGE-1",
                "action": StockAdjustmentForm.ACTION_SET,
                "quantity": 6,
                "notes": "Cycle count no-op",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 6)
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertContains(response, "already 6. No change needed")


class IntegrationSmokeTriggerViewTests(TestCase):
    def setUp(self):
        self.viewer_group, _ = Group.objects.get_or_create(name="ops_viewer")
        self.admin_group, _ = Group.objects.get_or_create(name="admin")
        self.viewer = get_user_model().objects.create_user(username="smokeviewer", password="testpass123")
        self.viewer.groups.add(self.viewer_group)
        self.admin = get_user_model().objects.create_user(username="smokeadmin", password="testpass123")
        self.admin.groups.add(self.admin_group)
        base_dir = Path(__file__).resolve().parents[1]
        backup_dir = base_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        self.latest_backup = backup_dir / "local_backup_20990101_010101.zip"
        with ZipFile(self.latest_backup, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("db.sqlite3", b"fake-db")

    def test_run_smoke_requires_login(self):
        response = self.client.post(reverse("run_integration_smoke"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(reverse("login"), response.request["PATH_INFO"])

    @patch("core.views.call_command")
    def test_admin_can_trigger_smoke(self, mock_call_command):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("run_integration_smoke"), follow=True)
        self.assertRedirects(response, reverse("home"))
        mock_call_command.assert_called_once()
        self.assertContains(response, "Integration smoke completed")

    @patch("core.views.call_command")
    def test_viewer_cannot_trigger_smoke(self, mock_call_command):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("run_integration_smoke"), follow=True)
        self.assertRedirects(response, reverse("home"))
        mock_call_command.assert_not_called()
        self.assertContains(response, "read-only access")

    @patch("core.views.call_command")
    def test_admin_can_trigger_restore_dry_run(self, mock_call_command):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("run_restore_dry_run"), follow=True)
        self.assertRedirects(response, reverse("home"))
        mock_call_command.assert_called_once()
        self.assertContains(response, "Restore dry-run completed")

    @patch("core.views.call_command")
    def test_viewer_cannot_trigger_restore_dry_run(self, mock_call_command):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("run_restore_dry_run"), follow=True)
        self.assertRedirects(response, reverse("home"))
        mock_call_command.assert_not_called()
        self.assertContains(response, "read-only access")


class StatusUpdateSoftLockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="softlock", password="testpass123")
        self.client.force_login(self.user)

    @patch("core.views.cache.add", return_value=False)
    def test_duplicate_status_update_is_blocked(self, mock_cache_add):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-SOFTLOCK-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "9876543210",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)
        self.assertTrue(mock_cache_add.called)
        self.assertContains(response, "Duplicate status update blocked")


class HealthEndpointTests(TestCase):
    def test_healthz_returns_ok_payload(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("checks", payload)
        self.assertIn("database", payload["checks"])
        self.assertIn("queue", payload["checks"])


class MetricsEndpointTests(TestCase):
    def test_metrics_returns_prometheus_text(self):
        response = self.client.get(reverse("metrics"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        body = response.content.decode("utf-8")
        self.assertIn("mathukai_health_ok", body)
        self.assertIn("mathukai_queue_failed", body)
        self.assertIn("mathukai_webhook_freshness_minutes", body)

    @override_settings(METRICS_TOKEN="metrics-secret")
    def test_metrics_requires_token_when_configured(self):
        unauthorized = self.client.get(reverse("metrics"))
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.get(reverse("metrics"), HTTP_X_METRICS_TOKEN="metrics-secret")
        self.assertEqual(authorized.status_code, 200)


class WebhookStaleBannerTests(TestCase):
    @override_settings(WEBHOOK_STALE_MINUTES=30)
    def test_admin_sees_stale_webhook_banner(self):
        admin_group, _ = Group.objects.get_or_create(name="admin")
        admin_user = get_user_model().objects.create_user(username="staleadmin", password="testpass123")
        admin_user.groups.add(admin_group)
        webhook_log = WhatsAppNotificationLog.objects.create(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            is_success=True,
            delivery_status="delivered",
        )
        old_time = timezone.now() - timedelta(minutes=31)
        WhatsAppNotificationLog.objects.filter(pk=webhook_log.pk).update(created_at=old_time)

        self.client.force_login(admin_user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Webhook is stale")

    @override_settings(WEBHOOK_STALE_MINUTES=30)
    def test_non_admin_does_not_see_stale_banner(self):
        viewer_group, _ = Group.objects.get_or_create(name="ops_viewer")
        user = get_user_model().objects.create_user(username="stalenonadmin", password="testpass123")
        user.groups.add(viewer_group)
        webhook_log = WhatsAppNotificationLog.objects.create(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            is_success=True,
            delivery_status="delivered",
        )
        old_time = timezone.now() - timedelta(minutes=31)
        WhatsAppNotificationLog.objects.filter(pk=webhook_log.pk).update(created_at=old_time)

        self.client.force_login(user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Webhook is stale")


class WhatsAppPayloadVisibilityTests(TestCase):
    def setUp(self):
        self.viewer_group, _ = Group.objects.get_or_create(name="ops_viewer")
        self.admin_group, _ = Group.objects.get_or_create(name="admin")
        self.viewer = get_user_model().objects.create_user(username="payloadviewer", password="testpass123")
        self.viewer.groups.add(self.viewer_group)
        self.admin = get_user_model().objects.create_user(username="payloadadmin", password="testpass123")
        self.admin.groups.add(self.admin_group)
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PAYLOAD-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )
        WhatsAppNotificationLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_STATUS,
            delivery_status="delivered",
            is_success=True,
            request_payload={"sample": "request"},
            response_payload={"sample": "response"},
        )

    def test_viewer_sees_payload_hidden_message(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("whatsapp_delivery_logs"))
        self.assertContains(response, "Payload hidden (admin only)")

    def test_admin_sees_payload_details(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("whatsapp_delivery_logs"))
        self.assertContains(response, "Webhook Payload")
        self.assertContains(response, "Request")
        self.assertContains(response, "Response")


class RoleBootstrapCommandTests(TestCase):
    def test_bootstrap_roles_command_creates_groups(self):
        Group.objects.filter(name__in=["admin", "ops_viewer"]).delete()
        call_command("bootstrap_roles")
        self.assertTrue(Group.objects.filter(name="admin").exists())
        self.assertTrue(Group.objects.filter(name="ops_viewer").exists())


class WhatsAppQueueAlertCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        self.order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ALERT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )

    @override_settings(
        WHATSAPP_ALERTS_ENABLED="true",
        WHATSAPP_ALERT_FAILED_THRESHOLD=1,
        WHATSAPP_ALERT_COOLDOWN_MINUTES=30,
        WHATSAPP_ALERT_EMAIL_TO="ops@example.com",
        WHATSAPP_ALERT_WHATSAPP_TO="919999999999",
    )
    @patch("core.queue_alerts.send_test_whatsapp_message")
    @patch("core.queue_alerts.send_mail")
    def test_alert_command_sends_email_and_whatsapp(self, mock_send_mail, mock_send_whatsapp):
        WhatsAppNotificationQueue.objects.create(
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
            attempt_count=3,
            max_attempts=3,
        )
        stdout = StringIO()
        call_command("check_whatsapp_queue_alerts", "--worker", "test-worker", stdout=stdout)

        self.assertIn("status=sent", stdout.getvalue())
        mock_send_mail.assert_called_once()
        mock_send_whatsapp.assert_called_once()

    @override_settings(
        WHATSAPP_ALERTS_ENABLED="true",
        WHATSAPP_ALERT_FAILED_THRESHOLD=3,
        WHATSAPP_ALERT_EMAIL_TO="ops@example.com",
    )
    @patch("core.queue_alerts.send_mail")
    def test_alert_command_skips_below_threshold(self, mock_send_mail):
        WhatsAppNotificationQueue.objects.create(
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
            attempt_count=3,
            max_attempts=3,
        )
        stdout = StringIO()
        call_command("check_whatsapp_queue_alerts", "--worker", "test-worker", stdout=stdout)

        self.assertIn("status=below_threshold", stdout.getvalue())
        mock_send_mail.assert_not_called()


class ErrorDigestCommandTests(TestCase):
    def setUp(self):
        self.order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-DIGEST-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )

    def test_error_digest_prints_summary(self):
        OrderActivityLog.objects.create(
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_STATUS_CHANGE,
            title="Failed status update",
            description="Shiprocket timeout",
            is_success=False,
            triggered_by="tester",
        )
        WhatsAppNotificationLog.objects.create(
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            is_success=False,
            error_message="Whatomate API 500",
        )
        WhatsAppNotificationQueue.objects.create(
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            status=WhatsAppNotificationQueue.STATUS_FAILED,
            last_error="Queue exhausted",
        )
        stdout = StringIO()
        call_command("send_error_digest", "--hours", "24", stdout=stdout)
        text = stdout.getvalue()
        self.assertIn("Activity failures:", text)
        self.assertIn("WhatsApp log failures:", text)
        self.assertIn("Queue failed rows:", text)

    @patch("core.management.commands.send_error_digest.send_mail")
    def test_error_digest_can_send_email(self, mock_send_mail):
        stdout = StringIO()
        call_command(
            "send_error_digest",
            "--hours",
            "24",
            "--send-email",
            "--email-to",
            "ops@example.com",
            stdout=stdout,
        )
        self.assertTrue(mock_send_mail.called)
        self.assertIn("Digest email sent", stdout.getvalue())


class IncidentSnapshotCommandTests(TestCase):
    def test_incident_snapshot_exports_json(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-SNAPSHOT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )
        OrderActivityLog.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
            title="Manual update",
            description="Edited address",
            is_success=True,
            triggered_by="tester",
        )
        output_path = Path(__file__).resolve().parents[1] / "logs" / "incidents" / "test_snapshot.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()

        call_command(
            "export_incident_snapshot",
            "--hours",
            "24",
            "--limit",
            "50",
            "--out-file",
            str(output_path),
        )
        self.assertTrue(output_path.exists())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertIn("health", payload)
        self.assertIn("system_status", payload)
        self.assertIn("recent", payload)
