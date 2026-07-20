import copy
import uuid
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import Product, ShiprocketOrder, StockMovement, Tenant, TenantMembership


OPENAPI_PATH = (
    Path(settings.BASE_DIR)
    / "QuickShip_SaaS_V4_Enterprise_Engineering_Handbook"
    / "docs"
    / "08_api"
    / "mobile_phase1_openapi.yaml"
)


class OpenApiContractMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with OPENAPI_PATH.open(encoding="utf-8") as openapi_file:
            cls.openapi = yaml.safe_load(openapi_file)
        cls.openapi_resource = copy.deepcopy(cls.openapi)
        cls.openapi_resource["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        cls.registry = Registry().with_resource(
            "urn:quickship:mobile-openapi",
            Resource.from_contents(cls.openapi_resource),
        )

    def assert_contract(self, response, schema_name):
        validator = Draft202012Validator(
            {"$ref": f"urn:quickship:mobile-openapi#/components/schemas/{schema_name}"},
            registry=self.registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(response.json()), key=lambda error: list(error.path))
        if errors:
            details = "\n".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors
            )
            self.fail(f"Response does not match {schema_name}:\n{details}")


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileReadContractTests(OpenApiContractMixin, TestCase):
    roles = (
        TenantMembership.ROLE_VENDOR_OWNER,
        TenantMembership.ROLE_VENDOR_OPERATOR,
        TenantMembership.ROLE_VENDOR_VIEWER,
        TenantMembership.ROLE_WAREHOUSE_OPERATOR,
    )

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="read-contract-user")
        self.tenant = Tenant.objects.create(name="Contract Tenant", slug="contract-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Contract Tenant", slug="other-contract-tenant")
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
        access_token, _ = issue_access_token(session)
        self.headers = {"Authorization": f"Bearer {access_token}"}

        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Contract Product",
            sku="CONTRACT-SKU-1",
            barcode="890000000001",
            description="Contract-safe description",
            image_url="https://example.com/contract-product.jpg",
            actual_price="40.00",
            regular_price="60.00",
            sale_price="50.00",
            stock_quantity=8,
            reorder_level=3,
            woocommerce_product_id="contract-woo-product",
            woocommerce_variation_id="contract-woo-variation",
        )
        self.order = ShiprocketOrder.objects.create(
            tenant=self.tenant,
            shiprocket_order_id="CONTRACT-ORDER-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Contract Customer",
            customer_email="contract@example.com",
            customer_phone="9000000000",
            shipping_address={"address_1": "1 Contract Street", "city": "Chennai"},
            order_items=[
                {
                    "product_id": self.product.pk,
                    "name": "Contract Product",
                    "sku": self.product.sku,
                    "quantity": 1,
                    "price": "50.00",
                    "image_url": "https://example.com/contract-product.jpg",
                }
            ],
            total="50.00",
            shipping_base_amount="10.00",
            tracking_number="CONTRACT-TRACK-1",
        )
        StockMovement.objects.create(
            tenant=self.tenant,
            product=self.product,
            order=self.order,
            movement_type=StockMovement.TYPE_MANUAL_ADD,
            quantity_delta=3,
            quantity_before=5,
            quantity_after=8,
            notes="Contract movement",
            triggered_by="contract-operator@example.com",
        )
        self.other_product = Product.objects.create(
            tenant=self.other_tenant,
            name="Other Product",
            sku="OTHER-CONTRACT-SKU",
        )
        self.other_order = ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="OTHER-CONTRACT-ORDER",
        )

    def endpoints(self):
        return (
            ("/api/v1/dashboard", "DashboardResponse"),
            ("/api/v1/orders", "OrderListResponse"),
            (f"/api/v1/orders/{self.order.pk}", "OrderDetailResponse"),
            ("/api/v1/products", "ProductListResponse"),
            (f"/api/v1/products/{self.product.pk}", "ProductDetailResponse"),
            ("/api/v1/stock/movements", "StockMovementListResponse"),
        )

    def test_every_read_contract_for_all_roles(self):
        for role in self.roles:
            self.membership.role = role
            self.membership.save(update_fields=["role"])
            for path, schema_name in self.endpoints():
                with self.subTest(role=role, path=path):
                    response = self.client.get(path, headers=self.headers)
                    self.assertEqual(response.status_code, 200)
                    self.assert_contract(response, schema_name)

    def test_every_read_operation_has_the_standard_error_contract(self):
        for path, _schema_name in self.endpoints():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assert_contract(response, "ErrorResponse")

    def test_semantic_read_errors_match_the_standard_contract(self):
        no_tenant_user = get_user_model().objects.create_user(username="contract-no-tenant")
        no_tenant_session = create_mobile_session(
            user=no_tenant_user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=None,
        )
        no_tenant_token, _ = issue_access_token(no_tenant_session)
        cases = (
            (
                self.client.get(
                    "/api/v1/orders",
                    {"date_from": "2026-07-20", "date_to": "2026-07-19"},
                    headers=self.headers,
                ),
                400,
            ),
            (
                self.client.get(
                    "/api/v1/dashboard",
                    headers={"Authorization": f"Bearer {no_tenant_token}"},
                ),
                403,
            ),
            (self.client.get(f"/api/v1/orders/{self.other_order.pk}", headers=self.headers), 404),
            (self.client.get(f"/api/v1/products/{self.other_product.pk}", headers=self.headers), 404),
        )
        for response, expected_status in cases:
            with self.subTest(status=expected_status, path=response.wsgi_request.path):
                self.assertEqual(response.status_code, expected_status)
                self.assert_contract(response, "ErrorResponse")

    @override_settings(MOBILE_READ_API_ENABLED=False)
    def test_read_kill_switch_error_matches_contract(self):
        response = self.client.get("/api/v1/dashboard", headers=self.headers)

        self.assertEqual(response.status_code, 404)
        self.assert_contract(response, "ErrorResponse")
