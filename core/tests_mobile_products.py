import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import Product, Tenant, TenantMembership, TenantWooCommerceMappingRule


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileProductListApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="product-list-user")
        self.tenant = Tenant.objects.create(name="Product Tenant", slug="product-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Products", slug="other-products")
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )
        session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        token, _ = issue_access_token(session)
        self.headers = {"Authorization": f"Bearer {token}"}

    def product(self, suffix, *, tenant=None, quantity=10, reorder=5, **values):
        return Product.objects.create(
            tenant=tenant or self.tenant,
            name=values.pop("name", f"Product {suffix}"),
            sku=f"MOBILE-PROD-{suffix}",
            barcode=values.pop("barcode", f"890000{suffix}"),
            stock_quantity=quantity,
            reorder_level=reorder,
            **values,
        )

    def get(self, params=None):
        return self.client.get("/api/v1/products", params or {}, headers=self.headers)

    def test_list_is_tenant_scoped_and_returns_read_only_summary(self):
        own = self.product(
            "OWN",
            quantity=2,
            image_url="https://example.com/product.jpg",
            category="Organic",
        )
        self.product("OTHER", tenant=self.other_tenant, quantity=0)

        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 1)
        row = response.json()["data"][0]
        self.assertEqual(row["id"], own.pk)
        self.assertEqual(row["stock_state"], "low_stock")
        self.assertEqual(row["category"], "Organic")
        self.assertEqual(row["image_url"], "https://example.com/product.jpg")
        self.assertNotIn("actual_price", row)
        self.assertNotIn("woocommerce_product_id", row)

    def test_name_sku_and_barcode_search(self):
        named = self.product("NAME", name="Special Turmeric", barcode="111111")
        sku = self.product("SKU", barcode="222222")
        barcode = self.product("BARCODE", barcode="333333")

        by_name = self.get({"search": "Turmeric"})
        by_sku = self.get({"search": "MOBILE-PROD-SKU"})
        by_barcode = self.get({"search": "333333"})

        self.assertEqual([row["id"] for row in by_name.json()["data"]], [named.pk])
        self.assertEqual([row["id"] for row in by_sku.json()["data"]], [sku.pk])
        self.assertEqual([row["id"] for row in by_barcode.json()["data"]], [barcode.pk])

    def test_stock_state_filters_are_disjoint(self):
        in_stock = self.product("IN", quantity=10, reorder=5)
        low_stock = self.product("LOW", quantity=3, reorder=5)
        out_of_stock = self.product("OUT", quantity=0, reorder=5)

        results = {
            state: [row["id"] for row in self.get({"stock_state": state}).json()["data"]]
            for state in ["in_stock", "low_stock", "out_of_stock"]
        }

        self.assertEqual(results["in_stock"], [in_stock.pk])
        self.assertEqual(results["low_stock"], [low_stock.pk])
        self.assertEqual(results["out_of_stock"], [out_of_stock.pk])

    def test_updated_after_filters_product_changes(self):
        old = self.product("OLD")
        recent = self.product("RECENT")
        cutoff = timezone.now() - timedelta(hours=1)
        Product.objects.filter(pk=old.pk).update(updated_at=cutoff - timedelta(minutes=1))
        Product.objects.filter(pk=recent.pk).update(updated_at=cutoff + timedelta(minutes=1))

        response = self.get({"updated_after": cutoff.isoformat()})

        self.assertEqual([row["id"] for row in response.json()["data"]], [recent.pk])

    def test_route_ready_uses_direct_identifier_or_matching_tenant_rule(self):
        direct = self.product("DIRECT", smartbiz_product_id="direct-id")
        matched = self.product("RULE-123")
        missing = self.product("MISSING")
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="MOBILE-PROD-RULE",
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.other_tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="MOBILE-PROD-MISSING",
        )

        response = self.get()
        readiness = {row["id"]: row["route_ready"] for row in response.json()["data"]}

        self.assertTrue(readiness[direct.pk])
        self.assertTrue(readiness[matched.pk])
        self.assertFalse(readiness[missing.pk])

    def test_cursor_queries_are_bounded_and_post_is_not_available(self):
        for index in range(30):
            self.product(f"PAGE-{index}")
        with CaptureQueriesContext(connection) as queries:
            response = self.get({"page_size": 25})
        mutation = self.client.post("/api/v1/products", {}, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 25)
        self.assertTrue(response.json()["pagination"]["has_more"])
        self.assertLessEqual(len(queries), 5)
        self.assertEqual(mutation.status_code, 405)
