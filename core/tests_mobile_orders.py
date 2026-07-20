import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import ShiprocketOrder, Tenant, TenantMembership


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileOrderListApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="order-list-user")
        self.tenant = Tenant.objects.create(name="Order Tenant", slug="order-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Orders", slug="other-orders")
        self.membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        token, _ = issue_access_token(session)
        self.headers = {"Authorization": f"Bearer {token}"}

    def order(self, suffix, *, tenant=None, status=None, **values):
        return ShiprocketOrder.objects.create(
            tenant=tenant or self.tenant,
            shiprocket_order_id=f"MOBILE-{suffix}",
            local_status=status or ShiprocketOrder.STATUS_NEW,
            customer_name=values.pop("customer_name", "Allowed Customer"),
            customer_email=values.pop("customer_email", "allowed@example.com"),
            customer_phone=values.pop("customer_phone", "9000000000"),
            order_items=values.pop(
                "order_items",
                [{"name": "Item", "quantity": 2}],
            ),
            order_date=values.pop("order_date", timezone.now()),
            **values,
        )

    def get(self, params=None):
        return self.client.get("/api/v1/orders", params or {}, headers=self.headers)

    def test_list_is_tenant_scoped_and_matches_safe_summary_contract(self):
        own = self.order(
            "OWN",
            status=ShiprocketOrder.STATUS_DELIVERY_ISSUE,
            total="125.50",
            tracking_number="TRACK-OWN",
        )
        self.order("OTHER", tenant=self.other_tenant)

        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 1)
        row = response.json()["data"][0]
        self.assertEqual(row["id"], own.pk)
        self.assertEqual(row["reference"], "MOBILE-OWN")
        self.assertEqual(row["status"], {"code": "delivery_issue", "label": "Delivery Issue"})
        self.assertEqual(row["payment_state"]["code"], "pending")
        self.assertEqual(row["item_count"], 2)
        self.assertEqual(row["total"], {"amount": "125.50", "currency": "INR"})
        self.assertTrue(row["attention_required"])
        self.assertEqual(row["version"], "1")
        self.assertNotIn("raw_payload", row)
        self.assertNotIn("shipping_address", row)
        self.assertEqual(response.json()["pagination"]["has_more"], False)
        self.assertIn("request_id", response.json()["meta"])

    def test_status_payment_and_date_filters_apply_together(self):
        now = timezone.now()
        matching = self.order(
            "MATCH",
            status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_received_at=now,
            order_date=now - timedelta(days=1),
        )
        self.order("PENDING", status=ShiprocketOrder.STATUS_ACCEPTED, order_date=now)
        self.order("OLD", status=ShiprocketOrder.STATUS_ACCEPTED, payment_received_at=now, order_date=now - timedelta(days=10))
        self.order("WRONG-STATUS", status=ShiprocketOrder.STATUS_NEW, payment_received_at=now)

        response = self.get(
            {
                "status": ShiprocketOrder.STATUS_ACCEPTED,
                "payment_state": "received",
                "date_from": (now - timedelta(days=2)).date().isoformat(),
                "date_to": now.date().isoformat(),
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["data"]], [matching.pk])

    def test_updated_after_and_invalid_date_range(self):
        old = self.order("OLD-UPDATE")
        recent = self.order("RECENT-UPDATE")
        cutoff = timezone.now() - timedelta(hours=1)
        ShiprocketOrder.objects.filter(pk=old.pk).update(updated_at=cutoff - timedelta(hours=1))
        ShiprocketOrder.objects.filter(pk=recent.pk).update(updated_at=cutoff + timedelta(minutes=1))

        filtered = self.get({"updated_after": cutoff.isoformat()})
        invalid = self.get({"date_from": "2026-07-20", "date_to": "2026-07-19"})

        self.assertEqual([row["id"] for row in filtered.json()["data"]], [recent.pk])
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("date_to", invalid.json()["error"]["fields"])

    def test_customer_search_and_display_follow_role_policy(self):
        order = self.order("CUSTOMER", customer_name="Sensitive Name")
        owner = self.get({"search": "Sensitive Name"})
        self.membership.role = TenantMembership.ROLE_VENDOR_VIEWER
        self.membership.save(update_fields=["role"])
        viewer_customer_search = self.get({"search": "Sensitive Name"})
        viewer_reference_search = self.get({"search": "MOBILE-CUSTOMER"})

        self.assertEqual(owner.json()["data"][0]["customer_display_name"], "Sensitive Name")
        self.assertEqual(viewer_customer_search.json()["data"], [])
        self.assertEqual(viewer_reference_search.json()["data"][0]["id"], order.pk)
        self.assertEqual(viewer_reference_search.json()["data"][0]["customer_display_name"], "S•••")

    def test_cursor_is_stable_when_new_order_arrives(self):
        original = [self.order(f"PAGE-{index}") for index in range(5)]
        expected = [order.pk for order in reversed(original)]

        first = self.get({"page_size": 2}).json()
        self.order("ARRIVED-LATER")
        second = self.get(
            {"page_size": 2, "cursor": first["pagination"]["next_cursor"]}
        ).json()
        third = self.get(
            {"page_size": 2, "cursor": second["pagination"]["next_cursor"]}
        ).json()

        actual = [row["id"] for page in (first, second, third) for row in page["data"]]
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(set(actual)))

    def test_query_count_is_bounded_for_full_page(self):
        for index in range(25):
            self.order(f"QUERY-{index}")

        with CaptureQueriesContext(connection) as queries:
            response = self.get({"page_size": 25})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 25)
        self.assertLessEqual(len(queries), 5)
