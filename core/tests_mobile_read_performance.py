import json
import os
import time
import uuid
from statistics import median
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import Product, ShiprocketOrder, StockMovement, Tenant, TenantMembership


RUN_PERFORMANCE_TESTS = os.environ.get("RUN_MOBILE_PERFORMANCE_TESTS") == "1"


@skipUnless(
    RUN_PERFORMANCE_TESTS,
    "Set RUN_MOBILE_PERFORMANCE_TESTS=1 to run the PostgreSQL benchmark.",
)
@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileReadPostgresPerformanceTests(TestCase):
    orders_per_tenant = 5_000
    products_per_tenant = 2_000
    movements_per_tenant = 10_000
    samples_per_endpoint = 5

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise AssertionError("The mobile performance gate must run against PostgreSQL.")

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mobile-performance-user")
        self.tenant = Tenant.objects.create(name="Performance Tenant", slug="performance-tenant")
        self.other_tenant = Tenant.objects.create(
            name="Performance Control",
            slug="performance-control",
        )
        TenantMembership.objects.create(
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
        self._seed_representative_data()

    def _seed_representative_data(self):
        statuses = (
            ShiprocketOrder.STATUS_NEW,
            ShiprocketOrder.STATUS_ACCEPTED,
            ShiprocketOrder.STATUS_DELIVERY_ISSUE,
            ShiprocketOrder.STATUS_SHIPPED,
            ShiprocketOrder.STATUS_COMPLETED,
        )
        orders = []
        for tenant, prefix in ((self.tenant, "PERF"), (self.other_tenant, "CONTROL")):
            orders.extend(
                ShiprocketOrder(
                    tenant=tenant,
                    shiprocket_order_id=f"{prefix}-ORDER-{index:05d}",
                    local_status=statuses[index % len(statuses)],
                    customer_name=f"Customer {index}",
                    customer_email=f"customer{index}@example.com",
                    customer_phone=f"9{index:09d}",
                    order_items=[{"name": "Benchmark item", "quantity": 1, "price": "50.00"}],
                    total="50.00",
                )
                for index in range(self.orders_per_tenant)
            )
        ShiprocketOrder.objects.bulk_create(orders, batch_size=1_000)

        products = []
        for tenant, prefix in ((self.tenant, "PERF"), (self.other_tenant, "CONTROL")):
            products.extend(
                Product(
                    tenant=tenant,
                    name=f"{prefix} Product {index:05d}",
                    sku=f"{prefix}-SKU-{index:05d}",
                    barcode=f"{1 if prefix == 'PERF' else 2}{index:011d}",
                    stock_quantity=index % 20,
                    reorder_level=5,
                    regular_price="60.00",
                    sale_price="50.00",
                )
                for index in range(self.products_per_tenant)
            )
        Product.objects.bulk_create(products, batch_size=1_000)

        own_products = list(Product.objects.filter(tenant=self.tenant).order_by("pk")[:100])
        other_products = list(Product.objects.filter(tenant=self.other_tenant).order_by("pk")[:100])
        movements = []
        for tenant, tenant_products in (
            (self.tenant, own_products),
            (self.other_tenant, other_products),
        ):
            movements.extend(
                StockMovement(
                    tenant=tenant,
                    product=tenant_products[index % len(tenant_products)],
                    movement_type=StockMovement.TYPE_MANUAL_ADD,
                    quantity_delta=1,
                    quantity_before=index % 20,
                    quantity_after=(index % 20) + 1,
                    triggered_by="performance@example.com",
                )
                for index in range(self.movements_per_tenant)
            )
        StockMovement.objects.bulk_create(movements, batch_size=1_000)

        self.order_id = (
            ShiprocketOrder.objects.filter(tenant=self.tenant)
            .values_list("pk", flat=True)
            .first()
        )
        self.product_id = own_products[0].pk

    def _plan_node_types(self, plan):
        nodes = {plan["Node Type"]}
        for child in plan.get("Plans", []):
            nodes.update(self._plan_node_types(child))
        return sorted(nodes)

    def _explain_selects(self, captured_queries):
        plans = []
        with connection.cursor() as cursor:
            for query in captured_queries:
                sql = query["sql"].lstrip()
                if not sql.upper().startswith("SELECT"):
                    continue
                cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
                explanation = cursor.fetchone()[0]
                root = explanation[0]
                plans.append(
                    {
                        "node": root["Plan"]["Node Type"],
                        "nodes": self._plan_node_types(root["Plan"]),
                        "planning_ms": round(root["Planning Time"], 3),
                        "execution_ms": round(root["Execution Time"], 3),
                    }
                )
        self.assertTrue(plans, "Expected at least one SELECT query to explain.")
        return plans

    def test_representative_read_pages_meet_latency_query_and_plan_targets(self):
        endpoints = (
            ("dashboard", "/api/v1/dashboard", 1_000, 7),
            ("orders", "/api/v1/orders?page_size=25", 1_000, 5),
            ("order_detail", f"/api/v1/orders/{self.order_id}", 500, 5),
            ("products", "/api/v1/products?page_size=25", 500, 5),
            ("product_detail", f"/api/v1/products/{self.product_id}", 500, 5),
            ("stock_movements", "/api/v1/stock/movements?page_size=25", 500, 4),
        )
        report = {
            "dataset": {
                "tenants": 2,
                "orders": self.orders_per_tenant * 2,
                "products": self.products_per_tenant * 2,
                "stock_movements": self.movements_per_tenant * 2,
            },
            "endpoints": {},
        }

        for name, path, target_ms, max_queries in endpoints:
            warmup = self.client.get(path, headers=self.headers)
            self.assertEqual(warmup.status_code, 200)
            samples = []
            captured = None
            for _index in range(self.samples_per_endpoint):
                started = time.perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(path, headers=self.headers)
                elapsed_ms = (time.perf_counter() - started) * 1_000
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(queries), max_queries)
                samples.append(round(elapsed_ms, 3))
                captured = list(queries.captured_queries)

            median_ms = median(samples)
            self.assertLess(
                median_ms,
                target_ms,
                f"{name} median {median_ms:.3f} ms exceeded {target_ms} ms",
            )
            plans = self._explain_selects(captured)
            database_ms = sum(plan["execution_ms"] for plan in plans)
            self.assertLess(
                database_ms,
                target_ms,
                f"{name} database plans took {database_ms:.3f} ms",
            )
            report["endpoints"][name] = {
                "target_ms": target_ms,
                "median_ms": round(median_ms, 3),
                "max_ms": max(samples),
                "queries": len(captured),
                "database_ms": round(database_ms, 3),
                "plans": plans,
            }

        print("MOBILE_READ_PERFORMANCE=" + json.dumps(report, sort_keys=True))
