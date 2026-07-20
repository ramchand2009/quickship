import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import OrderActivityLog, ShiprocketOrder, Tenant, TenantMembership


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


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileOrderDetailApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="order-detail-user")
        self.tenant = Tenant.objects.create(name="Detail Tenant", slug="detail-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Detail", slug="other-detail")
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
        self.order = ShiprocketOrder.objects.create(
            tenant=self.tenant,
            shiprocket_order_id="MOBILE-DETAIL-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Private Customer",
            customer_email="private@example.com",
            customer_phone="9876543210",
            shipping_address={
                "name": "Private Customer",
                "email": "private@example.com",
                "phone": "9876543210",
                "address_1": "10 Private Street",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "pincode": "600001",
                "country": "India",
            },
            order_items=[
                {
                    "product_id": 42,
                    "name": "Organic Item",
                    "sku": "ORG-42",
                    "quantity": 2,
                    "price": "75.25",
                    "image_url": "https://example.com/item.jpg",
                }
            ],
            total="150.50",
            shipping_base_amount="20.00",
            raw_payload={
                "courier_name": "Safe Courier",
                "consumer_secret": "must-never-leak",
            },
        )
        self.activity = OrderActivityLog.objects.create(
            tenant=self.tenant,
            order=self.order,
            shiprocket_order_id=self.order.shiprocket_order_id,
            title="Order accepted",
            description="Ready for dispatch",
            previous_status=ShiprocketOrder.STATUS_NEW,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            triggered_by="operator@example.com",
        )

    def get(self, order=None):
        target = order or self.order
        return self.client.get(f"/api/v1/orders/{target.pk}", headers=self.headers)

    def test_owner_detail_matches_normalized_contract_and_excludes_raw_payload(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["customer"]["name"], "Private Customer")
        self.assertEqual(data["customer"]["phone"], "9876543210")
        self.assertEqual(data["customer"]["email"], "private@example.com")
        self.assertIn("10 Private Street", data["customer"]["delivery_address"])
        self.assertEqual(data["items"][0]["total"], {"amount": "150.50", "currency": "INR"})
        self.assertEqual(data["courier_name"], "Safe Courier")
        self.assertEqual(data["shipping_cost"], {"amount": "20.00", "currency": "INR"})
        self.assertEqual(data["activity"][0]["id"], self.activity.pk)
        self.assertEqual(data["activity"][0]["actor_display_name"], "operator@example.com")
        response_text = response.content.decode("utf-8")
        self.assertNotIn("consumer_secret", response_text)
        self.assertNotIn("must-never-leak", response_text)

    def test_role_field_snapshots_and_allowed_actions(self):
        expectations = {
            TenantMembership.ROLE_VENDOR_OWNER: {
                "name": "Private Customer",
                "phone": "9876543210",
                "email": "private@example.com",
                "address": True,
                "actions": True,
                "actor": "operator@example.com",
            },
            TenantMembership.ROLE_VENDOR_OPERATOR: {
                "name": "Private Customer",
                "phone": "9876543210",
                "email": "private@example.com",
                "address": True,
                "actions": True,
                "actor": "operator@example.com",
            },
            TenantMembership.ROLE_VENDOR_VIEWER: {
                "name": "P•••",
                "phone": None,
                "email": None,
                "address": False,
                "actions": False,
                "actor": None,
            },
            TenantMembership.ROLE_WAREHOUSE_OPERATOR: {
                "name": "Private Customer",
                "phone": "9876543210",
                "email": None,
                "address": True,
                "actions": False,
                "actor": None,
            },
        }
        for role, expected in expectations.items():
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                data = self.get().json()["data"]
                customer = data["customer"]
                self.assertEqual(customer["name"], expected["name"])
                self.assertEqual(customer["phone"], expected["phone"])
                self.assertEqual(customer["email"], expected["email"])
                self.assertEqual(customer["delivery_address"] is not None, expected["address"])
                self.assertEqual(bool(data["allowed_actions"]), expected["actions"])
                self.assertEqual(data["activity"][0]["actor_display_name"], expected["actor"])
                action_targets = {
                    action["target_status"]
                    for action in data["allowed_actions"]
                    if action["code"] == "update_status"
                }
                self.assertNotIn(ShiprocketOrder.STATUS_PACKED, action_targets)

    def test_owner_actions_include_shipping_requirements_and_payment(self):
        actions = self.get().json()["data"]["allowed_actions"]
        shipped = next(
            action for action in actions if action.get("target_status") == ShiprocketOrder.STATUS_SHIPPED
        )
        cancelled = next(
            action for action in actions if action.get("target_status") == ShiprocketOrder.STATUS_CANCELLED
        )
        payment = next(action for action in actions if action["code"] == "mark_payment_received")

        self.assertEqual(
            shipped["required_fields"],
            ["courier_name", "tracking_number", "shipping_base_amount"],
        )
        self.assertTrue(cancelled["reason_required"])
        self.assertTrue(payment["confirmation_required"])

    def test_cross_tenant_id_and_cross_tenant_activity_are_not_exposed(self):
        other_order = ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="MOBILE-DETAIL-OTHER",
        )
        OrderActivityLog.objects.create(
            tenant=self.other_tenant,
            order=self.order,
            title="Cross tenant poison",
            description="must not appear",
        )

        forbidden = self.get(other_order)
        own = self.get()

        self.assertEqual(forbidden.status_code, 404)
        self.assertNotIn("Cross tenant poison", own.content.decode("utf-8"))

    def test_detail_query_count_is_bounded(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 5)
