import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import (
    Product,
    ShiprocketOrder,
    StockMovement,
    Tenant,
    TenantMembership,
    TenantWooCommerceMappingRule,
)


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


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileProductDetailAndMovementApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="product-detail-user")
        self.tenant = Tenant.objects.create(name="Product Detail", slug="product-detail")
        self.other_tenant = Tenant.objects.create(name="Other Product Detail", slug="other-product-detail")
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
        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Detailed Product",
            sku="DETAIL-SKU-1",
            barcode="444444",
            description="Safe product description",
            actual_price="45.00",
            regular_price="100.00",
            sale_price="80.00",
            stock_quantity=8,
            reorder_level=3,
            woocommerce_product_id="woo-product-1",
            woocommerce_variation_id="woo-variation-1",
        )
        self.order = ShiprocketOrder.objects.create(
            tenant=self.tenant,
            shiprocket_order_id="PRODUCT-MOVEMENT-ORDER",
        )
        self.movement = StockMovement.objects.create(
            tenant=self.tenant,
            product=self.product,
            order=self.order,
            movement_type=StockMovement.TYPE_MANUAL_ADD,
            quantity_delta=3,
            quantity_before=5,
            quantity_after=8,
            notes="Private stock note",
            triggered_by="stock-owner@example.com",
        )

    def get_product(self, product=None):
        target = product or self.product
        return self.client.get(f"/api/v1/products/{target.pk}", headers=self.headers)

    def get_movements(self, params=None):
        return self.client.get("/api/v1/stock/movements", params or {}, headers=self.headers)

    def test_product_detail_role_price_and_routing_policy(self):
        expected = {
            TenantMembership.ROLE_VENDOR_OWNER: {
                "actual": True,
                "regular": True,
                "sale": True,
                "routing_ids": True,
            },
            TenantMembership.ROLE_VENDOR_OPERATOR: {
                "actual": False,
                "regular": True,
                "sale": True,
                "routing_ids": False,
            },
            TenantMembership.ROLE_VENDOR_VIEWER: {
                "actual": False,
                "regular": True,
                "sale": True,
                "routing_ids": False,
            },
            TenantMembership.ROLE_WAREHOUSE_OPERATOR: {
                "actual": False,
                "regular": False,
                "sale": False,
                "routing_ids": False,
            },
        }
        for role, visibility in expected.items():
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                data = self.get_product().json()["data"]
                self.assertEqual(data["description"], "Safe product description")
                self.assertTrue(data["routing"]["ready"])
                self.assertEqual(data["prices"]["actual"] is not None, visibility["actual"])
                self.assertEqual(data["prices"]["regular"] is not None, visibility["regular"])
                self.assertEqual(data["prices"]["sale"] is not None, visibility["sale"])
                self.assertEqual(
                    data["routing"]["woocommerce_product_id"] is not None,
                    visibility["routing_ids"],
                )

    def test_product_detail_is_tenant_scoped_and_query_bounded(self):
        other = Product.objects.create(
            tenant=self.other_tenant,
            name="Other Secret Product",
            sku="OTHER-DETAIL-SKU",
        )
        with CaptureQueriesContext(connection) as queries:
            own = self.get_product()
        forbidden = self.get_product(other)

        self.assertEqual(own.status_code, 200)
        self.assertLessEqual(len(queries), 5)
        self.assertEqual(forbidden.status_code, 404)

    def test_movement_field_visibility_and_safe_order_reference(self):
        other_order = ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="CROSS-TENANT-MOVEMENT-ORDER",
        )
        poisoned = StockMovement.objects.create(
            tenant=self.tenant,
            product=self.product,
            order=other_order,
            movement_type=StockMovement.TYPE_MANUAL_REMOVE,
            quantity_delta=-1,
            quantity_before=8,
            quantity_after=7,
            notes="Operational warehouse note",
            triggered_by="hidden-actor@example.com",
        )
        expected = {
            TenantMembership.ROLE_VENDOR_OWNER: ("Private stock note", "stock-owner@example.com"),
            TenantMembership.ROLE_VENDOR_OPERATOR: ("Private stock note", "stock-owner@example.com"),
            TenantMembership.ROLE_VENDOR_VIEWER: (None, None),
            TenantMembership.ROLE_WAREHOUSE_OPERATOR: ("Private stock note", None),
        }
        for role, (note, actor) in expected.items():
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                rows = self.get_movements({"product_id": self.product.pk}).json()["data"]
                by_id = {row["id"]: row for row in rows}
                self.assertEqual(by_id[self.movement.pk]["note"], note)
                self.assertEqual(by_id[self.movement.pk]["actor_display_name"], actor)
                self.assertEqual(by_id[self.movement.pk]["order_id"], self.order.pk)
                self.assertIsNone(by_id[poisoned.pk]["order_id"])

    def test_movement_filters_cursor_and_query_count(self):
        cutoff = timezone.now() - timedelta(hours=1)
        StockMovement.objects.filter(pk=self.movement.pk).update(created_at=cutoff - timedelta(minutes=1))
        recent = StockMovement.objects.create(
            tenant=self.tenant,
            product=self.product,
            movement_type=StockMovement.TYPE_MANUAL_SET,
            quantity_delta=0,
            quantity_before=8,
            quantity_after=8,
        )
        other_product = Product.objects.create(
            tenant=self.tenant,
            name="Movement Filter Product",
            sku="MOVEMENT-FILTER-SKU",
        )
        StockMovement.objects.create(
            tenant=self.tenant,
            product=other_product,
            movement_type=StockMovement.TYPE_MANUAL_ADD,
            quantity_delta=1,
            quantity_after=1,
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.get_movements(
                {"product_id": self.product.pk, "updated_after": cutoff.isoformat()}
            )

        self.assertEqual([row["id"] for row in response.json()["data"]], [recent.pk])
        self.assertLessEqual(len(queries), 4)

    def test_movement_cursor_paginates_without_duplicates(self):
        for index in range(25):
            StockMovement.objects.create(
                tenant=self.tenant,
                product=self.product,
                movement_type=StockMovement.TYPE_MANUAL_ADD,
                quantity_delta=1,
                quantity_before=index,
                quantity_after=index + 1,
            )

        first = self.get_movements({"product_id": self.product.pk, "page_size": 10}).json()
        second = self.get_movements(
            {
                "product_id": self.product.pk,
                "page_size": 10,
                "cursor": first["pagination"]["next_cursor"],
            }
        ).json()
        third = self.get_movements(
            {
                "product_id": self.product.pk,
                "page_size": 10,
                "cursor": second["pagination"]["next_cursor"],
            }
        ).json()

        ids = [row["id"] for page in [first, second, third] for row in page["data"]]
        self.assertEqual(len(ids), 26)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(third["pagination"]["has_more"])

    def test_detail_and_movement_mutations_are_not_available(self):
        detail_post = self.client.post(
            f"/api/v1/products/{self.product.pk}",
            {},
            headers=self.headers,
        )
        movement_post = self.client.post(
            "/api/v1/stock/movements",
            {},
            headers=self.headers,
        )

        self.assertEqual(detail_post.status_code, 405)
        self.assertEqual(movement_post.status_code, 405)
