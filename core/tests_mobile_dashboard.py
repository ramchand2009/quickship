import uuid
from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import (
    Product,
    ShiprocketOrder,
    Tenant,
    TenantMembership,
    TenantWooCommerceMappingRule,
)


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileDashboardApiTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="dashboard-user")
        self.tenant = Tenant.objects.create(name="Dashboard Tenant", slug="dashboard-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Dashboard", slug="other-dashboard")
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
        self.token, _ = issue_access_token(self.session)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def order(self, tenant, suffix, status, **values):
        return ShiprocketOrder.objects.create(
            tenant=tenant,
            shiprocket_order_id=f"DASH-{suffix}",
            local_status=status,
            **values,
        )

    def product(self, tenant, suffix, quantity, reorder=5, routed=False, actual_price=None):
        return Product.objects.create(
            tenant=tenant,
            name=f"Product {suffix}",
            sku=f"DASH-SKU-{suffix}",
            stock_quantity=quantity,
            reorder_level=reorder,
            smartbiz_product_id=f"route-{suffix}" if routed else None,
            actual_price=actual_price,
        )

    def test_dashboard_counts_only_active_tenant_and_returns_cache_metadata(self):
        self.order(self.tenant, "PENDING", ShiprocketOrder.STATUS_NEW)
        self.product(self.tenant, "PROFIT", 20, routed=True, actual_price="25.00")
        self.order(
            self.tenant,
            "ACCEPTED",
            ShiprocketOrder.STATUS_ACCEPTED,
            total="175.00",
            order_items=[{"sku": "DASH-SKU-PROFIT", "quantity": 1, "price": "75.00"}],
        )
        self.order(self.tenant, "SHIPPED", ShiprocketOrder.STATUS_SHIPPED)
        self.order(self.tenant, "COMPLETED", ShiprocketOrder.STATUS_COMPLETED)
        self.order(self.tenant, "CANCELLED", ShiprocketOrder.STATUS_CANCELLED)
        self.order(self.tenant, "ISSUE", ShiprocketOrder.STATUS_DELIVERY_ISSUE)
        previous_month = self.order(self.tenant, "PREVIOUS", ShiprocketOrder.STATUS_NEW)
        previous_month.order_date = timezone.now() - timedelta(days=40)
        previous_month.save(update_fields=["order_date"])
        self.order(self.other_tenant, "OTHER", ShiprocketOrder.STATUS_NEW)
        self.product(self.tenant, "LOW", 2)
        self.product(self.tenant, "ROUTED", 20, routed=True)
        self.product(self.other_tenant, "OTHER", 0)

        response = self.client.get("/api/v1/dashboard", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        metric_rows = {row["key"]: row for row in response.json()["data"]["metrics"]}
        metrics = {key: row["value"] for key, row in metric_rows.items()}
        self.assertEqual(metrics["total_orders"], 6)
        self.assertEqual(metrics["pending_orders"], 1)
        self.assertEqual(metrics["accepted_orders"], 1)
        self.assertEqual(metrics["shipped_orders"], 1)
        self.assertEqual(metrics["completed_orders"], 1)
        self.assertEqual(metrics["cancelled_orders"], 1)
        self.assertEqual(metrics["total_sales"], "₹ 175.00")
        self.assertEqual(metrics["total_profit"], "₹ 50.00")
        self.assertIn("status=shipped", metric_rows["shipped_orders"]["destination"])
        self.assertIn("date_from=", metric_rows["shipped_orders"]["destination"])
        self.assertIn("date_to=", metric_rows["shipped_orders"]["destination"])
        self.assertEqual(
            set(metrics),
            {
                "total_orders",
                "pending_orders",
                "accepted_orders",
                "shipped_orders",
                "completed_orders",
                "cancelled_orders",
                "total_sales",
                "total_profit",
            },
        )
        alert_ids = {row["id"] for row in response.json()["data"]["alerts"]}
        self.assertIn("orders:delivery_issue", alert_ids)
        self.assertIn("stock:low", alert_ids)
        self.assertIn("routing:missing", alert_ids)
        self.assertIn("cache_expires_at", response.json()["meta"])
        self.assertIn("ETag", response)
        self.assertEqual(response["Cache-Control"], "private, max-age=30")

    def test_all_roles_receive_the_monthly_order_and_financial_metrics(self):
        expected_keys = {
            "total_orders",
            "pending_orders",
            "accepted_orders",
            "shipped_orders",
            "completed_orders",
            "cancelled_orders",
            "total_sales",
            "total_profit",
        }
        for role in (
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
            TenantMembership.ROLE_VENDOR_VIEWER,
            TenantMembership.ROLE_WAREHOUSE_OPERATOR,
        ):
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                response = self.client.get("/api/v1/dashboard", headers=self.headers)
                keys = {row["key"] for row in response.json()["data"]["metrics"]}
                self.assertEqual(keys, expected_keys)

    def test_etag_returns_not_modified_without_leaking_cross_tenant_state(self):
        self.order(self.tenant, "CACHE", ShiprocketOrder.STATUS_NEW)
        first = self.client.get("/api/v1/dashboard", headers=self.headers)
        self.order(self.other_tenant, "CACHE-OTHER", ShiprocketOrder.STATUS_NEW)

        cached = self.client.get(
            "/api/v1/dashboard",
            headers={**self.headers, "If-None-Match": first["ETag"]},
        )

        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached["ETag"], first["ETag"])

    @override_settings(MOBILE_READ_API_ENABLED=False)
    def test_read_api_kill_switch_hides_dashboard(self):
        response = self.client.get("/api/v1/dashboard", headers=self.headers)

        self.assertEqual(response.status_code, 404)

    def test_dashboard_query_count_is_bounded(self):
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="DASH",
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/v1/dashboard", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 7)
