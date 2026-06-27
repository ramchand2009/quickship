import json
import tempfile
from types import SimpleNamespace
from io import StringIO
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import ShiprocketOrderStatusForm, StockAdjustmentForm
from .models import (
    BusinessExpense,
    ExpensePerson,
    OrderActivityLog,
    Product,
    ProductCategory,
    Project,
    SenderAddress,
    ShiprocketOrder,
    StockMovement,
    Tenant,
    TenantMembership,
    TenantWooCommerceMappingRule,
    WhatsAppNotificationLog,
    WhatsAppNotificationQueue,
    WhatsAppSettings,
    WhatsAppStatusTemplateConfig,
    WhatsAppTemplate,
    WebPushSubscription,
    WooCommerceSettings,
)
from .access import (
    TenantScopedQuerysetMixin,
    can_access_tenant,
    can_manage_vendor_settings,
    can_operate_vendor_orders,
    can_update_order_status,
    get_active_tenant,
    get_user_default_tenant,
    is_ops_admin,
    is_ops_viewer,
    is_super_admin,
    is_vendor_user,
)
from .middleware import ActiveTenantMiddleware
from .stock import summarize_order_profit
from .system_status import write_system_heartbeat
from .whatomate import (
    WhatomateNotificationError,
    build_order_payment_reminder_idempotency_payload,
    build_order_status_idempotency_payload,
    _build_template_params_for_status,
    _create_contact,
    _get_headers,
    check_api_connection,
    send_order_enquiry_reply,
    send_order_status_update,
    send_test_template_message,
    send_test_whatsapp_message,
    sync_templates_from_api,
)
from .views import _build_webhook_test_payload, _build_woocommerce_webhook_signature, _send_internal_webhook_test
from .whatsapp_queue import enqueue_whatsapp_notification, process_whatsapp_notification_queue
from .shiprocket import sync_orders
from .woocommerce import WooCommerceAPIError
from .woocommerce import import_order_payload as import_woocommerce_order_payload
from .woocommerce import sync_orders as sync_woocommerce_orders
from .woocommerce import sync_products as sync_woocommerce_products
from .woocommerce import update_order_status as update_woocommerce_order_status
from .woocommerce import woocommerce_status_for_local_status


class TenantFoundationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.vendor_user = self.user_model.objects.create_user(username="vendor", password="pass")
        self.other_user = self.user_model.objects.create_user(username="other", password="pass")
        self.super_user = self.user_model.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass",
        )
        self.mathukai = Tenant.get_default()
        self.other_tenant = Tenant.objects.create(name="Other Vendor", slug="other-vendor")

    def test_default_tenant_is_mathukai(self):
        tenant = Tenant.get_default()

        self.assertEqual(tenant.name, "Mathukai")
        self.assertEqual(tenant.slug, "mathukai")
        self.assertTrue(tenant.is_active)

    def test_business_models_default_to_mathukai_tenant(self):
        order = ShiprocketOrder.objects.create(shiprocket_order_id="TENANT-ORDER-1")
        product = Product.objects.create(name="Tenant Product", sku="TENANT-PRODUCT-1")

        self.assertEqual(order.tenant, self.mathukai)
        self.assertEqual(product.tenant, self.mathukai)

    def test_vendor_membership_grants_tenant_access(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )

        self.assertEqual(get_user_default_tenant(self.vendor_user), self.mathukai)
        self.assertTrue(can_access_tenant(self.vendor_user, self.mathukai))
        self.assertTrue(can_operate_vendor_orders(self.vendor_user, self.mathukai))
        self.assertFalse(can_manage_vendor_settings(self.vendor_user, self.mathukai))
        self.assertFalse(can_access_tenant(self.vendor_user, self.other_tenant))
        self.assertTrue(is_vendor_user(self.vendor_user))
        self.assertTrue(is_ops_viewer(self.vendor_user))
        self.assertFalse(is_ops_admin(self.vendor_user))

    def test_vendor_owner_can_manage_vendor_settings(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )

        self.assertTrue(can_manage_vendor_settings(self.vendor_user, self.mathukai))
        self.assertTrue(can_operate_vendor_orders(self.vendor_user, self.mathukai))

    def test_inactive_membership_does_not_grant_access(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
            is_active=False,
        )

        self.assertIsNone(get_user_default_tenant(self.vendor_user))
        self.assertFalse(can_access_tenant(self.vendor_user, self.mathukai))
        self.assertFalse(is_vendor_user(self.vendor_user))
        self.assertFalse(is_ops_admin(self.vendor_user))
        self.assertFalse(is_ops_viewer(self.vendor_user))
        self.assertFalse(can_update_order_status(self.vendor_user))

    def test_inactive_tenant_does_not_grant_vendor_access(self):
        inactive_tenant = Tenant.objects.create(name="Inactive Vendor", slug="inactive-vendor", is_active=False)
        TenantMembership.objects.create(
            tenant=inactive_tenant,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )

        self.assertIsNone(get_user_default_tenant(self.vendor_user))
        self.assertFalse(can_access_tenant(self.vendor_user, inactive_tenant))
        self.assertFalse(is_vendor_user(self.vendor_user))
        self.assertFalse(is_ops_admin(self.vendor_user))
        self.assertFalse(can_update_order_status(self.vendor_user))

    def test_super_admin_can_access_all_tenants_without_membership(self):
        self.assertTrue(is_super_admin(self.super_user))
        self.assertTrue(is_ops_admin(self.super_user))
        self.assertFalse(is_vendor_user(self.super_user))
        self.assertTrue(can_access_tenant(self.super_user, self.mathukai))
        self.assertTrue(can_manage_vendor_settings(self.super_user, self.other_tenant))
        self.assertTrue(can_operate_vendor_orders(self.super_user, self.other_tenant))

    def test_membership_is_unique_per_user_and_tenant(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenantMembership.objects.create(
                    tenant=self.mathukai,
                    user=self.vendor_user,
                    role=TenantMembership.ROLE_VENDOR_OPERATOR,
                )

    def test_tenant_scoped_queryset_mixin_filters_by_active_tenant(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        mathukai_product = Product.objects.create(name="Mathukai Product", sku="TENANT-SCOPE-1")
        Product.objects.create(name="Other Product", sku="TENANT-SCOPE-2", tenant=self.other_tenant)

        mixin = TenantScopedQuerysetMixin()
        mixin.request = SimpleNamespace(user=self.vendor_user)
        queryset = mixin.scope_queryset_to_tenant(Product.objects.all())

        self.assertEqual(list(queryset), [mathukai_product])

    def test_active_tenant_middleware_sets_vendor_request_tenant(self):
        membership = TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        request = SimpleNamespace(user=self.vendor_user)
        middleware = ActiveTenantMiddleware(lambda request: "ok")

        response = middleware(request)

        self.assertEqual(response, "ok")
        self.assertEqual(request.tenant, self.mathukai)
        self.assertEqual(request.tenant_membership, membership)
        self.assertEqual(get_active_tenant(request), self.mathukai)

    def test_active_tenant_middleware_leaves_super_admin_platform_scoped(self):
        request = SimpleNamespace(user=self.super_user)
        middleware = ActiveTenantMiddleware(lambda request: "ok")

        response = middleware(request)

        self.assertEqual(response, "ok")
        self.assertIsNone(request.tenant)
        self.assertIsNone(request.tenant_membership)

    def test_vendor_login_routes_to_mobile_operations_dashboard(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )

        response = self.client.post(
            reverse("login"),
            {"username": "vendor", "password": "pass"},
        )

        self.assertRedirects(response, reverse("order_management"), fetch_redirect_response=False)

    def test_super_admin_login_routes_to_desktop_dashboard(self):
        response = self.client.post(
            reverse("login"),
            {"username": "root", "password": "pass"},
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_logout_redirects_to_login_page(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.client.force_login(self.vendor_user)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_home_requires_login(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_order_management_requires_login(self):
        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_signup_creates_vendor_tenant_owner_and_default_settings(self):
        response = self.client.post(
            reverse("signup"),
            {
                "tenant_name": "New Vendor Store",
                "username": "newvendor",
                "email": "newvendor@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("order_management"), fetch_redirect_response=False)
        tenant = Tenant.objects.get(slug="new-vendor-store")
        user = self.user_model.objects.get(username="newvendor")
        membership = TenantMembership.objects.get(tenant=tenant, user=user)

        self.assertEqual(tenant.owner, user)
        self.assertEqual(tenant.contact_email, "newvendor@example.com")
        self.assertEqual(membership.role, TenantMembership.ROLE_VENDOR_OWNER)
        self.assertTrue(membership.is_active)
        self.assertTrue(SenderAddress.objects.filter(tenant=tenant, name="New Vendor Store").exists())
        self.assertTrue(WooCommerceSettings.objects.filter(tenant=tenant).exists())
        self.assertTrue(WhatsAppSettings.objects.filter(tenant=tenant).exists())
        self.assertTrue(is_vendor_user(user))
        self.assertTrue(is_ops_viewer(user))
        self.assertFalse(is_ops_admin(user))

    def test_signup_rejects_duplicate_vendor_workspace_slug(self):
        Tenant.objects.create(name="Existing Vendor", slug="existing-vendor")

        response = self.client.post(
            reverse("signup"),
            {
                "tenant_name": "Existing Vendor",
                "username": "existingvendoruser",
                "email": "existingvendor@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertFalse(self.user_model.objects.filter(username="existingvendoruser").exists())

    def test_short_signup_url_opens_vendor_registration(self):
        self.assertEqual(reverse("signup"), "/signup/")

        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Business Name")
        self.assertContains(response, "responsive-form-card")

    def test_legacy_signup_url_still_opens_vendor_registration(self):
        response = self.client.get("/accounts/signup/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Business Name")

    def test_vendor_mobile_pages_show_active_tenant_brand_name(self):
        brand_tenant = Tenant.objects.create(name="Blue Lotus Vendor", slug="blue-lotus-vendor")
        TenantMembership.objects.create(
            tenant=brand_tenant,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        Product.objects.create(tenant=brand_tenant, name="Blue Product", sku="BLUE-1")
        self.client.force_login(self.vendor_user)

        for url_name in ["order_management", "home", "stock_management", "expense_tracker", "special_stock_issue_register"]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)
            self.assertContains(response, "Blue Lotus Vendor")

    def test_vendor_mobile_bottom_nav_shows_logout_action(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.client.force_login(self.vendor_user)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("logout"))
        self.assertContains(response, "Logout")
        self.assertContains(response, "fa-sign-out-alt")

    def test_vendor_mobile_stock_empty_state_shows_product_sync_action(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.client.force_login(self.vendor_user)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="form_action" value="sync_woocommerce_products"', html=False)
        self.assertContains(response, "Sync WooCommerce products")
        self.assertContains(response, "No products found")
        self.assertContains(response, "import mapped WooCommerce products")

    @patch("core.views.sync_woocommerce_products")
    def test_vendor_stock_sync_falls_back_to_order_items_when_shared_woocommerce_missing(self, mock_sync_products):
        mock_sync_products.side_effect = WooCommerceAPIError(
            "WooCommerce credentials are missing. Set WOOCOMMERCE_STORE_URL, "
            "WOOCOMMERCE_CONSUMER_KEY, and WOOCOMMERCE_CONSUMER_SECRET."
        )
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.mathukai,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="ENQ-",
        )
        ShiprocketOrder.objects.create(
            tenant=self.mathukai,
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-406",
            woocommerce_order_id="406",
            order_items=[
                {
                    "name": "Tenant Woo Product",
                    "product_id": 300,
                    "sku": "ENQ-300",
                    "quantity": 1,
                    "price": "130.00",
                    "image": "https://shop.example.com/product.jpg",
                }
            ],
        )
        self.client.force_login(self.vendor_user)

        response = self.client.post(
            reverse("stock_management"),
            {"form_action": "sync_woocommerce_products"},
            follow=True,
        )

        product = Product.objects.get(tenant=self.mathukai, smartbiz_product_id="300")
        self.assertEqual(product.name, "Tenant Woo Product")
        self.assertEqual(product.sku, "ENQ-300")
        self.assertEqual(product.stock_quantity, 0)
        self.assertContains(response, "Created local stock products from this vendor")
        self.assertNotContains(response, "WOOCOMMERCE_STORE_URL")

    def test_vendor_stock_page_auto_creates_products_from_existing_woocommerce_orders(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.mathukai,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="ENQ-",
        )
        ShiprocketOrder.objects.create(
            tenant=self.mathukai,
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-407",
            woocommerce_order_id="407",
            order_items=[
                {
                    "name": "Auto Created Woo Product",
                    "product_id": 300,
                    "sku": "ENQ-300",
                    "quantity": 1,
                    "price": "130.00",
                }
            ],
        )
        self.client.force_login(self.vendor_user)

        response = self.client.get(reverse("stock_management"))

        product = Product.objects.get(tenant=self.mathukai, smartbiz_product_id="300")
        self.assertEqual(product.sku, "ENQ-300")
        self.assertContains(response, "Auto Created Woo Product")
        self.assertContains(response, "Created local stock products from this vendor")

    def test_vendor_stock_page_does_not_create_product_without_matching_sku_prefix(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.mathukai,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="ENQ-",
        )
        ShiprocketOrder.objects.create(
            tenant=self.mathukai,
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-408",
            woocommerce_order_id="408",
            order_items=[
                {
                    "name": "Wrong Vendor Product",
                    "product_id": 301,
                    "sku": "OTHER-301",
                    "quantity": 1,
                    "price": "130.00",
                }
            ],
        )
        self.client.force_login(self.vendor_user)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(tenant=self.mathukai, smartbiz_product_id="301").exists())
        self.assertNotContains(response, "Wrong Vendor Product")

    def test_vendor_order_management_lists_only_active_tenant_orders(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        own_order = ShiprocketOrder.objects.create(
            tenant=self.mathukai,
            shiprocket_order_id="TENANT-A-ORDER-LIST",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        other_order = ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="TENANT-B-ORDER-LIST",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        self.client.force_login(self.vendor_user)

        response = self.client.get(reverse("order_management"), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_order.shiprocket_order_id)
        self.assertNotContains(response, other_order.shiprocket_order_id)

    def test_vendor_cannot_open_or_update_other_tenant_order(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        other_order = ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="TENANT-B-ORDER-DETAIL",
            local_status=ShiprocketOrder.STATUS_NEW,
        )
        self.client.force_login(self.vendor_user)

        detail_response = self.client.get(reverse("order_detail", args=[other_order.pk]))
        update_response = self.client.post(
            reverse("update_shiprocket_order_status", args=[other_order.pk]),
            {
                f"order-{other_order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{other_order.pk}-manual_customer_phone": "9876543210",
            },
        )

        other_order.refresh_from_db()
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(update_response.status_code, 404)
        self.assertEqual(other_order.local_status, ShiprocketOrder.STATUS_NEW)

    def test_vendor_stock_management_lists_and_updates_only_active_tenant_products(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        own_product = Product.objects.create(
            tenant=self.mathukai,
            name="Tenant A Product",
            sku="TENANT-A-STOCK-1",
            stock_quantity=5,
        )
        other_product = Product.objects.create(
            tenant=self.other_tenant,
            name="Tenant B Product",
            sku="TENANT-B-STOCK-1",
            stock_quantity=7,
        )
        self.client.force_login(self.vendor_user)

        list_response = self.client.get(reverse("stock_management"))
        blocked_update = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "TENANT-B-STOCK-1",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 3,
            },
            follow=True,
        )
        own_update = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "lookup_value": "TENANT-A-STOCK-1",
                "action": StockAdjustmentForm.ACTION_ADD,
                "quantity": 2,
            },
            follow=True,
        )

        own_product.refresh_from_db()
        other_product.refresh_from_db()
        self.assertContains(list_response, own_product.name)
        self.assertNotContains(list_response, other_product.name)
        self.assertContains(blocked_update, "No product found")
        self.assertEqual(other_product.stock_quantity, 7)
        self.assertContains(own_update, "2 unit(s) added")
        self.assertEqual(own_product.stock_quantity, 7)
        self.assertTrue(StockMovement.objects.filter(product=own_product, tenant=self.mathukai).exists())

    def test_vendor_expense_tracker_lists_and_creates_only_active_tenant_expenses(self):
        TenantMembership.objects.create(
            tenant=self.mathukai,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        own_person = ExpensePerson.objects.create(tenant=self.mathukai, name="Tenant A Buyer")
        other_person = ExpensePerson.objects.create(tenant=self.other_tenant, name="Tenant B Buyer")
        own_expense = BusinessExpense.objects.create(
            tenant=self.mathukai,
            expense_person=own_person,
            item_name="Tenant A Boxes",
            quantity=1,
            unit_price="10.00",
        )
        other_expense = BusinessExpense.objects.create(
            tenant=self.other_tenant,
            expense_person=other_person,
            item_name="Tenant B Boxes",
            quantity=1,
            unit_price="20.00",
        )
        self.client.force_login(self.vendor_user)

        list_response = self.client.get(reverse("expense_tracker"))
        create_response = self.client.post(
            reverse("expense_tracker"),
            {
                "expense_person": own_person.pk,
                "item_name": "Tenant A Tape",
                "quantity": 2,
                "unit_price": "5.00",
                "remark": "",
            },
            follow=True,
        )

        created_expense = BusinessExpense.objects.get(item_name="Tenant A Tape")
        self.assertContains(list_response, own_expense.item_name)
        self.assertNotContains(list_response, other_expense.item_name)
        self.assertContains(create_response, "Saved expense")
        self.assertEqual(created_expense.tenant, self.mathukai)
        self.assertFalse(BusinessExpense.objects.filter(tenant=self.other_tenant, item_name="Tenant A Tape").exists())


class SuperAdminTenantViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.super_user = user_model.objects.create_superuser(
            username="platform",
            email="platform@example.com",
            password="pass",
        )
        self.vendor_user = user_model.objects.create_user(username="vendoruser", password="pass")
        self.tenant = Tenant.objects.create(
            name="Vendor Workspace",
            slug="vendor-workspace",
            owner=self.vendor_user,
            contact_name="Vendor Owner",
            contact_email="owner@example.com",
            contact_phone="9876543210",
        )
        self.other_tenant = Tenant.objects.create(name="Other Workspace", slug="other-workspace")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.vendor_user,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        Product.objects.create(tenant=self.tenant, name="Tenant Product", sku="TENANT-ADMIN-1")
        Product.objects.create(tenant=self.other_tenant, name="Other Product", sku="TENANT-ADMIN-2")
        ShiprocketOrder.objects.create(
            tenant=self.tenant,
            shiprocket_order_id="SR-TENANT-ADMIN-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Tenant Customer",
            total="450.00",
        )
        ShiprocketOrder.objects.create(
            tenant=self.other_tenant,
            shiprocket_order_id="SR-TENANT-ADMIN-2",
            local_status=ShiprocketOrder.STATUS_CANCELLED,
            customer_name="Other Customer",
            total="900.00",
        )
        WooCommerceSettings.objects.create(
            store_url="https://vendor.example",
            consumer_key="ck_vendor",
            consumer_secret="cs_vendor",
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=self.tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="TENANT-",
        )
        WhatsAppSettings.objects.create(
            enabled=True,
            api_base_url="https://wa-api.cloud",
            api_key="token",
        )

    def test_super_admin_can_view_tenant_list(self):
        self.client.force_login(self.super_user)

        response = self.client.get(reverse("tenant_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendor Workspaces")
        self.assertContains(response, "Vendor Workspace")
        self.assertContains(response, "vendor-workspace")
        self.assertContains(response, "Mapped")
        self.assertContains(response, "Shared")
        self.assertContains(response, reverse("tenant_detail", args=[self.tenant.pk]))

    def test_super_admin_can_view_tenant_detail(self):
        self.client.force_login(self.super_user)

        response = self.client.get(reverse("tenant_detail", args=[self.tenant.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendor Workspace")
        self.assertContains(response, "vendoruser")
        self.assertContains(response, "SR-TENANT-ADMIN-1")
        self.assertNotContains(response, "SR-TENANT-ADMIN-2")
        self.assertContains(response, "WooCommerce")
        self.assertContains(response, "WooCommerce Mapping Rules")
        self.assertContains(response, "SKU Prefix")
        self.assertContains(response, "TENANT-")
        self.assertContains(response, "WhatsApp")

    def test_super_admin_can_create_mapping_rule_from_tenant_detail(self):
        self.client.force_login(self.super_user)

        response = self.client.post(
            reverse("tenant_detail", args=[self.tenant.pk]),
            {
                "action": "save_mapping_rule",
                "match_type": TenantWooCommerceMappingRule.MATCH_CATEGORY,
                "match_value": "Vendor Category",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TenantWooCommerceMappingRule.objects.filter(
                tenant=self.tenant,
                match_type=TenantWooCommerceMappingRule.MATCH_CATEGORY,
                match_value="Vendor Category",
                is_active=True,
            ).exists()
        )

    def test_super_admin_can_edit_mapping_rule_from_tenant_detail(self):
        self.client.force_login(self.super_user)
        rule = TenantWooCommerceMappingRule.objects.get(
            tenant=self.tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
        )

        response = self.client.post(
            reverse("tenant_detail", args=[self.tenant.pk]),
            {
                "action": "save_mapping_rule",
                "mapping_rule_id": str(rule.pk),
                "match_type": TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
                "match_value": "vendor-new-",
            },
        )

        self.assertEqual(response.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual(rule.match_value, "VENDOR-NEW-")
        self.assertFalse(rule.is_active)

    def test_duplicate_mapping_rule_shows_error(self):
        self.client.force_login(self.super_user)

        response = self.client.post(
            reverse("tenant_detail", args=[self.tenant.pk]),
            {
                "action": "save_mapping_rule",
                "match_type": TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
                "match_value": "TENANT-",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This mapping rule already exists for this tenant.")
        self.assertEqual(
            TenantWooCommerceMappingRule.objects.filter(
                tenant=self.tenant,
                match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
                match_value="TENANT-",
            ).count(),
            1,
        )

    def test_vendor_user_cannot_create_mapping_rule(self):
        self.client.force_login(self.vendor_user)

        response = self.client.post(
            reverse("tenant_detail", args=[self.tenant.pk]),
            {
                "action": "save_mapping_rule",
                "match_type": TenantWooCommerceMappingRule.MATCH_TAG,
                "match_value": "Blocked Tag",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("order_management"), response.url)
        self.assertFalse(
            TenantWooCommerceMappingRule.objects.filter(
                tenant=self.tenant,
                match_type=TenantWooCommerceMappingRule.MATCH_TAG,
                match_value="Blocked Tag",
            ).exists()
        )

    def test_vendor_user_cannot_view_tenant_pages(self):
        self.client.force_login(self.vendor_user)

        list_response = self.client.get(reverse("tenant_list"))
        detail_response = self.client.get(reverse("tenant_detail", args=[self.tenant.pk]))

        self.assertEqual(list_response.status_code, 302)
        self.assertIn(reverse("order_management"), list_response.url)
        self.assertEqual(detail_response.status_code, 302)
        self.assertIn(reverse("order_management"), detail_response.url)

    def test_tenant_link_only_shows_for_super_admin_sidebar(self):
        self.client.force_login(self.super_user)
        super_response = self.client.get(reverse("home"))
        self.assertContains(super_response, reverse("tenant_list"))
        self.assertContains(super_response, "Tenants")

        self.client.force_login(self.vendor_user)
        vendor_response = self.client.get(reverse("order_management"))
        self.assertNotContains(vendor_response, reverse("tenant_list"))


class ShiprocketSyncTests(TestCase):
    @patch("core.shiprocket._get_auth_token", return_value="token-123")
    @patch("core.shiprocket._json_request")
    def test_sync_orders_imports_only_new_shiprocket_orders(self, mock_json_request, mock_get_auth_token):
        mock_json_request.return_value = {
            "data": [
                {
                    "id": 101,
                    "status": "NEW",
                    "customer_name": "Fresh Order",
                    "customer_email": "fresh@example.com",
                    "customer_phone": "9999999999",
                    "payment_method": "Prepaid",
                    "total": "250.00",
                    "created_at": "2026-04-01T01:00:00+00:00",
                },
                {
                    "id": 202,
                    "status": "CANCELED",
                    "customer_name": "Cancelled Order",
                    "customer_email": "cancelled@example.com",
                    "customer_phone": "8888888888",
                    "payment_method": "COD",
                    "total": "175.00",
                    "created_at": "2026-04-01T02:00:00+00:00",
                },
            ]
        }

        synced = sync_orders()

        self.assertEqual(synced, 1)
        self.assertTrue(ShiprocketOrder.objects.filter(shiprocket_order_id="101").exists())
        self.assertFalse(ShiprocketOrder.objects.filter(shiprocket_order_id="202").exists())
        self.assertEqual(ShiprocketOrder.objects.get(shiprocket_order_id="101").status, "NEW")


class WooCommerceSyncTests(TestCase):
    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_orders_imports_woocommerce_orders(self, mock_json_request):
        mock_json_request.return_value = [
            {
                "id": 501,
                "number": "1001",
                "order_key": "wc_order_abc",
                "customer_id": 44,
                "status": "processing",
                "payment_method_title": "Razorpay",
                "total": "499.00",
                "date_created": "2026-04-01T10:30:00",
                "billing": {
                    "first_name": "Woo",
                    "last_name": "Customer",
                    "email": "woo@example.com",
                    "phone": "9876543210",
                    "address_1": "Billing street",
                    "postcode": "600001",
                },
                "shipping": {
                    "first_name": "Woo",
                    "last_name": "Customer",
                    "address_1": "Shipping street",
                    "city": "Chennai",
                    "state": "TN",
                    "postcode": "600001",
                    "country": "IN",
                },
                "line_items": [
                    {
                        "name": "Herbal Tea",
                        "sku": "TEA-1",
                        "product_id": 11,
                        "quantity": 2,
                        "price": "249.50",
                    }
                ],
            }
        ]

        synced = sync_woocommerce_orders()

        self.assertEqual(synced, 1)
        mock_json_request.assert_called_once()
        self.assertIn("whatsapp-draft", mock_json_request.call_args.kwargs["params"]["status"])
        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-501")
        self.assertEqual(order.source, ShiprocketOrder.SOURCE_WOOCOMMERCE)
        self.assertEqual(order.woocommerce_order_id, "501")
        self.assertEqual(order.channel_order_id, "1001")
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)
        self.assertEqual(order.order_items[0]["sku"], "TEA-1")

    @patch("core.woocommerce._json_request")
    def test_sync_orders_uses_shared_connection_with_requested_tenant_fallback(self, mock_json_request):
        vendor_tenant = Tenant.objects.create(name="Woo Vendor", slug="woo-vendor")
        WooCommerceSettings.objects.create(store_url="https://shared.example.com", consumer_key="ck_shared", consumer_secret="cs_shared")
        mock_json_request.return_value = [
            {
                "id": 901,
                "number": "1901",
                "customer_id": 44,
                "status": "processing",
                "total": "499.00",
                "date_created": "2026-04-01T10:30:00",
                "billing": {
                    "first_name": "Tenant",
                    "last_name": "Buyer",
                    "phone": "9876543210",
                    "address_1": "Tenant billing street",
                    "postcode": "600001",
                },
                "shipping": {},
                "line_items": [],
            }
        ]

        synced = sync_woocommerce_orders(tenant=vendor_tenant)

        self.assertEqual(synced, 1)
        mock_json_request.assert_called_once()
        self.assertNotIn("tenant", mock_json_request.call_args.kwargs)
        order = ShiprocketOrder.objects.get(woocommerce_order_id="901")
        self.assertEqual(order.tenant, vendor_tenant)
        self.assertEqual(order.source, ShiprocketOrder.SOURCE_WOOCOMMERCE)

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_products_imports_woocommerce_products_and_variations(self, mock_json_request):
        mock_json_request.side_effect = [
            [
                {
                    "id": 11,
                    "name": "Goat Milk Soap",
                    "type": "simple",
                    "status": "publish",
                    "sku": "soap-100",
                    "stock_quantity": 12,
                    "description": "<p>Gentle goat milk soap &amp; scrub.<br />Use daily.</p>",
                    "regular_price": "150.00",
                    "sale_price": "120.00",
                    "categories": [{"name": "Soap"}],
                    "images": [{"src": "https://shop.example.com/images/goat-milk-soap.jpg"}],
                },
                {
                    "id": 12,
                    "name": "Amla Juice",
                    "type": "variable",
                    "status": "publish",
                    "sku": "",
                    "stock_quantity": None,
                    "categories": [{"name": "Juice"}],
                    "images": [{"src": "https://shop.example.com/images/amla-parent.jpg"}],
                    "variations": [121],
                },
            ],
            [
                {
                    "id": 121,
                    "sku": "amla-500",
                    "stock_quantity": 5,
                    "regular_price": "300.00",
                    "sale_price": "250.00",
                    "attributes": [{"name": "Size", "option": "500 ml"}],
                    "image": {"src": "https://shop.example.com/images/amla-500.jpg"},
                }
            ],
        ]

        summary = sync_woocommerce_products()

        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["variations_seen"], 1)
        soap = Product.objects.get(sku="SOAP-100")
        self.assertEqual(soap.name, "Goat Milk Soap")
        self.assertEqual(soap.stock_quantity, 12)
        self.assertEqual(soap.description, "Gentle goat milk soap & scrub.\nUse daily.")
        self.assertEqual(str(soap.regular_price), "150.00")
        self.assertEqual(str(soap.sale_price), "120.00")
        self.assertEqual(soap.smartbiz_product_id, "11")
        self.assertEqual(soap.image_url, "https://shop.example.com/images/goat-milk-soap.jpg")
        self.assertEqual(soap.category_master.name, "Soap")
        amla = Product.objects.get(sku="AMLA-500")
        self.assertEqual(amla.name, "Amla Juice - 500 ml")
        self.assertEqual(amla.stock_quantity, 5)
        self.assertEqual(str(amla.regular_price), "300.00")
        self.assertEqual(str(amla.sale_price), "250.00")
        self.assertEqual(amla.smartbiz_product_id, "121")
        self.assertEqual(amla.image_url, "https://shop.example.com/images/amla-500.jpg")
        self.assertEqual(amla.category_master.name, "Juice")

    @patch("core.woocommerce._json_request")
    def test_sync_products_assigns_tenant_from_category_mapping(self, mock_json_request):
        vendor_tenant = Tenant.objects.create(name="Woo Product Vendor", slug="woo-product-vendor")
        WooCommerceSettings.objects.create(
            store_url="https://shared-products.example.com",
            consumer_key="ck_shared",
            consumer_secret="cs_shared",
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=vendor_tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_CATEGORY,
            match_value="Tenant Soap",
        )
        mock_json_request.return_value = [
            {
                "id": 1901,
                "name": "Tenant Soap",
                "type": "simple",
                "status": "publish",
                "sku": "tenant-soap-1901",
                "stock_quantity": 9,
                "categories": [{"name": "Tenant Soap"}],
                "images": [],
            }
        ]

        summary = sync_woocommerce_products()

        self.assertEqual(summary["created"], 1)
        mock_json_request.assert_called_once()
        self.assertNotIn("tenant", mock_json_request.call_args.kwargs)
        product = Product.objects.get(sku="TENANT-SOAP-1901")
        self.assertEqual(product.tenant, vendor_tenant)
        self.assertEqual(product.category_master.tenant, vendor_tenant)

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_update_product_sends_product_payload_to_woocommerce(self, mock_json_request):
        from .woocommerce import update_product as update_woocommerce_product

        product = Product.objects.create(
            name="24K Gold Serum",
            sku="MO-SER-001",
            stock_quantity=4,
            smartbiz_product_id="101",
            image_url="https://shop.example.com/images/serum.jpg",
            is_active=True,
        )

        update_woocommerce_product(
            product,
            extra_fields={
                "description": "Reduces fine lines and wrinkles.",
                "regular_price": "300.00",
                "sale_price": "240.00",
            },
        )

        mock_json_request.assert_called_once_with(
            "products/101",
            method="PUT",
            payload={
                "name": "24K Gold Serum",
                "sku": "MO-SER-001",
                "manage_stock": True,
                "stock_quantity": 4,
                "status": "publish",
                "images": [{"src": "https://shop.example.com/images/serum.jpg"}],
                "description": "Reduces fine lines and wrinkles.",
                "regular_price": "300.00",
                "sale_price": "240.00",
            },
        )

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_refresh_product_from_woocommerce_updates_local_prices_and_image(self, mock_json_request):
        from .woocommerce import refresh_product_from_woocommerce

        product = Product.objects.create(
            name="Anti-Dandruff Hair Oil",
            sku="MO-HC-007",
            stock_quantity=8,
            smartbiz_product_id="101",
            is_active=True,
        )
        mock_json_request.return_value = {
            "id": 101,
            "name": "Anti-Dandruff Hair Oil",
            "status": "publish",
            "sku": "MO-HC-007",
            "stock_quantity": 8,
            "description": "<p>1.Reduces Dark spots &amp; pigmentation<br />2.Promotes even skin tone &amp; a youthful appearance</p>",
            "regular_price": "300.00",
            "sale_price": "240.00",
            "categories": [{"name": "Hair Care"}],
            "images": [{"src": "https://shop.example.com/images/hair-oil.jpg"}],
        }

        refreshed = refresh_product_from_woocommerce(product)

        product.refresh_from_db()
        self.assertTrue(refreshed)
        self.assertEqual(
            product.description,
            "1.Reduces Dark spots & pigmentation\n2.Promotes even skin tone & a youthful appearance",
        )
        self.assertEqual(str(product.regular_price), "300.00")
        self.assertEqual(str(product.sale_price), "240.00")
        self.assertEqual(product.image_url, "https://shop.example.com/images/hair-oil.jpg")
        self.assertEqual(product.category_master.name, "Hair Care")

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_orders_skips_whatsapp_draft_orders_until_billing_address_exists(self, mock_json_request):
        WooCommerceSettings.objects.create(import_statuses="pending,processing,on-hold")
        mock_json_request.return_value = [
            {
                "id": 503,
                "number": "1003",
                "customer_id": 44,
                "status": "whatsapp-draft",
                "total": "150.00",
                "date_created": "2026-04-01T10:35:00",
                "billing": {"first_name": "Ramachandran", "last_name": "", "phone": "9876543210"},
                "shipping": {},
                "line_items": [],
            }
        ]

        synced = sync_woocommerce_orders()

        self.assertEqual(synced, 0)
        self.assertIn("whatsapp-draft", mock_json_request.call_args.kwargs["params"]["status"])
        self.assertFalse(ShiprocketOrder.objects.filter(shiprocket_order_id="WC-503").exists())

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_orders_imports_whatsapp_draft_after_billing_address_arrives(self, mock_json_request):
        mock_json_request.return_value = [
            {
                "id": 503,
                "number": "1003",
                "status": "whatsapp-draft",
                "total": "150.00",
                "date_created": "2026-04-01T10:35:00",
                "billing": {
                    "first_name": "Ramachandran",
                    "phone": "9876543210",
                    "address_1": "No 38 5th Street jeevan Adambakkam",
                    "city": "Chennai",
                    "postcode": "600088",
                },
                "shipping": {},
                "line_items": [],
            }
        ]

        synced = sync_woocommerce_orders()

        self.assertEqual(synced, 1)
        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-503")
        self.assertEqual(order.channel_order_id, "1003")
        self.assertEqual(order.woocommerce_status, "whatsapp-draft")
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)
        self.assertEqual(order.display_shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_import_guest_order_assigns_unique_customer_matching_billing_phone(self, mock_json_request):
        mock_json_request.side_effect = [
            [
                {
                    "id": 902,
                    "billing": {"phone": "+91 98765 43210"},
                }
            ],
            {"id": 7001, "customer_id": 902},
        ]

        order, created = import_woocommerce_order_payload(
            {
                "id": 7001,
                "number": "7001",
                "customer_id": 0,
                "status": "processing",
                "billing": {
                    "first_name": "Mapped",
                    "last_name": "Customer",
                    "phone": "9876543210",
                    "address_1": "Mapped street",
                    "postcode": "600001",
                },
                "shipping": {},
                "line_items": [],
            }
        )

        self.assertTrue(created)
        self.assertEqual(order.raw_payload["customer_id"], 902)
        self.assertEqual(
            mock_json_request.call_args_list[1].args[0],
            "orders/7001",
        )
        self.assertEqual(mock_json_request.call_args_list[1].kwargs["method"], "PUT")
        self.assertEqual(mock_json_request.call_args_list[1].kwargs["payload"], {"customer_id": 902})

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_import_guest_order_does_not_assign_when_phone_matches_multiple_customers(self, mock_json_request):
        mock_json_request.return_value = [
            {"id": 902, "billing": {"phone": "9876543210"}},
            {"id": 903, "billing": {"phone": "+91 98765 43210"}},
        ]

        order, created = import_woocommerce_order_payload(
            {
                "id": 7002,
                "number": "7002",
                "customer_id": 0,
                "status": "processing",
                "billing": {
                    "first_name": "Duplicate",
                    "last_name": "Customer",
                    "phone": "9876543210",
                    "address_1": "Duplicate street",
                    "postcode": "600001",
                },
                "shipping": {},
                "line_items": [],
            }
        )

        self.assertTrue(created)
        self.assertEqual(order.raw_payload["customer_id"], 0)
        self.assertEqual(mock_json_request.call_count, 1)

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_update_status_assigns_guest_order_even_when_status_already_synced(self, mock_json_request):
        mock_json_request.side_effect = [
            [],
            [{"id": 902, "billing": {"phone": "9876543210"}}],
            {"id": 7003, "customer_id": 902},
        ]
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-7003",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            woocommerce_order_id="7003",
            woocommerce_status="processing",
            status="processing",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543210",
            billing_address={"phone": "9876543210"},
            raw_payload={"id": 7003, "customer_id": 0, "billing": {"phone": "9876543210"}},
        )

        result = update_woocommerce_order_status(order)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "already_synced")
        order.refresh_from_db()
        self.assertEqual(order.raw_payload["customer_id"], 902)
        self.assertEqual(mock_json_request.call_args_list[2].args[0], "orders/7003")
        self.assertEqual(mock_json_request.call_args_list[2].kwargs["method"], "PUT")
        self.assertEqual(mock_json_request.call_args_list[2].kwargs["payload"], {"customer_id": 902})

    def test_import_order_payload_preserves_existing_address_when_update_has_no_billing_address(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-9005",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            woocommerce_order_id="9005",
            woocommerce_status="whatsapp-draft",
            billing_address={
                "name": "Ramachandran",
                "phone": "9876543210",
                "address_1": "No 38 5th Street jeevan Adambakkam",
                "city": "Chennai",
                "pincode": "600088",
            },
            shipping_address={
                "name": "Ramachandran",
                "phone": "9876543210",
                "address_1": "No 38 5th Street jeevan Adambakkam",
                "city": "Chennai",
                "pincode": "600088",
            },
        )

        refreshed, created = import_woocommerce_order_payload(
            {
                "id": 9005,
                "number": "9005",
                "status": "processing",
                "billing": {"first_name": "Ramachandran", "phone": "9876543210"},
                "shipping": {},
                "line_items": [],
            }
        )

        self.assertFalse(created)
        self.assertEqual(refreshed.pk, order.pk)
        refreshed.refresh_from_db()
        self.assertEqual(refreshed.billing_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(refreshed.shipping_address["pincode"], "600088")

    def test_woocommerce_status_mapping_rejects_local_status_values(self):
        WooCommerceSettings.objects.create(
            status_map=json.dumps({ShiprocketOrder.STATUS_ACCEPTED: ShiprocketOrder.STATUS_ACCEPTED})
        )

        self.assertEqual(
            woocommerce_status_for_local_status(ShiprocketOrder.STATUS_ACCEPTED),
            "processing",
        )

    def test_woocommerce_status_mapping_accepts_wc_prefixed_status_values(self):
        WooCommerceSettings.objects.create(status_map=json.dumps({ShiprocketOrder.STATUS_ACCEPTED: "wc-processing"}))

        self.assertEqual(
            woocommerce_status_for_local_status(ShiprocketOrder.STATUS_ACCEPTED),
            "processing",
        )

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_orders_uses_billing_address_when_shipping_address_is_blank(self, mock_json_request):
        mock_json_request.return_value = [
            {
                "id": 504,
                "number": "1004",
                "status": "processing",
                "total": "150.00",
                "date_created": "2026-04-01T10:35:00",
                "billing": {
                    "first_name": "Ramachandran",
                    "phone": "9876543210",
                    "address_1": "No 38 5th Street jeevan Adambakkam",
                    "city": "Chennai",
                    "state": "TN",
                    "postcode": "600088",
                    "country": "IN",
                },
                "shipping": {
                    "first_name": "Shipping",
                    "phone": "1111111111",
                    "address_1": "Wrong Shipping Street",
                    "city": "Wrong City",
                    "postcode": "",
                },
                "line_items": [],
            }
        ]

        sync_woocommerce_orders()

        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-504")
        self.assertEqual(order.shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.shipping_address["city"], "Chennai")
        self.assertEqual(order.shipping_address["pincode"], "600088")
        self.assertEqual(order.shipping_address["phone"], "9876543210")
        self.assertEqual(order.display_shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.display_shipping_address["pincode"], "600088")

    def test_display_shipping_address_falls_back_to_billing_address(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-EXISTING-ADDRESS-1",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shipping_address={"name": "Existing Customer", "address_1": "", "pincode": ""},
            billing_address={
                "address_1": "No 38 5th Street jeevan Adambakkam",
                "address_2": "Near Station",
                "city": "Chennai",
                "state": "TN",
                "country": "IN",
                "pincode": "600088",
            },
        )

        self.assertEqual(order.display_shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.display_shipping_address["city"], "Chennai")
        self.assertEqual(order.display_shipping_address["pincode"], "600088")

    def test_display_shipping_address_prefers_billing_for_woocommerce_orders(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-BILLING-FIRST-1",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shipping_address={
                "name": "Shipping Name",
                "phone": "1111111111",
                "address_1": "Old Shipping Street",
                "city": "Old City",
                "pincode": "",
            },
            billing_address={
                "name": "Ramachandran",
                "phone": "+919952975768",
                "address_1": "No 38 5th Street jeevan Adambakkam",
                "city": "Chennai",
                "state": "TN",
                "country": "IN",
                "pincode": "600088",
            },
        )

        self.assertEqual(order.display_shipping_address["name"], "Ramachandran")
        self.assertEqual(order.display_shipping_address["phone"], "+919952975768")
        self.assertEqual(order.display_shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.display_shipping_address["city"], "Chennai")
        self.assertEqual(order.display_shipping_address["pincode"], "600088")

    def test_display_shipping_address_falls_back_to_raw_woocommerce_billing_payload(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-EXISTING-ADDRESS-RAW",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shipping_address={"name": "Ramachandran", "phone": "+919952975768", "address_1": ""},
            billing_address={},
            raw_payload={
                "billing": {
                    "first_name": "Ramachandran",
                    "phone": "+919952975768",
                    "address_1": "No 38 5th Street jeevan Adambakkam",
                    "city": "Chennai",
                    "state": "TN",
                    "postcode": "600088",
                    "country": "IN",
                },
                "shipping": {"first_name": "Ramachandran", "address_1": ""},
            },
        )

        self.assertEqual(order.display_shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.display_shipping_address["city"], "Chennai")
        self.assertEqual(order.display_shipping_address["pincode"], "600088")

    def test_repair_woocommerce_addresses_backfills_shipping_from_billing_payload(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-REPAIR-ADDRESS-1",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shipping_address={"name": "Ramachandran", "address_1": ""},
            billing_address={},
            raw_payload={
                "billing": {
                    "first_name": "Ramachandran",
                    "phone": "+919952975768",
                    "address_1": "No 38 5th Street jeevan Adambakkam",
                    "city": "Chennai",
                    "postcode": "600088",
                    "country": "IN",
                }
            },
        )

        call_command("repair_woocommerce_addresses", "--confirm", stdout=StringIO())

        order.refresh_from_db()
        self.assertEqual(order.billing_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.shipping_address["address_1"], "No 38 5th Street jeevan Adambakkam")
        self.assertEqual(order.shipping_address["pincode"], "600088")

    @override_settings(
        WOOCOMMERCE_STORE_URL="https://shop.example.com",
        WOOCOMMERCE_CONSUMER_KEY="ck_test",
        WOOCOMMERCE_CONSUMER_SECRET="cs_test",
    )
    @patch("core.woocommerce._json_request")
    def test_sync_orders_treats_woocommerce_gmt_order_date_as_utc(self, mock_json_request):
        mock_json_request.return_value = [
            {
                "id": 502,
                "number": "1002",
                "customer_id": 44,
                "status": "processing",
                "total": "100.00",
                "date_created": "2026-04-01T16:00:00",
                "date_created_gmt": "2026-04-01T10:30:00",
                "billing": {"first_name": "Time", "last_name": "Check", "address_1": "Clock street"},
                "shipping": {},
                "line_items": [],
            }
        ]

        sync_woocommerce_orders()

        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-502")
        local_order_time = timezone.localtime(order.order_date)
        self.assertEqual(local_order_time.strftime("%Y-%m-%d %H:%M"), "2026-04-01 16:00")

    def test_woocommerce_webhook_imports_order_with_valid_signature(self):
        WooCommerceSettings.objects.create(webhook_secret="woo-secret")
        payload = {
            "id": 777,
            "number": "1777",
            "order_key": "wc_order_webhook",
            "status": "processing",
            "payment_method_title": "COD",
            "total": "399.00",
            "date_created": "2026-04-02T10:30:00",
            "billing": {
                "first_name": "Webhook",
                "last_name": "Buyer",
                "email": "webhook@example.com",
                "phone": "9876543210",
                "address_1": "Billing road",
                "postcode": "600001",
            },
            "shipping": {
                "first_name": "Webhook",
                "last_name": "Buyer",
                "address_1": "Delivery road",
                "city": "Chennai",
                "state": "TN",
                "postcode": "600001",
                "country": "IN",
            },
            "line_items": [
                {"name": "Webhook Product", "sku": "WEBHOOK-SKU-1", "product_id": 71, "quantity": 1, "price": "399.00"}
            ],
        }
        raw_body = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            reverse("woocommerce_webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_WC_WEBHOOK_SIGNATURE=_build_woocommerce_webhook_signature(raw_body, "woo-secret"),
            HTTP_X_WC_WEBHOOK_TOPIC="order.created",
        )

        self.assertEqual(response.status_code, 200)
        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-777")
        self.assertEqual(order.source, ShiprocketOrder.SOURCE_WOOCOMMERCE)
        self.assertEqual(order.channel_order_id, "1777")
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_NEW)
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                title="WooCommerce webhook order imported",
                is_success=True,
            ).exists()
        )

    def test_woocommerce_webhook_rejects_invalid_signature(self):
        WooCommerceSettings.objects.create(webhook_secret="woo-secret")
        response = self.client.post(
            reverse("woocommerce_webhook"),
            data=json.dumps({"id": 778}).encode("utf-8"),
            content_type="application/json",
            HTTP_X_WC_WEBHOOK_SIGNATURE="wrong",
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(ShiprocketOrder.objects.filter(shiprocket_order_id="WC-778").exists())

    @patch("core.views.sync_woocommerce_orders")
    def test_woocommerce_webhook_accepts_signed_validation_without_order_id(self, mock_sync_orders):
        WooCommerceSettings.objects.create(webhook_secret="woo-secret")
        mock_sync_orders.return_value = 1
        raw_body = b"{}"
        response = self.client.post(
            reverse("woocommerce_webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_WC_WEBHOOK_SIGNATURE=_build_woocommerce_webhook_signature(raw_body, "woo-secret"),
            HTTP_X_WC_WEBHOOK_TOPIC="order.created",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ignored"], True)
        self.assertEqual(response.json()["fallback_sync"], True)
        self.assertEqual(response.json()["synced"], 1)
        mock_sync_orders.assert_called_once()
        self.assertFalse(ShiprocketOrder.objects.exists())

    @patch("core.views.sync_woocommerce_orders")
    def test_woocommerce_webhook_accepts_signed_non_json_validation_payload(self, mock_sync_orders):
        WooCommerceSettings.objects.create(webhook_secret="woo-secret")
        mock_sync_orders.return_value = 1
        raw_body = b"webhook_id=123"
        response = self.client.post(
            reverse("woocommerce_webhook"),
            data=raw_body,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_WC_WEBHOOK_SIGNATURE=_build_woocommerce_webhook_signature(raw_body, "woo-secret"),
            HTTP_X_WC_WEBHOOK_TOPIC="order.created",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ignored"], True)
        self.assertEqual(response.json()["fallback_sync"], True)
        self.assertEqual(response.json()["synced"], 1)
        mock_sync_orders.assert_called_once()
        self.assertFalse(ShiprocketOrder.objects.exists())

    def test_woocommerce_webhook_accepts_query_secret_fallback(self):
        WooCommerceSettings.objects.create(webhook_secret="woo-secret")
        payload = {
            "id": 779,
            "number": "1779",
            "status": "processing",
            "billing": {
                "first_name": "Query",
                "last_name": "Secret",
                "phone": "9876543210",
                "address_1": "Delivery road",
            },
            "shipping": {"address_1": "Delivery road", "postcode": "600001"},
            "line_items": [],
        }
        response = self.client.post(
            f"{reverse('woocommerce_webhook')}?secret=woo-secret",
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        order = ShiprocketOrder.objects.get(shiprocket_order_id="WC-779")
        self.assertEqual(order.source, ShiprocketOrder.SOURCE_WOOCOMMERCE)

    def test_woocommerce_webhook_assigns_tenant_from_sku_mapping(self):
        vendor_tenant = Tenant.objects.create(name="Webhook Vendor", slug="webhook-vendor")
        WooCommerceSettings.objects.create(
            webhook_secret="shared-webhook-secret",
        )
        TenantWooCommerceMappingRule.objects.create(
            tenant=vendor_tenant,
            match_type=TenantWooCommerceMappingRule.MATCH_SKU_PREFIX,
            match_value="VENDOR-",
        )
        payload = {
            "id": 1780,
            "number": "1780",
            "customer_id": 44,
            "status": "processing",
            "billing": {
                "first_name": "Webhook",
                "last_name": "Tenant",
                "phone": "9876543210",
                "address_1": "Tenant delivery road",
            },
            "shipping": {},
            "line_items": [
                {"name": "Vendor Product", "sku": "VENDOR-001", "product_id": 9901, "quantity": 1, "price": "499.00"}
            ],
        }
        raw_body = json.dumps(payload).encode("utf-8")

        response = self.client.post(
            reverse("woocommerce_webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_WC_WEBHOOK_SIGNATURE=_build_woocommerce_webhook_signature(raw_body, "shared-webhook-secret"),
        )

        self.assertEqual(response.status_code, 200)
        order = ShiprocketOrder.objects.get(woocommerce_order_id="1780")
        self.assertEqual(order.tenant, vendor_tenant)
        self.assertEqual(response.json()["order_pk"], order.pk)

    @patch("core.views.sync_woocommerce_orders")
    def test_vendor_order_sync_uses_shared_woocommerce_connection(self, mock_sync_orders):
        vendor_tenant = Tenant.objects.create(name="Sync Vendor", slug="sync-vendor")
        vendor_user = get_user_model().objects.create_user(username="syncvendor", password="testpass123")
        TenantMembership.objects.create(
            tenant=vendor_tenant,
            user=vendor_user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        WooCommerceSettings.objects.create(store_url="https://shared.example.com", consumer_key="ck_shared", consumer_secret="cs_shared")
        mock_sync_orders.return_value = 2
        self.client.force_login(vendor_user)

        response = self.client.post(reverse("sync_orders"), follow=True)

        self.assertRedirects(response, reverse("home"))
        mock_sync_orders.assert_called_once_with()
        self.assertContains(response, "WooCommerce: 2 orders refreshed")

    def test_order_notifications_poll_returns_new_woocommerce_orders(self):
        user = get_user_model().objects.create_user(username="notifyuser", password="testpass123")
        old_order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-NOTIFY-OLD",
            channel_order_id="9000",
            customer_name="Old Customer",
        )
        new_order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-NOTIFY-NEW",
            channel_order_id="9001",
            customer_name="New Customer",
            total="250.00",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("order_notifications_poll"),
            {"since": old_order.created_at.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["orders"]), 1)
        self.assertEqual(payload["orders"][0]["id"], new_order.pk)
        self.assertEqual(payload["orders"][0]["order_id"], "9001")

    @override_settings(PWA_VAPID_PUBLIC_KEY="public-key", PWA_VAPID_PRIVATE_KEY="private-key")
    def test_web_push_config_returns_public_key(self):
        user = get_user_model().objects.create_user(username="pushconfig", password="testpass123")
        self.client.force_login(user)

        response = self.client.get(reverse("web_push_config"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["public_key"], "public-key")

    def test_web_push_subscribe_saves_subscription(self):
        user = get_user_model().objects.create_user(username="pushuser", password="testpass123")
        self.client.force_login(user)
        payload = {
            "endpoint": "https://push.example.com/subscription/abc",
            "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
        }

        response = self.client.post(
            reverse("web_push_subscribe"),
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        subscription = WebPushSubscription.objects.get(endpoint=payload["endpoint"])
        self.assertEqual(subscription.user, user)
        self.assertEqual(subscription.p256dh_key, "p256dh-key")
        self.assertTrue(subscription.is_active)


class ShiprocketOrderStatusFormTests(TestCase):
    def test_status_form_excludes_current_and_previous_statuses(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]

        self.assertEqual(choices[0], ShiprocketOrder.STATUS_DELIVERED)
        self.assertIn(ShiprocketOrder.STATUS_DELIVERY_ISSUE, choices)
        self.assertIn(ShiprocketOrder.STATUS_OUT_FOR_DELIVERY, choices)
        self.assertNotIn(ShiprocketOrder.STATUS_CANCELLED, choices)
        self.assertNotIn(ShiprocketOrder.STATUS_NEW, choices)
        self.assertNotIn(ShiprocketOrder.STATUS_SHIPPED, choices)

    def test_delivery_issue_moves_only_to_delivered_or_out_for_delivery(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-DI-1",
            local_status=ShiprocketOrder.STATUS_DELIVERY_ISSUE,
        )

        form = ShiprocketOrderStatusForm(instance=order, prefix=f"order-{order.pk}")
        choices = [value for value, _ in form.fields["local_status"].choices]
        self.assertEqual(
            choices,
            [ShiprocketOrder.STATUS_DELIVERED, ShiprocketOrder.STATUS_OUT_FOR_DELIVERY],
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

    def test_shipped_status_requires_shipping_base_amount(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-FORM-SHIP-COST-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
        )

        form = ShiprocketOrderStatusForm(
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_SHIPPED,
                f"order-{order.pk}-courier_name": "India Post",
                f"order-{order.pk}-tracking_number": "AA123456789AA",
            },
            instance=order,
            prefix=f"order-{order.pk}",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("shipping_base_amount", form.errors)


class ShiprocketOrderProfitTests(TestCase):
    def test_order_profit_uses_product_actual_price(self):
        Product.objects.create(
            name="Profit Soap",
            sku="PROFIT-SOAP-1",
            actual_price="40.00",
            regular_price="100.00",
            sale_price="90.00",
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PROFIT-1",
            order_items=[
                {"name": "Profit Soap", "sku": "PROFIT-SOAP-1", "quantity": 2, "price": "90.00"},
            ],
        )

        summary = summarize_order_profit(order)

        self.assertTrue(summary["is_complete"])
        self.assertEqual(str(summary["revenue_total"]), "180.00")
        self.assertEqual(str(summary["actual_cost_total"]), "80.00")
        self.assertEqual(str(summary["profit_amount"]), "100.00")

    def test_order_profit_does_not_treat_missing_actual_price_as_full_profit(self):
        Product.objects.create(
            name="Profit Soap",
            sku="PROFIT-SOAP-1",
            regular_price="100.00",
            sale_price="90.00",
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PROFIT-MISSING-1",
            order_items=[
                {"name": "Profit Soap", "sku": "PROFIT-SOAP-1", "quantity": 2, "price": "90.00"},
            ],
        )

        summary = summarize_order_profit(order)

        self.assertFalse(summary["is_complete"])
        self.assertEqual(str(summary["revenue_total"]), "180.00")
        self.assertEqual(str(summary["actual_cost_total"]), "0.00")
        self.assertEqual(str(summary["profit_amount"]), "0.00")

    def test_order_profit_matches_product_by_unique_name_when_sku_missing(self):
        Product.objects.create(
            name="Profit Soap",
            sku="PROFIT-SOAP-NAME-1",
            actual_price="40.00",
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PROFIT-NAME-1",
            order_items=[
                {"name": "Profit Soap", "quantity": 2, "price": "90.00"},
            ],
        )

        summary = summarize_order_profit(order)

        self.assertTrue(summary["is_complete"])
        self.assertEqual(str(summary["revenue_total"]), "180.00")
        self.assertEqual(str(summary["actual_cost_total"]), "80.00")
        self.assertEqual(str(summary["profit_amount"]), "100.00")


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

    @patch("core.views.update_woocommerce_order_status")
    def test_woocommerce_order_status_syncs_after_local_status_change(self, mock_update_woocommerce_order_status):
        mock_update_woocommerce_order_status.return_value = {
            "skipped": False,
            "status": "processing",
        }
        order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-9001",
            woocommerce_order_id="9001",
            woocommerce_status="pending",
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
        mock_update_woocommerce_order_status.assert_called_once()
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                title="WooCommerce status synced",
                is_success=True,
            ).exists()
        )

    @patch("core.views.update_woocommerce_order_status")
    def test_missing_woocommerce_credentials_show_vendor_friendly_warning(self, mock_update_woocommerce_order_status):
        mock_update_woocommerce_order_status.side_effect = WooCommerceAPIError(
            "WooCommerce credentials are missing. Set WOOCOMMERCE_STORE_URL, "
            "WOOCOMMERCE_CONSUMER_KEY, and WOOCOMMERCE_CONSUMER_SECRET."
        )
        order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-9004",
            woocommerce_order_id="9004",
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
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertContains(response, "WooCommerce status sync is not configured in shared platform settings.")
        self.assertNotContains(response, "WOOCOMMERCE_STORE_URL")

    @patch("core.views.update_woocommerce_order_status")
    @patch("core.views.enqueue_whatsapp_notification")
    def test_woocommerce_accept_uses_existing_customer_phone(
        self,
        mock_enqueue_whatsapp_notification,
        mock_update_woocommerce_order_status,
    ):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        mock_update_woocommerce_order_status.return_value = {
            "skipped": False,
            "status": "processing",
        }
        order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-9002",
            woocommerce_order_id="9002",
            customer_phone="9876543210",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={"name": "Phone Fallback", "address_1": "Street 1"},
        )

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertEqual(order.manual_customer_phone, "9876543210")

    @patch("core.views.update_woocommerce_order_status")
    @patch("core.views.enqueue_whatsapp_notification")
    def test_woocommerce_accept_uses_raw_billing_phone(
        self,
        mock_enqueue_whatsapp_notification,
        mock_update_woocommerce_order_status,
    ):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        mock_update_woocommerce_order_status.return_value = {
            "skipped": False,
            "status": "processing",
        }
        order = ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-9003",
            woocommerce_order_id="9003",
            local_status=ShiprocketOrder.STATUS_NEW,
            shipping_address={"name": "Raw Phone", "address_1": "Street 1"},
            raw_payload={"billing": {"phone": "9876543211"}},
        )

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_ACCEPTED,
                f"order-{order.pk}-manual_customer_phone": "",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertEqual(order.manual_customer_phone, "9876543211")

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
    def test_accepted_order_payment_reminder_queues_template_message(
        self,
        mock_enqueue_whatsapp_notification,
        mock_attempt_inline_queue_send,
    ):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": True,
            "reason": "queued",
            "job": type("Job", (), {"pk": 401})(),
        }
        mock_attempt_inline_queue_send.return_value = type(
            "ProcessedJob",
            (),
            {"pk": 401, "status": WhatsAppNotificationQueue.STATUS_SUCCESS, "last_error": ""},
        )()
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PAY-REMINDER-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            total="799.00",
            shipping_address={"name": "Payment Customer", "phone": "9876543210"},
        )

        response = self.client.post(
            reverse("send_order_payment_reminder", args=[order.pk]),
            {"active_tab": ShiprocketOrder.STATUS_ACCEPTED},
            follow=True,
        )

        self.assertRedirects(response, f"{reverse('order_detail', args=[order.pk])}?tab={ShiprocketOrder.STATUS_ACCEPTED}")
        mock_enqueue_whatsapp_notification.assert_called_once()
        _, kwargs = mock_enqueue_whatsapp_notification.call_args
        self.assertEqual(kwargs["trigger"], WhatsAppNotificationLog.TRIGGER_PAYMENT_REMINDER)
        mock_attempt_inline_queue_send.assert_called_once()
        self.assertContains(response, "Payment reminder sent successfully")

    def test_mark_payment_received_sets_timestamp_and_logs_activity(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PAY-RECEIVED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            total="799.00",
        )

        response = self.client.post(
            reverse("mark_order_payment_received", args=[order.pk]),
            {"active_tab": ShiprocketOrder.STATUS_ACCEPTED},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, f"{reverse('order_detail', args=[order.pk])}?tab={ShiprocketOrder.STATUS_ACCEPTED}")
        self.assertIsNotNone(order.payment_received_at)
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
                title="Payment marked received",
            ).exists()
        )
        self.assertContains(response, "Payment marked as received")

    def test_payment_reminder_plan_uses_order_payment_template(self):
        WhatsAppSettings.objects.create(
            enabled=True,
            api_base_url="https://wa-api.cloud",
            api_key="token-123",
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-PAY-PLAN-1",
            channel_order_id="WC-1001",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            total="1250.00",
            shipping_address={"name": "Template Customer", "phone": "9876543210"},
        )

        plan = build_order_payment_reminder_idempotency_payload(order)

        self.assertTrue(plan["sendable"])
        self.assertEqual(plan["mode"], "template")
        self.assertEqual(plan["template_name"], "order_payment")
        self.assertEqual(plan["template_params"], {"1": "WC-1001", "2": "1250.00"})
        self.assertEqual(plan["phone_number"], "919876543210")

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
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_SHIPPED)
        self.assertEqual(order.cancellation_reason, "")

    def test_can_move_from_shipped_directly_to_delivered(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-DELIVER-DIRECT-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="1234567890123",
        )

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_DELIVERED,
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_DELIVERED)
        self.assertIsNotNone(order.delivered_at)

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

    def test_status_update_sets_packed_shipped_and_delivered_dates(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-STATUS-DATES-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            manual_customer_phone="9876543210",
            shipping_address={
                "name": "Receiver Name",
                "phone": "9876543210",
                "address_1": "Street 1",
                "pincode": "600001",
            },
        )

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED},
            follow=True,
        )
        order.refresh_from_db()
        self.assertIsNotNone(order.packed_at)

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_SHIPPED,
                f"order-{order.pk}-tracking_number": "AA123456789AA",
                f"order-{order.pk}-shipping_base_amount": "100.00",
            },
            follow=True,
        )
        order.refresh_from_db()
        self.assertIsNotNone(order.shipped_at)
        self.assertEqual(str(order.shipping_base_amount), "100.00")
        self.assertEqual(str(order.shipping_tax_amount), "18.0000")
        self.assertEqual(str(order.shipping_total_amount), "118.0000")

        self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_DELIVERED},
            follow=True,
        )
        order.refresh_from_db()
        self.assertIsNotNone(order.delivered_at)

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
    def setUp(self):
        admin_group, _ = Group.objects.get_or_create(name="admin")
        self.user = get_user_model().objects.create_user(username="labeladmin", password="testpass123")
        self.user.groups.add(admin_group)
        self.client.force_login(self.user)

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
        self.assertContains(response, "size: 4in 6in;")
        self.assertContains(response, "CH-1001")
        self.assertNotContains(response, "Order SR-LABEL-1")
        self.assertContains(response, "Manual Name")
        self.assertContains(response, "Manual Street 10")
        self.assertContains(response, "Warehouse Sender")
        self.assertContains(response, "Sender Street 5")
        self.assertEqual(order.label_print_count, 0)
        self.assertIsNone(order.last_label_printed_at)
        self.assertContains(response, reverse("shipping_label_pdf", args=[order.pk]))

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

    def test_shipping_label_page_renders_for_accepted_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Accepted Receiver",
                "phone": "9000000000",
                "address_1": "Accepted Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
        )

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accepted Receiver")
        self.assertContains(response, reverse("shipping_label_pdf", args=[order.pk]))

    def test_shipping_label_pdf_downloads_for_accepted_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-PDF-ACCEPTED-1",
            channel_order_id="CH-PDF-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Accepted PDF Receiver",
                "phone": "9000000000",
                "address_1": "Accepted PDF Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
        )

        response = self.client.get(reverse("shipping_label_pdf", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("shipping-label-CH-PDF-ACCEPTED-1", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_track_shipping_label_print_increments_count_for_accepted_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-TRACK-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )

        response = self.client.post(reverse("track_shipping_label_print", args=[order.pk]))
        order.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(order.label_print_count, 1)
        self.assertIsNotNone(order.last_label_printed_at)

    def test_shipping_label_redirects_for_non_packed_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-NOT-PACKED-1",
            local_status=ShiprocketOrder.STATUS_NEW,
        )

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]), follow=True)

        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))

    def test_shipping_label_shows_single_4x6_print_action(self):
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

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print 4x6 Label")
        self.assertContains(response, "Slot Receiver")
        self.assertContains(response, "size: 4in 6in;")

    def test_shipping_label_shows_ship_to_before_from_without_alt_phone(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-LABEL-ORDER-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Receiver First",
                "phone": "9000012345",
                "alternate_phone": "9000099999",
                "address_1": "To Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600001",
            },
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            address_1="From Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index('section-title">To</div>'), content.index('section-title">From</div>'))
        self.assertIn("Chennai", content)
        self.assertIn("TN", content)
        self.assertIn("600001", content)
        self.assertIn("Pincode 600001", content)
        self.assertIn("Phone", content)
        self.assertIn("9000012345", content)
        self.assertNotIn("Alt: 9000099999", content)

    def test_shipping_label_test_page_renders_without_tracking_order_prints(self):
        SenderAddress.objects.create(
            name="Warehouse Sender",
            phone="8888888888",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )

        response = self.client.get(reverse("shipping_label_test_4x6"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Test 4x6 Label")
        self.assertContains(response, "Helett H30C Pro")
        self.assertContains(response, "This sample label is for printer setup only.")
        self.assertContains(response, reverse("print_queue"))
        self.assertNotContains(response, "Save PDF")
        self.assertNotContains(response, "track-print/")


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
        self.assertContains(response, "Bulk 4x6 Shipping Labels (Order Packed)")
        self.assertNotContains(response, "New Receiver")
        self.assertContains(response, "Packed Receiver")
        self.assertContains(response, "Print 4x6 Labels")
        self.assertContains(response, "size: 4in 6in;")
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

    def test_bulk_labels_page_shows_already_printed_orders(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-PRINTED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=2,
            shipping_address={
                "name": "Printed Receiver",
                "phone": "9000000111",
                "address_1": "Printed Street 1",
                "city": "Chennai",
                "state": "TN",
                "country": "India",
                "pincode": "600011",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-PENDING-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=0,
            shipping_address={
                "name": "Pending Receiver",
                "phone": "9000000112",
                "address_1": "Pending Street 1",
                "city": "Madurai",
                "state": "TN",
                "country": "India",
                "pincode": "625011",
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

        response = self.client.get(reverse("bulk_shipping_labels_4x6"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Receiver")
        self.assertContains(response, "Printed Receiver")
        self.assertContains(response, "SR-BULK-PRINTED-1")

    def test_bulk_labels_page_exposes_pdf_download_action(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-PDF-ACTION-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Pdf Action Receiver",
                "phone": "9000000113",
                "address_1": "Pdf Action Street 1",
                "city": "Erode",
                "state": "TN",
                "country": "India",
                "pincode": "638010",
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

        response = self.client.get(reverse("bulk_shipping_labels_4x6"), {"order_id": [order.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save as PDF")
        self.assertContains(response, reverse("bulk_shipping_labels_pdf"))

    def test_bulk_labels_pdf_download_returns_pdf(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-BULK-PDF-DL-1",
            channel_order_id="CH-BULK-PDF-DL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Bulk Pdf Receiver",
                "phone": "9000000114",
                "address_1": "Bulk Pdf Street 1",
                "city": "Erode",
                "state": "TN",
                "country": "India",
                "pincode": "638011",
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

        response = self.client.get(reverse("bulk_shipping_labels_pdf"), {"order_id": [order.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)
        self.assertIn(b"CH-BULK-PDF-DL-1", response.content)

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

    def test_bulk_labels_render_one_4x6_page_per_order(self):
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

        response = self.client.get(reverse("bulk_shipping_labels_4x6"))

        self.assertEqual(response.status_code, 200)
        orders = response.context["orders"]
        self.assertEqual([order.shiprocket_order_id for order in orders], ["SR-BULK-SLOT-2", "SR-BULK-SLOT-1"])
        self.assertContains(response, "Receiver 1")
        self.assertContains(response, "Receiver 2")
        self.assertContains(response, "page-break-after: always;")

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

    def test_home_shows_current_month_profit_total(self):
        current_month = timezone.now()
        previous_month = current_month - timedelta(days=35)
        Product.objects.create(name="Profit Soap A", sku="HOME-PROFIT-A", actual_price="40.00")
        Product.objects.create(name="Profit Soap B", sku="HOME-PROFIT-B", actual_price="25.00")
        Product.objects.create(name="Profit Soap C", sku="HOME-PROFIT-C", actual_price="10.00")
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-SALES-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            total="250.00",
            order_date=current_month,
            order_items=[
                {"name": "Profit Soap A", "sku": "HOME-PROFIT-A", "quantity": 2, "price": "90.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-SALES-2",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            total="175.00",
            order_date=current_month,
            order_items=[
                {"name": "Profit Soap B", "quantity": 1, "price": "75.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-SALES-CANCELLED-1",
            local_status=ShiprocketOrder.STATUS_CANCELLED,
            total="1000.00",
            order_date=current_month,
            order_items=[
                {"name": "Profit Soap C", "sku": "HOME-PROFIT-C", "quantity": 1, "price": "999.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-HOME-SALES-OLD-1",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
            total="999.00",
            order_date=previous_month,
            order_items=[
                {"name": "Profit Soap C", "sku": "HOME-PROFIT-C", "quantity": 1, "price": "999.00"},
            ],
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monthly sales")
        self.assertContains(response, "Rs 175.00")
        self.assertContains(response, "Monthly profit")
        self.assertContains(response, "Rs 50.00")

    def test_home_shows_action_sections_and_work_queues(self):
        Product.objects.create(
            name="Low Stock Powder",
            sku="SKU-HOME-LOW-1",
            stock_quantity=2,
            reorder_level=5,
        )
        Product.objects.create(
            name="No Stock Soap",
            sku="SKU-HOME-NO-1",
            stock_quantity=0,
            reorder_level=4,
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
        self.assertContains(response, "Home Dashboard")
        self.assertContains(response, "This Month Order Status")
        self.assertContains(response, "Stock Dashboard")
        self.assertContains(response, "Low Stock Items")
        self.assertContains(response, "No Stock Items")
        self.assertContains(response, "Low Qty Stock")
        self.assertContains(response, "Out Of Stock")
        self.assertContains(response, "Low Stock Powder")
        self.assertContains(response, "No Stock Soap")
        self.assertContains(response, "Qty 2")
        self.assertContains(response, "Qty 0")
        self.assertContains(response, "Open Stock Management")
        self.assertContains(response, "orders-dashboard-shell")
        self.assertContains(response, "dashboard-stock-list-card")
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
            channel_order_id="CH-ORDER-MGMT-1",
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
        self.assertContains(response, order.channel_order_id)
        self.assertNotContains(response, f"Shiprocket: {order.shiprocket_order_id}")
        self.assertContains(response, "Workflow: Order Accepted")
        self.assertContains(response, "Tracking: 1234567890123")
        self.assertContains(response, "Packing Checklist")
        self.assertContains(response, "order-status-tabs")
        self.assertContains(response, 'data-label="Order ID"', html=False)
        self.assertContains(response, 'data-label="Move Order"', html=False)

    def test_order_management_shows_stock_shortage_indicator_for_new_order(self):
        Product.objects.create(
            name="Short Stock Soap",
            sku="SHORT-STOCK-1",
            stock_quantity=1,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ORDER-MGMT-STOCK-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            order_items=[
                {"name": "Short Stock Soap", "sku": "SHORT-STOCK-1", "quantity": 3, "price": "99"},
            ],
        )

        response = self.client.get(
            reverse("order_management"),
            {"tab": ShiprocketOrder.STATUS_NEW},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.shiprocket_order_id)
        self.assertContains(response, "Stock short")
        self.assertContains(response, "required 3,")
        self.assertContains(response, "available 1")


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
        self.assertContains(response, reverse("shipping_label_test_4x6"))
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

    def test_partial_manual_update_preserves_existing_unposted_fields(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-MANUAL-PARTIAL-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            manual_customer_name="Saved Name",
            manual_customer_email="saved@example.com",
            manual_customer_alternate_phone="9000000001",
            manual_shipping_city="Erode",
            manual_shipping_state="TN",
            manual_shipping_country="India",
        )

        response = self.client.post(
            reverse("update_shiprocket_order", args=[order.pk]),
            {
                "manual_customer_phone": "9876543210",
                "manual_shipping_address_1": "55 Updated Street",
                "manual_shipping_pincode": "638001",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.manual_customer_phone, "9876543210")
        self.assertEqual(order.manual_shipping_address_1, "55 Updated Street")
        self.assertEqual(order.manual_shipping_pincode, "638001")
        self.assertEqual(order.manual_customer_name, "Saved Name")
        self.assertEqual(order.manual_customer_email, "saved@example.com")
        self.assertEqual(order.manual_customer_alternate_phone, "9000000001")
        self.assertEqual(order.manual_shipping_city, "Erode")
        self.assertEqual(order.manual_shipping_state, "TN")
        self.assertEqual(order.manual_shipping_country, "India")


class ShiprocketOrderTrackingUpdateViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="trackingadmin", password="testpass123")
        self.client.force_login(user)

    def test_tracking_update_works(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-TRACKING-EDIT-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="AA123456789AA",
        )

        response = self.client.post(
            reverse("update_shiprocket_order_tracking", args=[order.pk]),
            {"tracking_number": "BB123456789BB", "shipping_base_amount": "120.00"},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.tracking_number, "BB123456789BB")
        self.assertEqual(str(order.shipping_base_amount), "120.00")
        self.assertEqual(str(order.shipping_total_amount), "141.6000")

    def test_tracking_update_rejects_invalid_length(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-TRACKING-EDIT-2",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="AA123456789AA",
        )

        response = self.client.post(
            reverse("update_shiprocket_order_tracking", args=[order.pk]),
            {"tracking_number": "SHORT123", "shipping_base_amount": "120.00"},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.tracking_number, "AA123456789AA")


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
                "settings-account_name": "Mathukai_Updates",
                "settings-account_id": "acc_123",
                "action": "save_settings",
            },
            follow=True,
        )

        settings_row = WhatsAppSettings.get_default()
        self.assertRedirects(response, reverse("whatsapp_settings"))
        self.assertTrue(settings_row.enabled)
        self.assertEqual(settings_row.api_base_url, "http://127.0.0.1:8080")
        self.assertEqual(settings_row.api_key, "whm_test_key_123")
        self.assertEqual(settings_row.account_name, "Mathukai_Updates")
        self.assertEqual(settings_row.account_id, "acc_123")

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


class WhatomateTextSendTests(TestCase):
    @patch("core.whatomate._json_request")
    @patch("core.whatomate._ensure_contact_id")
    def test_send_test_message_keeps_plain_text_payload_minimal(self, mock_ensure_contact_id, mock_json_request):
        mock_ensure_contact_id.return_value = "contact_123"
        mock_json_request.return_value = {"status": "success", "id": "msg_123"}

        result = send_test_whatsapp_message(
            phone_number="9952975768",
            message_text="Test ping",
            config_overrides={
                "enabled": True,
                "base_url": "https://whatomate.mathukaiorganic.store/api",
                "api_key": "whm_test_key_123",
                "account_name": "Mathukai_Updates",
                "account_id": "acc_123",
            },
        )

        self.assertTrue(result["sent"])
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "type": "text",
                "text": "Test ping",
            },
        )

    def test_libromi_headers_use_bearer_token_from_api_key(self):
        headers = _get_headers(
            {
                "base_url": "https://wa-api.cloud",
                "api_key": "libromi_token_123",
            }
        )

        self.assertEqual(headers["Authorization"], "Bearer libromi_token_123")
        self.assertNotIn("X-API-Key", headers)

    def test_libromi_check_connection_validates_config_without_contact_api_probe(self):
        result = check_api_connection(
            config_overrides={
                "enabled": True,
                "base_url": "https://wa-api.cloud",
                "api_key": "libromi_token_123",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "libromi")
        self.assertEqual(result["endpoint"], "/api/v1/messages")

    def test_meta_cloud_check_connection_requires_phone_number_id(self):
        with self.assertRaises(WhatomateNotificationError):
            check_api_connection(
                config_overrides={
                    "enabled": True,
                    "base_url": "https://graph.facebook.com/v23.0",
                    "api_key": "meta_token_123",
                    "account_id": "",
                }
            )

    def test_meta_cloud_check_connection_uses_phone_number_messages_endpoint(self):
        result = check_api_connection(
            config_overrides={
                "enabled": True,
                "base_url": "https://graph.facebook.com/v23.0",
                "api_key": "meta_token_123",
                "account_id": "1234567890",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "meta")
        self.assertEqual(result["endpoint"], "/1234567890/messages")

    def test_libromi_template_sync_is_not_required(self):
        result = sync_templates_from_api(
            config_overrides={
                "enabled": True,
                "base_url": "https://wa-api.cloud",
                "api_key": "libromi_token_123",
            }
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["provider"], "libromi")
        self.assertEqual(result["synced_count"], 0)

    @patch("core.whatomate._json_request")
    def test_libromi_template_message_uses_cloud_api_payload(self, mock_json_request):
        mock_json_request.return_value = {"status": "success", "id": "wamid_123"}

        result = send_test_template_message(
            phone_number="9952975768",
            template_name="optin_templet",
            template_params={"1": "SR-1001"},
            config_overrides={
                "enabled": True,
                "base_url": "https://wa-api.cloud",
                "api_key": "libromi_token_123",
            },
        )

        self.assertTrue(result["sent"])
        self.assertEqual(result["endpoint"], "/api/v1/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "optin_templet",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": "SR-1001",
                                }
                            ],
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    def test_libromi_template_message_without_parameters_keeps_body_component(self, mock_json_request):
        mock_json_request.return_value = {"status": "success", "id": "wamid_123"}

        result = send_test_template_message(
            phone_number="9952975768",
            template_name="order_confirm_1",
            template_params={},
            config_overrides={
                "enabled": True,
                "base_url": "https://wa-api.cloud",
                "api_key": "libromi_token_123",
            },
        )

        self.assertTrue(result["sent"])
        self.assertEqual(result["endpoint"], "/api/v1/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "order_confirm_1",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    def test_accepted_order_uses_configured_libromi_confirmation_template(self, mock_json_request):
        mock_json_request.return_value = {"status": "success", "id": "wamid_accepted_123"}
        settings_row = WhatsAppSettings.get_default()
        settings_row.enabled = True
        settings_row.api_base_url = "https://wa-api.cloud"
        settings_row.api_key = "libromi_token_123"
        settings_row.save(update_fields=["enabled", "api_base_url", "api_key", "updated_at"])
        WhatsAppStatusTemplateConfig.objects.update_or_create(
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            defaults={
                "enabled": True,
                "template_name": "order_confirm_1",
                "template_param_mapping": {},
            },
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Test Customer",
            customer_phone="9952975768",
            shipping_address={"phone": "9952975768", "name": "Test Customer"},
        )

        result = send_order_status_update(order, previous_status=ShiprocketOrder.STATUS_NEW)

        self.assertTrue(result["sent"])
        self.assertEqual(result["mode"], "template")
        self.assertEqual(result["template_name"], "order_confirm_1")
        self.assertEqual(result["endpoint"], "/api/v1/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "order_confirm_1",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    def test_shipped_order_uses_woocommerce_order_number_in_libromi_template(self, mock_json_request):
        mock_json_request.return_value = {"status": "success", "id": "wamid_shipped_123"}
        settings_row = WhatsAppSettings.get_default()
        settings_row.enabled = True
        settings_row.api_base_url = "https://wa-api.cloud"
        settings_row.api_key = "libromi_token_123"
        settings_row.save(update_fields=["enabled", "api_base_url", "api_key", "updated_at"])
        WhatsAppStatusTemplateConfig.objects.update_or_create(
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            defaults={
                "enabled": True,
                "template_name": "order_shipped_1",
                "template_param_mapping": {
                    "1": "channel_order_id",
                    "2": "tracking_number",
                },
            },
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-9001",
            channel_order_id="9001",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_name="Test Customer",
            customer_phone="9952975768",
            tracking_number="AA123456789AA",
            shipping_address={"phone": "9952975768", "name": "Test Customer"},
        )

        result = send_order_status_update(order, previous_status=ShiprocketOrder.STATUS_PACKED)

        self.assertTrue(result["sent"])
        self.assertEqual(result["mode"], "template")
        self.assertEqual(result["template_name"], "order_shipped_1")
        self.assertEqual(result["endpoint"], "/api/v1/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "order_shipped_1",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": "9001",
                                },
                                {
                                    "type": "text",
                                    "text": "AA123456789AA",
                                },
                            ],
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    def test_completed_order_uses_delivered_libromi_template(self, mock_json_request):
        mock_json_request.return_value = {"status": "success", "id": "wamid_delivered_123"}
        settings_row = WhatsAppSettings.get_default()
        settings_row.enabled = True
        settings_row.api_base_url = "https://wa-api.cloud"
        settings_row.api_key = "libromi_token_123"
        settings_row.save(update_fields=["enabled", "api_base_url", "api_key", "updated_at"])
        WhatsAppStatusTemplateConfig.objects.update_or_create(
            local_status=ShiprocketOrder.STATUS_COMPLETED,
            defaults={
                "enabled": True,
                "template_name": "order_delivered",
                "template_param_mapping": {
                    "1": "channel_order_id",
                },
            },
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-9002",
            channel_order_id="9002",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
            customer_name="Test Customer",
            customer_phone="9952975768",
            shipping_address={"phone": "9952975768", "name": "Test Customer"},
        )

        result = send_order_status_update(order, previous_status=ShiprocketOrder.STATUS_SHIPPED)

        self.assertTrue(result["sent"])
        self.assertEqual(result["mode"], "template")
        self.assertEqual(result["template_name"], "order_delivered")
        self.assertEqual(result["endpoint"], "/api/v1/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "order_delivered",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": "9002",
                                },
                            ],
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    def test_meta_cloud_template_message_uses_graph_api_payload(self, mock_json_request):
        mock_json_request.return_value = {"messages": [{"id": "wamid_123"}]}

        result = send_test_template_message(
            phone_number="9952975768",
            template_name="optin_templet",
            template_params={"1": "SR-1001"},
            config_overrides={
                "enabled": True,
                "base_url": "https://graph.facebook.com/v23.0",
                "api_key": "meta_token_123",
                "account_id": "1234567890",
            },
        )

        self.assertTrue(result["sent"])
        self.assertEqual(result["endpoint"], "/1234567890/messages")
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "messaging_product": "whatsapp",
                "to": "919952975768",
                "type": "template",
                "template": {
                    "name": "optin_templet",
                    "language": {
                        "code": "en",
                        "policy": "deterministic",
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": "SR-1001",
                                }
                            ],
                        }
                    ],
                },
            },
        )

    @patch("core.whatomate._json_request")
    @patch("core.whatomate._ensure_contact_id")
    def test_order_enquiry_reply_keeps_plain_text_payload_minimal(self, mock_ensure_contact_id, mock_json_request):
        mock_ensure_contact_id.return_value = "contact_123"
        mock_json_request.return_value = {"status": "success", "id": "msg_123"}
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WA-ENQ-1",
            customer_name="Customer One",
            customer_phone="9952975768",
            shipping_address={"phone": "9952975768", "name": "Customer One"},
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
        )

        result = send_order_enquiry_reply(
            order,
            incoming_phone_number="919952975768",
            config_overrides={
                "enabled": True,
                "base_url": "https://whatomate.mathukaiorganic.store/api",
                "api_key": "whm_test_key_123",
                "account_name": "Mathukai_Updates",
                "account_id": "acc_123",
            },
        )

        self.assertTrue(result["sent"])
        self.assertEqual(
            mock_json_request.call_args.kwargs["payload"],
            {
                "type": "text",
                "text": "Hi Customer One, Your order SR-WA-ENQ-1 is currently Order Accepted. Tracking number is not assigned yet. We will share the next update soon.",
            },
        )

    @patch("core.whatomate._json_request")
    def test_create_contact_resolves_account_id_from_account_name(self, mock_json_request):
        mock_json_request.side_effect = [
            {
                "status": "success",
                "data": {
                    "items": [
                        {"id": "acc_123", "name": "Mathukai_Updates"},
                    ]
                },
            },
            {
                "status": "success",
                "data": {"id": "contact_123"},
            },
        ]

        contact_id = _create_contact(
            phone_number="919952975768",
            name="Customer One",
            config={
                "base_url": "https://whatomate.mathukaiorganic.store/api",
                "api_key": "whm_test_key_123",
                "account_name": "Mathukai_Updates",
            },
        )

        self.assertEqual(contact_id, "contact_123")
        self.assertEqual(
            mock_json_request.call_args_list[1].kwargs["payload"],
            {
                "phone_number": "919952975768",
                "name": "Customer One",
                "account_id": "acc_123",
            },
        )


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

    @override_settings(ALLOWED_HOSTS=["localhost"], SECURE_SSL_REDIRECT=True)
    def test_internal_webhook_test_bypasses_https_redirect(self):
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

    @patch("core.views.send_order_enquiry_reply")
    def test_incoming_message_webhook_sends_order_reply(self, mock_send_order_enquiry_reply):
        mock_send_order_enquiry_reply.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "text",
            "message_text": "Order update message",
            "external_message_id": "reply_001",
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-INCOMING-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_phone="919876543210",
            tracking_number="AA123456789AA",
            shipping_address={"phone": "919876543210", "name": "Incoming Customer"},
        )
        payload = {
            "event_id": "evt_incoming_001",
            "event_type": "message_incoming",
            "message": {
                "from": "919876543210",
                "text": "Where is my order?",
            },
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_send_order_enquiry_reply.assert_called_once()
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_INCOMING,
            shiprocket_order_id=order.shiprocket_order_id,
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.is_success)
        self.assertEqual(log.webhook_event_id, "evt_incoming_001")
        self.assertEqual(log.phone_number, "919876543210")
        self.assertTrue(
            OrderActivityLog.objects.filter(
                order=order,
                event_type=OrderActivityLog.EVENT_WHATSAPP_WEBHOOK,
                title="WhatsApp customer enquiry received",
                is_success=True,
            ).exists()
        )
        self.assertContains(response, '"replied": true', status_code=200)

    @patch("core.views.send_order_enquiry_reply")
    def test_incoming_message_webhook_reads_phone_from_contact_mobile(self, mock_send_order_enquiry_reply):
        mock_send_order_enquiry_reply.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "text",
            "message_text": "Order update message",
            "external_message_id": "reply_003",
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-INCOMING-2",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_phone="919876543210",
            tracking_number="AA123456789AA",
            shipping_address={"phone": "919876543210", "name": "Incoming Mobile Customer"},
        )
        payload = {
            "event_id": "evt_incoming_003",
            "event_type": "message_incoming",
            "contact": {
                "mobile": "9876543210",
            },
            "message": {
                "body": "Order update please",
            },
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_send_order_enquiry_reply.assert_called_once()
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_INCOMING,
            shiprocket_order_id=order.shiprocket_order_id,
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.is_success)
        self.assertEqual(log.phone_number, "919876543210")
        self.assertContains(response, '"replied": true', status_code=200)

    @patch("core.views.send_order_enquiry_reply")
    def test_incoming_message_webhook_reads_meta_style_nested_payload(self, mock_send_order_enquiry_reply):
        mock_send_order_enquiry_reply.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "text",
            "message_text": "Order update message",
            "external_message_id": "reply_004",
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-INCOMING-3",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_phone="919876543210",
            tracking_number="AA123456789AA",
            shipping_address={"phone": "919876543210", "name": "Nested Payload Customer"},
        )
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "contacts": [
                                    {
                                        "profile": {"name": "Nested Payload Customer"},
                                        "wa_id": "919876543210",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "919876543210",
                                        "id": "wamid.nested.001",
                                        "type": "text",
                                        "text": {"body": "Where is my order?"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_send_order_enquiry_reply.assert_called_once()
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_INCOMING,
            shiprocket_order_id=order.shiprocket_order_id,
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.is_success)
        self.assertEqual(log.phone_number, "919876543210")
        self.assertContains(response, '"replied": true', status_code=200)

    @patch("core.views.resolve_phone_number_from_contact_id")
    @patch("core.views.send_order_enquiry_reply")
    def test_incoming_message_webhook_reads_message_new_payload_via_contact_id(
        self,
        mock_send_order_enquiry_reply,
        mock_resolve_phone_number_from_contact_id,
    ):
        mock_resolve_phone_number_from_contact_id.return_value = "919876543210"
        mock_send_order_enquiry_reply.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "text",
            "message_text": "Order update message",
            "external_message_id": "reply_005",
        }
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-WEBHOOK-INCOMING-4",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            customer_phone="919876543210",
            tracking_number="AA123456789AA",
            shipping_address={"phone": "919876543210", "name": "Message New Customer"},
        )
        payload = {
            "type": "message:new",
            "payload": {
                "id": "evt_message_new_001",
                "contact_id": "contact_123",
                "direction": "incoming",
                "type": "text",
                "content": {"text": "Where is my order?"},
                "timestamp": "2026-04-11T17:00:00Z",
            },
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_resolve_phone_number_from_contact_id.assert_called_once_with("contact_123")
        mock_send_order_enquiry_reply.assert_called_once()
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_INCOMING,
            shiprocket_order_id=order.shiprocket_order_id,
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.is_success)
        self.assertEqual(log.phone_number, "919876543210")
        self.assertContains(response, '"replied": true', status_code=200)

    @patch("core.views.send_no_order_found_reply")
    @patch("core.views.send_order_enquiry_reply")
    def test_incoming_message_webhook_without_matching_order_sends_no_order_reply(
        self,
        mock_send_order_enquiry_reply,
        mock_send_no_order_found_reply,
    ):
        mock_send_no_order_found_reply.return_value = {
            "sent": True,
            "phone_number": "919000000000",
            "mode": "text",
            "message_text": "No order found message",
            "external_message_id": "reply_002",
        }
        payload = {
            "event_id": "evt_incoming_002",
            "event_type": "message_incoming",
            "message": {
                "from": "919000000000",
                "text": "Order status?",
            },
        }

        response = self.client.post(
            reverse("whatomate_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_send_order_enquiry_reply.assert_not_called()
        mock_send_no_order_found_reply.assert_called_once()
        log = WhatsAppNotificationLog.objects.filter(
            trigger=WhatsAppNotificationLog.TRIGGER_WEBHOOK_INCOMING,
            webhook_event_id="evt_incoming_002",
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.is_success)
        self.assertEqual(log.phone_number, "919000000000")
        self.assertContains(response, '"replied": true', status_code=200)

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
        self.assertContains(response, "Expense People")
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

    def test_admin_utilities_can_save_expense_person(self):
        response = self.client.post(
            reverse("admin_utilities"),
            {"action": "save_expense_person", "name": "Kumar", "is_active": "on"},
            follow=True,
        )

        self.assertRedirects(response, reverse("admin_utilities"))
        self.assertTrue(ExpensePerson.objects.filter(name="Kumar", is_active=True).exists())
        self.assertContains(response, "Saved expense person Kumar.")


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


class WhatsAppTenantIsolationTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Vendor A", slug="vendor-a")
        self.tenant_b = Tenant.objects.create(name="Vendor B", slug="vendor-b")
        settings_row = WhatsAppSettings.get_default()
        settings_row.enabled = True
        settings_row.api_base_url = "https://wa-api.cloud"
        settings_row.api_key = "shared-token"
        settings_row.save(update_fields=["enabled", "api_base_url", "api_key", "updated_at"])

    def _order(self, tenant, order_id):
        return ShiprocketOrder.objects.create(
            tenant=tenant,
            shiprocket_order_id=order_id,
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_phone="9876543210",
            shipping_address={"phone": "9876543210", "name": "Customer"},
        )

    def test_whatsapp_templates_and_status_configs_are_unique_per_tenant(self):
        WhatsAppTemplate.objects.create(
            tenant=self.tenant_a,
            name="order_accepted_template",
            language="en",
            template_id="tpl-a",
        )
        WhatsAppTemplate.objects.create(
            tenant=self.tenant_b,
            name="order_accepted_template",
            language="en",
            template_id="tpl-b",
        )
        WhatsAppStatusTemplateConfig.objects.create(
            tenant=self.tenant_a,
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            enabled=True,
            template_name="order_accepted_template",
        )
        WhatsAppStatusTemplateConfig.objects.create(
            tenant=self.tenant_b,
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            enabled=True,
            template_name="order_accepted_template",
        )

        self.assertEqual(WhatsAppTemplate.objects.filter(name="order_accepted_template").count(), 2)
        self.assertEqual(
            WhatsAppStatusTemplateConfig.objects.filter(
                tenant__in=[self.tenant_a, self.tenant_b],
                local_status=ShiprocketOrder.STATUS_ACCEPTED,
            ).count(),
            2,
        )

    @override_settings(WHATOMATE_ENABLED=True, WHATOMATE_BASE_URL="https://global.example", WHATOMATE_API_KEY="global")
    def test_non_default_vendor_uses_shared_whatsapp_settings(self):
        WhatsAppSettings.objects.filter(tenant=self.tenant_a).update(enabled=False, api_base_url="", api_key="")
        order = self._order(self.tenant_a, "SR-WA-TENANT-DISABLED")

        plan = build_order_status_idempotency_payload(order)

        self.assertTrue(plan["sendable"])
        self.assertEqual(plan["config"]["api_key"], "shared-token")

    def test_enqueue_and_worker_logs_are_tenant_scoped(self):
        order = self._order(self.tenant_a, "SR-WA-TENANT-QUEUE")
        other_plan = build_order_status_idempotency_payload(order)
        WhatsAppNotificationLog.objects.create(
            tenant=self.tenant_b,
            shiprocket_order_id="SR-OTHER-TENANT",
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            idempotency_key=other_plan["idempotency_key"],
            is_success=True,
        )

        enqueue_result = enqueue_whatsapp_notification(
            order=order,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
            previous_status=ShiprocketOrder.STATUS_NEW,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )

        self.assertTrue(enqueue_result["queued"])
        self.assertEqual(enqueue_result["job"].tenant, self.tenant_a)

    @patch("core.whatsapp_queue.send_order_status_update")
    def test_worker_processes_only_requested_tenant(self, mock_send_order_status_update):
        mock_send_order_status_update.return_value = {
            "sent": True,
            "phone_number": "919876543210",
            "mode": "text",
            "request_payload": {"text": "ok"},
            "response_payload": {"status": "success"},
        }
        order_a = self._order(self.tenant_a, "SR-WA-TENANT-A")
        order_b = self._order(self.tenant_b, "SR-WA-TENANT-B")
        job_a = enqueue_whatsapp_notification(
            order=order_a,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )["job"]
        job_b = enqueue_whatsapp_notification(
            order=order_b,
            trigger=WhatsAppNotificationLog.TRIGGER_RESEND,
            previous_status=ShiprocketOrder.STATUS_ACCEPTED,
            current_status=ShiprocketOrder.STATUS_ACCEPTED,
            initiated_by="tester",
        )["job"]

        summary = process_whatsapp_notification_queue(limit=5, worker_name="tenant-worker", tenant=self.tenant_a)

        job_a.refresh_from_db()
        job_b.refresh_from_db()
        self.assertEqual(summary["success"], 1)
        self.assertEqual(job_a.status, WhatsAppNotificationQueue.STATUS_SUCCESS)
        self.assertEqual(job_b.status, WhatsAppNotificationQueue.STATUS_PENDING)
        self.assertTrue(
            WhatsAppNotificationLog.objects.filter(
                tenant=self.tenant_a,
                shiprocket_order_id=order_a.shiprocket_order_id,
                is_success=True,
            ).exists()
        )

    @patch("core.whatomate._json_request")
    def test_template_sync_writes_to_requested_tenant(self, mock_json_request):
        mock_json_request.return_value = {
            "items": [
                {
                    "id": "tpl-shared",
                    "name": "shared_status_template",
                    "language": "en",
                    "status": "APPROVED",
                }
            ]
        }

        sync_templates_from_api(
            config_overrides={"enabled": True, "base_url": "https://whatomate.example", "api_key": "a"},
            tenant=self.tenant_a,
        )
        sync_templates_from_api(
            config_overrides={"enabled": True, "base_url": "https://whatomate.example", "api_key": "b"},
            tenant=self.tenant_b,
        )

        self.assertTrue(
            WhatsAppTemplate.objects.filter(
                tenant=self.tenant_a,
                name="shared_status_template",
                language="en",
            ).exists()
        )
        self.assertTrue(
            WhatsAppTemplate.objects.filter(
                tenant=self.tenant_b,
                name="shared_status_template",
                language="en",
            ).exists()
        )


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


class FreshStartInventoryCommandTests(TestCase):
    def test_fresh_start_inventory_requires_confirm_for_deletion(self):
        product = Product.objects.create(name="Reset Product", sku="RESET-1", stock_quantity=4)
        ShiprocketOrder.objects.create(shiprocket_order_id="RESET-ORDER-1")

        stdout = StringIO()
        call_command("fresh_start_inventory", stdout=stdout)

        self.assertIn("Fresh start dry run", stdout.getvalue())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertTrue(ShiprocketOrder.objects.filter(shiprocket_order_id="RESET-ORDER-1").exists())

    def test_fresh_start_inventory_deletes_orders_products_and_related_rows(self):
        product = Product.objects.create(name="Reset Product", sku="RESET-1", stock_quantity=4)
        order = ShiprocketOrder.objects.create(shiprocket_order_id="RESET-ORDER-1")
        StockMovement.objects.create(
            product=product,
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            movement_type=StockMovement.TYPE_MANUAL_ADD,
            quantity_delta=4,
            quantity_before=0,
            quantity_after=4,
        )
        OrderActivityLog.objects.create(order=order, shiprocket_order_id=order.shiprocket_order_id)
        WhatsAppNotificationLog.objects.create(order=order, shiprocket_order_id=order.shiprocket_order_id)
        WhatsAppNotificationQueue.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
        )

        stdout = StringIO()
        call_command("fresh_start_inventory", "--confirm", stdout=stdout)

        self.assertIn("Fresh start complete", stdout.getvalue())
        self.assertFalse(Product.objects.exists())
        self.assertFalse(ShiprocketOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(OrderActivityLog.objects.exists())
        self.assertFalse(WhatsAppNotificationLog.objects.exists())
        self.assertFalse(WhatsAppNotificationQueue.objects.exists())


class FreshStartOrdersCommandTests(TestCase):
    def test_fresh_start_orders_requires_confirm_for_deletion(self):
        product = Product.objects.create(name="Keep Product", sku="KEEP-1", stock_quantity=4)
        ShiprocketOrder.objects.create(shiprocket_order_id="RESET-ORDER-1")

        stdout = StringIO()
        call_command("fresh_start_orders", stdout=stdout)

        self.assertIn("Fresh orders dry run", stdout.getvalue())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertTrue(ShiprocketOrder.objects.filter(shiprocket_order_id="RESET-ORDER-1").exists())

    def test_fresh_start_orders_deletes_orders_and_order_related_rows_only(self):
        product = Product.objects.create(name="Keep Product", sku="KEEP-1", stock_quantity=4)
        order = ShiprocketOrder.objects.create(shiprocket_order_id="RESET-ORDER-1")
        StockMovement.objects.create(
            product=product,
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            movement_type=StockMovement.TYPE_ORDER_ACCEPTED,
            quantity_delta=-1,
            quantity_before=4,
            quantity_after=3,
        )
        unrelated_movement = StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.TYPE_MANUAL_ADD,
            quantity_delta=4,
            quantity_before=0,
            quantity_after=4,
        )
        OrderActivityLog.objects.create(order=order, shiprocket_order_id=order.shiprocket_order_id)
        OrderActivityLog.objects.create(shiprocket_order_id=order.shiprocket_order_id)
        WhatsAppNotificationLog.objects.create(order=order, shiprocket_order_id=order.shiprocket_order_id)
        WhatsAppNotificationQueue.objects.create(
            order=order,
            shiprocket_order_id=order.shiprocket_order_id,
            trigger=WhatsAppNotificationLog.TRIGGER_STATUS_CHANGE,
        )

        stdout = StringIO()
        call_command("fresh_start_orders", "--confirm", stdout=stdout)

        self.assertIn("Fresh orders complete", stdout.getvalue())
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(ShiprocketOrder.objects.exists())
        self.assertEqual(list(StockMovement.objects.values_list("pk", flat=True)), [unrelated_movement.pk])
        self.assertFalse(OrderActivityLog.objects.exists())
        self.assertFalse(WhatsAppNotificationLog.objects.exists())
        self.assertFalse(WhatsAppNotificationQueue.objects.exists())


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

    def test_manual_mapping_is_used_when_template_metadata_is_not_synced(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-MANUAL-MAP-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Fallback Name",
            shipping_address={"name": "Manual Customer", "phone": "9876543210"},
        )

        params = _build_template_params_for_status(
            [],
            order,
            field_mapping={
                "1": "customer_name",
                "2": "order_id",
                "3": "status",
            },
        )

        self.assertEqual(
            params,
            {
                "1": "Manual Customer",
                "2": "SR-MANUAL-MAP-1",
                "3": "Order Accepted",
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

    def test_ops_viewer_home_shows_live_order_counts(self):
        previous_month = timezone.now() - timedelta(days=35)
        Product.objects.create(name="Viewer Profit Accepted", sku="VIEWER-PROFIT-A", actual_price="300.00")
        Product.objects.create(name="Viewer Profit Shipped", sku="VIEWER-PROFIT-S", actual_price="50.00")
        Product.objects.create(name="Viewer Profit Cancelled", sku="VIEWER-PROFIT-C", actual_price="10.00")
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-HOME-OLD-PACKED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            customer_name="Old Packed Count",
            order_date=previous_month,
            total="999.00",
            order_items=[
                {"name": "Viewer Profit Accepted", "sku": "VIEWER-PROFIT-A", "quantity": 1, "price": "999.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-HOME-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Accepted Count",
            total="1200.50",
            order_items=[
                {"name": "Viewer Profit Accepted", "sku": "VIEWER-PROFIT-A", "quantity": 2, "price": "500.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-HOME-SHIPPED-1",
            local_status=ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
            customer_name="Shipped Count",
            total="300.00",
            order_items=[
                {"name": "Viewer Profit Shipped", "sku": "VIEWER-PROFIT-S", "quantity": 1, "price": "150.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-HOME-CANCELLED-1",
            local_status=ShiprocketOrder.STATUS_CANCELLED,
            customer_name="Cancelled Count",
            total="700.00",
            order_items=[
                {"name": "Viewer Profit Cancelled", "sku": "VIEWER-PROFIT-C", "quantity": 1, "price": "700.00"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live order status and stock overview.")
        self.assertContains(response, "Sale Value")
        self.assertContains(response, "Profit")
        self.assertContains(response, "Rs 1500.50")
        self.assertContains(response, "Rs 500.00")
        self.assertContains(response, '<span class="ops-home-card-label">Accepted</span>', html=False)
        self.assertContains(response, '<span class="ops-home-card-value">2</span>', html=False)
        self.assertContains(response, '<span class="ops-home-card-label">Shipped</span>', html=False)
        self.assertContains(response, '<span class="ops-home-card-value">1</span>', html=False)
        self.assertContains(response, f"{reverse('order_management')}?tab=accepted")

    def test_ops_viewer_sidebar_shows_order_management_and_stock_management_only(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-LIST-1",
            channel_order_id="CH-ROLE-VIEWER-LIST-1",
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
        self.assertContains(response, order.channel_order_id)
        self.assertNotContains(response, f"Shiprocket: {order.shiprocket_order_id}")
        self.assertContains(response, "Order Date:")
        self.assertContains(response, reverse("order_detail", args=[order.pk]))
        self.assertNotContains(response, 'data-row-update-form', html=False)
        self.assertContains(response, reverse("stock_management"))
        self.assertContains(response, "Stock Management")
        self.assertNotContains(response, reverse("print_queue"))
        self.assertNotContains(response, reverse("admin_utilities"))

    def test_ops_viewer_mobile_orders_screen_shows_bulk_labels_pdf_link(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Labels PDF")
        self.assertContains(response, reverse("ops_print_queue"))

    def test_ops_viewer_order_management_shows_tracking_number(self):
        Product.objects.create(
            name="Tracking Profit Soap",
            sku="TRACK-PROFIT-1",
            actual_price="40.00",
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-TRACK-LIST-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="AA123456789AA",
            shipping_address={
                "name": "Tracking Receiver",
                "phone": "9876543222",
                "address_1": "Tracking Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638004",
            },
            order_items=[
                {"name": "Tracking Profit Soap", "sku": "TRACK-PROFIT-1", "quantity": 2, "price": "90.00"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": "shipped"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tracking: AA123456789AA")
        self.assertContains(response, "Profit: Rs 100.00")

    def test_ops_viewer_cancelled_tab_keeps_cancelled_label_active(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-CANCELLED-TAB-1",
            channel_order_id="CANCELLED-TAB-1",
            local_status=ShiprocketOrder.STATUS_CANCELLED,
            customer_name="Cancelled Tab Customer",
            shipping_address={"name": "Cancelled Tab Customer", "address_1": "Cancelled Street"},
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-PENDING-TAB-1",
            channel_order_id="PENDING-TAB-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            customer_name="Pending Tab Customer",
            shipping_address={"name": "Pending Tab Customer", "address_1": "Pending Street"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": "cancelled"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected: Cancelled")
        self.assertContains(response, "Cancelled Tab Customer")
        self.assertNotContains(response, "Pending Tab Customer")
        self.assertContains(response, 'class="ops-mobile-tab is-active"', html=False)
        self.assertContains(response, "<strong>Cancelled</strong>", html=False)

    def test_ops_viewer_order_management_shows_grouped_tab_counts(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-COUNT-ACCEPTED-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Accepted Count",
            shipping_address={"name": "Accepted Count", "address_1": "Accepted Street"},
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-COUNT-PACKED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            customer_name="Packed Count",
            shipping_address={"name": "Packed Count", "address_1": "Packed Street"},
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-COUNT-SHIPPED-1",
            local_status=ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
            customer_name="Shipped Count",
            shipping_address={"name": "Shipped Count", "address_1": "Shipped Street"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": "accepted"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span>2</span>', html=False)
        self.assertContains(response, '<span>1</span>', html=False)
        self.assertContains(response, "display: inline-flex")
        self.assertContains(response, "Accepted Count")
        self.assertContains(response, "Packed Count")
        self.assertNotContains(response, "Shipped Count")

    def test_ops_viewer_order_management_renders_woocommerce_item_image(self):
        ShiprocketOrder.objects.create(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            shiprocket_order_id="WC-ROLE-VIEWER-IMAGE-1",
            channel_order_id="10025",
            local_status=ShiprocketOrder.STATUS_NEW,
            customer_name="Image Customer",
            order_items=[
                {
                    "sku": "MTHKLB01",
                    "name": "Beetroot Lipbalm 8gm",
                    "image": "https://example.com/lipbalm.jpg",
                    "price": "90",
                    "quantity": 3,
                }
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://example.com/lipbalm.jpg")

    def test_ops_viewer_order_detail_shows_stock_shortage_alert(self):
        Product.objects.create(
            name="Viewer Short Stock",
            sku="VIEWER-STOCK-1",
            stock_quantity=0,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-STOCK-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Viewer Stock",
                "phone": "9876543210",
                "address_1": "Stock Street 5",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Viewer Short Stock", "sku": "VIEWER-STOCK-1", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock short for this order.")
        self.assertContains(response, "required 2,")
        self.assertContains(response, "available 0")

    def test_ops_viewer_cannot_open_shipping_label_test_page(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("shipping_label_test_4x6"))

        self.assertRedirects(response, reverse("order_management"))

    def test_ops_viewer_can_open_ops_print_queue(self):
        packed_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-LABEL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Bulk Label Receiver",
                "phone": "9876543210",
                "address_1": "Bulk Label Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_print_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Packed Orders Label Queue")
        self.assertContains(response, "Open 4x6 Labels")
        self.assertContains(response, packed_order.shiprocket_order_id)
        self.assertContains(response, "Bulk Label Receiver")
        self.assertContains(response, reverse("ops_bulk_shipping_labels_4x6"))
        self.assertNotContains(response, "Printer Test 4x6")
        self.assertNotContains(response, ">Phone<", html=False)
        self.assertNotContains(response, ">City<", html=False)
        self.assertNotContains(response, ">Pincode<", html=False)
        self.assertNotContains(response, ">Print Count<", html=False)

    def test_ops_print_queue_shows_already_printed_orders_by_default(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PRINTED-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=1,
            shipping_address={
                "name": "Printed Bulk Receiver",
                "phone": "9876543200",
                "address_1": "Printed Bulk Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638004",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PENDING-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=0,
            shipping_address={
                "name": "Pending Bulk Receiver",
                "phone": "9876543201",
                "address_1": "Pending Bulk Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638005",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_print_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Bulk Receiver")
        self.assertContains(response, "Printed Bulk Receiver")
        self.assertContains(response, 'id="skipPrintedToggle"', html=False)
        self.assertNotContains(response, 'id="skipPrintedToggle" checked', html=False)

    def test_ops_print_queue_can_show_already_printed_orders_for_reprint(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-REPRINT-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=2,
            shipping_address={
                "name": "Reprint Bulk Receiver",
                "phone": "9876543202",
                "address_1": "Reprint Bulk Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638006",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_print_queue"), {"skip_printed": "0"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reprint Bulk Receiver")
        self.assertNotContains(response, "Pending Bulk Receiver")

    def test_ops_print_queue_skip_printed_filter_hides_reprinted_orders(self):
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PRINTED-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=2,
            shipping_address={
                "name": "Printed Filter Receiver",
                "phone": "9876543203",
                "address_1": "Printed Filter Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638008",
            },
        )
        ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PENDING-2",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=0,
            shipping_address={
                "name": "Pending Filter Receiver",
                "phone": "9876543204",
                "address_1": "Pending Filter Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638009",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_print_queue"), {"skip_printed": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Filter Receiver")
        self.assertNotContains(response, "Printed Filter Receiver")
        self.assertContains(response, 'id="skipPrintedToggle"', html=False)
        self.assertContains(response, "checked")

    def test_ops_viewer_can_open_ops_bulk_shipping_labels_page(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PDF-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Bulk Pdf Receiver",
                "phone": "9876543211",
                "address_1": "Bulk Pdf Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638002",
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
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_bulk_shipping_labels_4x6"), {"order_id": [order.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.shiprocket_order_id)
        self.assertContains(response, "Save as PDF")
        self.assertContains(response, reverse("ops_bulk_shipping_labels_pdf"))
        self.assertContains(response, reverse("ops_print_queue"))

    def test_ops_viewer_can_reopen_bulk_shipping_labels_for_printed_order(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-REOPEN-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            label_print_count=3,
            shipping_address={
                "name": "Reopen Bulk Receiver",
                "phone": "9876543213",
                "address_1": "Reopen Bulk Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638007",
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
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_bulk_shipping_labels_4x6"), {"order_id": [order.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reopen Bulk Receiver")
        self.assertContains(response, order.shiprocket_order_id)

    def test_ops_viewer_can_download_ops_bulk_shipping_labels_pdf(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-BULK-PDF-DL-1",
            channel_order_id="CH-ROLE-VIEWER-BULK-PDF-DL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            shipping_address={
                "name": "Bulk Pdf Download Receiver",
                "phone": "9876543212",
                "address_1": "Bulk Pdf Download Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638003",
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
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("ops_bulk_shipping_labels_pdf"), {"order_id": [order.pk]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)
        self.assertIn(b"CH-ROLE-VIEWER-BULK-PDF-DL-1", response.content)
        self.assertNotIn(b"ORDER CH-ROLE-VIEWER-BULK-PDF-DL-1", response.content)
        self.assertIn(b"Order CH-ROLE-VIEWER-BULK-PDF-DL-1", response.content)
        self.assertIn(b"TO", response.content)
        self.assertIn(b"FROM", response.content)
        self.assertIn(b"Pincode 638003", response.content)
        self.assertIn(b"Pincode 638001", response.content)
        self.assertNotIn(b"PIN 638003", response.content)
        self.assertNotIn(b"TO ADDRESS", response.content)
        self.assertNotIn(b"FROM ADDRESS", response.content)

    def test_ops_viewer_desktop_order_list_shows_packing_and_shipping_print_links(self):
        accepted_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DESKTOP-PACK-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Accepted Desktop",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Accepted Desktop", "address_1": "Accepted Street"},
        )
        packed_order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DESKTOP-LABEL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            customer_name="Packed Desktop",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Packed Desktop", "address_1": "Packed Street"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": "accepted"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("packing_list", args=[accepted_order.pk]))
        self.assertContains(response, reverse("packing_list", args=[packed_order.pk]))
        self.assertContains(response, reverse("shipping_label_4x6", args=[packed_order.pk]))
        self.assertContains(response, "Print Packing List")
        self.assertContains(response, "Print Shipping Label")

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
        self.assertContains(response, "Edit Delivery Details")
        self.assertContains(response, "Order Date:")
        self.assertContains(response, 'name="manual_customer_phone"', html=False)
        self.assertContains(response, 'name="manual_shipping_address_1"', html=False)
        self.assertContains(response, 'name="manual_shipping_pincode"', html=False)

    def test_ops_viewer_order_detail_shows_order_profit(self):
        Product.objects.create(
            name="Soap Bar",
            sku="DETAIL-PROFIT-1",
            actual_price="50.00",
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-PROFIT-1",
            local_status=ShiprocketOrder.STATUS_NEW,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Profit Detail",
                "phone": "9876543210",
                "address_1": "42 Demo Street",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Profit")
        self.assertContains(response, "Rs 80.00")

    def test_ops_viewer_can_update_delivery_details(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-EDIT-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            shipping_address={
                "name": "Viewer Edit",
                "phone": "9000001111",
                "address_1": "Old Street 1",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order", args=[order.pk]),
            {
                "manual_customer_phone": "9876543210",
                "manual_shipping_address_1": "99 Updated Street",
                "manual_shipping_pincode": "638009",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.manual_customer_phone, "9876543210")
        self.assertEqual(order.manual_shipping_address_1, "99 Updated Street")
        self.assertEqual(order.manual_shipping_pincode, "638009")

    def test_ops_viewer_can_update_tracking_number(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-TRACK-EDIT-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            tracking_number="AA123456789AA",
            shipping_address={
                "name": "Viewer Tracking Edit",
                "phone": "9000001111",
                "address_1": "Tracking Lane",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order_tracking", args=[order.pk]),
            {"tracking_number": "BB123456789BB", "shipping_base_amount": "120.00"},
            follow=True,
        )

        order.refresh_from_db()
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.tracking_number, "BB123456789BB")
        self.assertEqual(str(order.shipping_base_amount), "120.00")
        self.assertEqual(str(order.shipping_total_amount), "141.6000")

    def test_ops_viewer_accepted_order_detail_hides_mobile_packing_options(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-PACK-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Accepted Detail",
                "phone": "9876543210",
                "email": "accepted@example.com",
                "address_1": "44 Packing Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 1, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "accepted"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Print Packing List")
        self.assertNotContains(response, reverse("packing_list", args=[order.pk]))

    def test_ops_viewer_accepted_order_detail_shows_label_and_ship_actions_without_packing_ui(self):
        Product.objects.create(
            name="Packing Soap",
            sku="PACK-SKU-UI-1",
            barcode="8901234567890",
            stock_quantity=10,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-PACK-SCAN-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packing Detail",
                "phone": "9876543210",
                "email": "packscan@example.com",
                "address_1": "88 Packing Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Packing Soap", "sku": "PACK-SKU-UI-1", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "accepted"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Shipping Label")
        self.assertContains(response, "Ship Order")
        self.assertContains(response, reverse("shipping_label_4x6", args=[order.pk]))
        self.assertContains(response, 'id="opsCourierInput"')
        self.assertContains(response, 'value="India Post"')
        self.assertNotContains(response, "Courier Partner")
        self.assertNotContains(response, "Initial Shipment Status")
        self.assertNotContains(response, "Packing Verification")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_ops_viewer_cannot_pack_without_scanning_full_quantity(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        Product.objects.create(
            name="Packing Soap",
            sku="PACK-SKU-QTY-1",
            barcode="8901234567891",
            stock_quantity=10,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-PACK-QTY-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packing Qty",
                "phone": "9876543210",
                "address_1": "90 Packing Street",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Packing Soap", "sku": "PACK-SKU-QTY-1", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                "active_tab": "accepted",
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED,
                f"order-{order.pk}-packing_scan_payload": json.dumps(["PACK-SKU-QTY-1"]),
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertContains(response, "Invalid order status selected")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_ops_viewer_cannot_pack_with_mismatched_barcode(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        Product.objects.create(
            name="Packing Soap",
            sku="PACK-SKU-MISMATCH-1",
            barcode="8901234567892",
            stock_quantity=10,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-PACK-MISMATCH-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packing Mismatch",
                "phone": "9876543210",
                "address_1": "91 Packing Street",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Packing Soap", "sku": "PACK-SKU-MISMATCH-1", "quantity": 1, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                "active_tab": "accepted",
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_PACKED,
                f"order-{order.pk}-packing_scan_payload": json.dumps(["WRONGSKU123"]),
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_ACCEPTED)
        self.assertContains(response, "Invalid order status selected")

    @patch("core.views.enqueue_whatsapp_notification")
    def test_ops_viewer_can_ship_accepted_order_after_printing_label_flow(self, mock_enqueue_whatsapp_notification):
        mock_enqueue_whatsapp_notification.return_value = {
            "queued": False,
            "reason": "disabled",
            "job": None,
        }
        Product.objects.create(
            name="Packing Soap",
            sku="PACK-SKU-OK-1",
            barcode="8901234567893",
            stock_quantity=10,
        )
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-PACK-OK-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packing Success",
                "phone": "9876543210",
                "address_1": "92 Packing Street",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Packing Soap", "sku": "PACK-SKU-OK-1", "quantity": 2, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("update_shiprocket_order_status", args=[order.pk]),
            {
                "active_tab": "accepted",
                f"order-{order.pk}-local_status": ShiprocketOrder.STATUS_SHIPPED,
                f"order-{order.pk}-tracking_number": "AA123456789AA",
                f"order-{order.pk}-courier_name": "India Post",
                f"order-{order.pk}-shipping_base_amount": "75.00",
            },
            follow=True,
        )

        order.refresh_from_db()
        self.assertEqual(order.local_status, ShiprocketOrder.STATUS_SHIPPED)
        self.assertEqual(order.courier_name, "India Post")
        self.assertEqual(str(order.shipping_base_amount), "75.00")
        self.assertEqual(str(order.shipping_total_amount), "88.5000")
        self.assertContains(response, "Order moved to the selected tab.")

    def test_ops_viewer_order_detail_shipped_action_uses_manual_tracking_entry(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-SHIP-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packed Detail",
                "phone": "9876543210",
                "email": "packed@example.com",
                "address_1": "55 Packed Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 1, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "shipped"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ship Order")
        self.assertContains(response, "Tracking ID / AWB Number")
        self.assertContains(response, "Enter tracking ID manually")
        self.assertNotContains(response, "Scan Barcode")
        self.assertNotContains(response, "opsScannerPanel")
        self.assertNotContains(response, "Courier Partner")
        self.assertNotContains(response, "Initial Shipment Status")

    def test_ops_viewer_packed_order_detail_shows_shipping_label_option(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-LABEL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Packed Label Detail",
                "phone": "9876543210",
                "email": "packedlabel@example.com",
                "address_1": "77 Label Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638001",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 1, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "accepted"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Shipping Label")
        self.assertContains(response, reverse("shipping_label_4x6", args=[order.pk]))

    def test_ops_viewer_shipped_order_detail_hides_shipping_label_option(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-DETAIL-SHIPPED-1",
            local_status=ShiprocketOrder.STATUS_SHIPPED,
            payment_method="Cash on Delivery",
            shipping_address={
                "name": "Shipped Detail",
                "phone": "9876543210",
                "email": "shipped@example.com",
                "address_1": "88 Shipped Street",
                "city": "Erode",
                "state": "TN",
                "pincode": "638010",
            },
            order_items=[
                {"name": "Soap Bar", "quantity": 1, "price": "90"},
            ],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_detail", args=[order.pk]), {"tab": "shipped"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Print Shipping Label")
        self.assertNotContains(response, reverse("shipping_label_4x6", args=[order.pk]))

    def test_ops_viewer_can_open_packing_list(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-PACKING-LIST-1",
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Packing Viewer",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Packing Viewer", "address_1": "Packing Street"},
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("packing_list", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Packing List")
        self.assertContains(response, order.shiprocket_order_id)

    def test_ops_viewer_can_open_shipping_label(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="SR-ROLE-VIEWER-SHIPPING-LABEL-1",
            local_status=ShiprocketOrder.STATUS_PACKED,
            customer_name="Label Viewer",
            payment_method="Cash on Delivery",
            shipping_address={"name": "Label Viewer", "address_1": "Label Street"},
        )
        SenderAddress.objects.create(
            name="Warehouse Sender",
            address_1="Sender Street 5",
            city="Erode",
            state="TN",
            country="India",
            pincode="638001",
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("shipping_label_4x6", args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shipping Label")
        self.assertContains(response, order.shiprocket_order_id)

    def test_ops_viewer_can_access_stock_management_screen(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock Management")
        self.assertContains(response, "ops-stock-shell")
        self.assertContains(response, "Products")
        self.assertContains(response, "A to Z")
        self.assertContains(response, "Category")
        self.assertContains(response, "ops-stock-filter-menu")
        self.assertContains(response, "Filters")
        self.assertNotContains(response, 'select name="category"', html=False)
        self.assertNotContains(response, "WooCommerce synced inventory")
        self.assertNotContains(response, "Sync Products from WooCommerce")
        self.assertNotContains(response, "Reconcile Missing Deductions")
        self.assertNotContains(response, "Total Products")
        self.assertNotContains(response, "Search product, SKU, or barcode")
        self.assertNotContains(response, '<section class="ops-stock-hero"', html=False)
        self.assertNotContains(response, '<section class="ops-stock-summary"', html=False)
        self.assertNotContains(response, '<form method="get" action="/stock-management/" class="ops-stock-search-form">', html=False)
        self.assertNotContains(response, "Product Detail")
        self.assertNotContains(response, "Products come from WooCommerce.")
        self.assertNotContains(response, "Last sync:")
        self.assertNotContains(response, "Select a product to update stock settings.")
        self.assertNotContains(response, "Stock Actions")
        self.assertNotContains(response, "Recent Movements")
        self.assertNotContains(response, "Apply Stock Update")
        self.assertNotContains(response, "Add Product")
        self.assertNotContains(response, "Stock Qty Table")
        self.assertNotContains(response, "Review stock, mapping, and edit products quickly.")

    def test_ops_viewer_can_access_special_stock_issue_register(self):
        product = Product.objects.create(
            name="Issue Product",
            sku="ISSUE-STOCK-1",
            stock_quantity=14,
            is_active=True,
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("special_stock_issue_register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Free / Sample Issue Register")
        self.assertContains(response, "Given To")
        self.assertContains(response, reverse("special_stock_issue_register"))
        self.assertContains(response, "Free Entry")
        self.assertContains(response, 'data-stock="14"', html=False)
        self.assertContains(response, "Available Stock")

    def test_ops_viewer_can_access_expense_tracker(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("expense_tracker"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expense Tracker")
        self.assertContains(response, "Expense Done By")
        self.assertContains(response, "Purchased Item")
        self.assertContains(response, "Total Spend")
        self.assertContains(response, reverse("expense_tracker"))
        self.assertContains(response, "Expenses")

    def test_ops_viewer_can_submit_expense_entry_and_see_total_spend(self):
        expense_person = ExpensePerson.objects.create(name="Arun")
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("expense_tracker"),
            {
                "expense_person": expense_person.pk,
                "item_name": "Bubble wrap roll",
                "quantity": 3,
                "unit_price": "120.50",
                "remark": "Packaging purchase",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("expense_tracker"))
        expense = BusinessExpense.objects.get(item_name="Bubble wrap roll")
        self.assertEqual(expense.quantity, 3)
        self.assertEqual(str(expense.unit_price), "120.50")
        self.assertEqual(expense.remark, "Packaging purchase")
        self.assertEqual(expense.expense_person, expense_person)
        self.assertEqual(expense.created_by, "viewer")
        self.assertContains(response, "Saved expense for Bubble wrap roll.")
        self.assertContains(response, "Rs 361.50")
        self.assertContains(response, "Arun")

    def test_ops_viewer_can_edit_expense_entry(self):
        expense = BusinessExpense.objects.create(
            item_name="Tape Roll",
            quantity=2,
            unit_price="50.00",
            remark="Old remark",
            created_by="viewer",
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("expense_tracker"),
            {
                "expense_id": expense.pk,
                "item_name": "Tape Roll",
                "quantity": 4,
                "unit_price": "55.00",
                "remark": "Updated remark",
            },
            follow=True,
        )

        expense.refresh_from_db()
        self.assertRedirects(response, reverse("expense_tracker"))
        self.assertEqual(expense.quantity, 4)
        self.assertEqual(str(expense.unit_price), "55.00")
        self.assertEqual(expense.remark, "Updated remark")
        self.assertEqual(expense.created_by, "viewer")
        self.assertContains(response, "Updated expense for Tape Roll.")
        self.assertContains(response, "Rs 220.00")

    def test_ops_viewer_can_submit_special_stock_issue(self):
        product = Product.objects.create(
            name="Issue Product",
            sku="ISSUE-1",
            stock_quantity=12,
            is_active=True,
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("special_stock_issue_register"),
            {
                "product": product.pk,
                "issue_category": StockMovement.ISSUE_CATEGORY_SAMPLE,
                "quantity": 3,
                "issue_recipient": "Demo Customer",
                "notes": "Festival sample pack",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("special_stock_issue_register"))
        product.refresh_from_db()
        movement = StockMovement.objects.filter(
            product=product,
            movement_type=StockMovement.TYPE_SPECIAL_ISSUE,
        ).latest("pk")
        self.assertEqual(product.stock_quantity, 9)
        self.assertEqual(movement.quantity_delta, -3)
        self.assertEqual(movement.issue_category, StockMovement.ISSUE_CATEGORY_SAMPLE)
        self.assertEqual(movement.issue_recipient, "Demo Customer")
        self.assertEqual(movement.notes, "Festival sample pack")
        self.assertContains(response, "Issued 3 unit(s) of Issue Product (ISSUE-1) as sample stock to Demo Customer.")

    def test_ops_viewer_stock_cards_show_requested_product_fields(self):
        product = Product.objects.create(
            name="Column Order Product",
            sku="COLUMN-ORDER-1",
            category="Soap",
            stock_quantity=9,
            reorder_level=2,
            is_active=True,
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Column Order Product")
        self.assertContains(response, "SKU: COLUMN-ORDER-1")
        self.assertContains(response, "9 in stock")
        self.assertContains(response, "Soap")
        self.assertContains(response, reverse("stock_product_detail", args=[product.pk]))
        self.assertNotContains(response, "In Stock")
        self.assertNotContains(response, "Current")
        self.assertNotContains(response, "Threshold")

    def test_ops_viewer_can_open_stock_product_detail_screen(self):
        product = Product.objects.create(
            name="24K Gold Serum",
            sku="MO-SER-001",
            category="Serums",
            stock_quantity=4,
            reorder_level=2,
            smartbiz_product_id="101",
            image_url="https://shop.example.com/serum.jpg",
            actual_price="320.00",
            regular_price="300.00",
            sale_price="240.00",
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_product_detail", args=[product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "24K Gold Serum")
        self.assertContains(response, "Description")
        self.assertContains(response, "Product image")
        self.assertContains(response, "Price")
        self.assertContains(response, "Actual price: Rs 320.00")
        self.assertContains(response, "Regular price: Rs 300.00")
        self.assertContains(response, "Sale price: Rs 240.00")
        self.assertContains(response, "Inventory")
        self.assertContains(response, "SKU: MO-SER-001")
        self.assertContains(response, "Stock quantity: 4")
        self.assertContains(response, "Backorders: Do not allow")
        self.assertContains(response, "Categories")
        self.assertContains(response, "Serums")
        self.assertContains(response, reverse("stock_product_section", args=[product.pk, "description"]))
        self.assertContains(response, reverse("stock_product_section", args=[product.pk, "images"]))
        self.assertContains(response, reverse("stock_product_section", args=[product.pk, "price"]))
        self.assertContains(response, reverse("stock_product_section", args=[product.pk, "inventory"]))
        self.assertContains(response, reverse("stock_product_section", args=[product.pk, "categories"]))
        self.assertNotContains(response, "Promote with Blaze")
        self.assertNotContains(response, "Reviews")
        self.assertNotContains(response, "Product Type")
        self.assertNotContains(response, "Save and Update WooCommerce")

    def test_ops_viewer_can_open_stock_product_section_screens(self):
        category = ProductCategory.objects.create(name="Serums")
        product = Product.objects.create(
            name="24K Gold Serum",
            sku="MO-SER-001",
            category_master=category,
            stock_quantity=4,
            reorder_level=2,
            smartbiz_product_id="101",
            image_url="https://shop.example.com/serum.jpg",
            description="<p>1.Reduces Dark spots &amp; pigmentation<br />2.Promotes even skin tone &amp; glow</p>",
            actual_price="320.00",
            regular_price="300.00",
            sale_price="240.00",
        )
        self.client.force_login(self.viewer)

        description_response = self.client.get(reverse("stock_product_section", args=[product.pk, "description"]))
        image_response = self.client.get(reverse("stock_product_section", args=[product.pk, "images"]))
        price_response = self.client.get(reverse("stock_product_section", args=[product.pk, "price"]))
        inventory_response = self.client.get(reverse("stock_product_section", args=[product.pk, "inventory"]))
        categories_response = self.client.get(reverse("stock_product_section", args=[product.pk, "categories"]))

        self.assertEqual(description_response.status_code, 200)
        self.assertContains(description_response, "Description")
        self.assertContains(description_response, "1.Reduces Dark spots &amp; pigmentation")
        self.assertContains(description_response, "2.Promotes even skin tone &amp; glow")
        self.assertNotContains(description_response, "&lt;p&gt;")
        self.assertNotContains(description_response, "&lt;br /&gt;")
        self.assertEqual(image_response.status_code, 200)
        self.assertContains(image_response, "Photos")
        self.assertContains(image_response, "Add photos")
        self.assertContains(image_response, 'type="file"', html=False)
        self.assertContains(image_response, 'accept="image/*"', html=False)
        self.assertContains(image_response, "https://shop.example.com/serum.jpg")
        self.assertEqual(price_response.status_code, 200)
        self.assertContains(price_response, "Actual price")
        self.assertContains(price_response, "Regular price")
        self.assertContains(price_response, "Sale price")
        self.assertContains(price_response, 'value="320.00"', html=False)
        self.assertContains(price_response, 'value="300.00"', html=False)
        self.assertContains(price_response, 'value="240.00"', html=False)
        self.assertEqual(inventory_response.status_code, 200)
        self.assertContains(inventory_response, "MO-SER-001")
        self.assertContains(inventory_response, "Manage stock")
        self.assertEqual(categories_response.status_code, 200)
        self.assertContains(categories_response, "Add category")
        self.assertContains(categories_response, "Serums")

    @patch("core.views.update_woocommerce_product")
    def test_ops_viewer_product_detail_updates_local_product_and_woocommerce(self, mock_update_product):
        product = Product.objects.create(
            name="24K Gold Serum",
            sku="MO-SER-001",
            category="Serums",
            stock_quantity=4,
            reorder_level=2,
            smartbiz_product_id="101",
            is_active=True,
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("stock_product_detail", args=[product.pk]),
            {
                "name": "24K Gold Serum Plus",
                "category_master": "",
                "sku": "MO-SER-001",
                "smartbiz_product_id": "101",
                "barcode": "",
                "image_url": "https://shop.example.com/serum-plus.jpg",
                "stock_quantity": "6",
                "reorder_level": "3",
                "is_active": "on",
                "description": "Reduces fine lines and wrinkles.",
                "actual_price": "320.00",
                "regular_price": "300.00",
                "sale_price": "240.00",
            },
            follow=True,
        )

        product.refresh_from_db()
        self.assertEqual(product.name, "24K Gold Serum Plus")
        self.assertEqual(product.stock_quantity, 6)
        self.assertEqual(product.reorder_level, 3)
        self.assertEqual(product.image_url, "https://shop.example.com/serum-plus.jpg")
        self.assertEqual(str(product.actual_price), "320.00")
        mock_update_product.assert_called_once()
        self.assertEqual(mock_update_product.call_args.args[0], product)
        self.assertEqual(
            mock_update_product.call_args.kwargs["extra_fields"]["description"],
            "Reduces fine lines and wrinkles.",
        )
        self.assertContains(response, "Updated 24K Gold Serum Plus locally and in WooCommerce.")

    @patch("core.views.update_woocommerce_product")
    def test_ops_viewer_product_section_screens_update_product_and_woocommerce(self, mock_update_product):
        serums = ProductCategory.objects.create(name="Serums")
        hair_care = ProductCategory.objects.create(name="Hair Care")
        product = Product.objects.create(
            name="24K Gold Serum",
            sku="MO-SER-001",
            category_master=serums,
            stock_quantity=4,
            reorder_level=2,
            smartbiz_product_id="101",
            actual_price="320.00",
            regular_price="300.00",
            sale_price="240.00",
            is_active=True,
        )
        self.client.force_login(self.viewer)

        description_response = self.client.post(
            reverse("stock_product_section", args=[product.pk, "description"]),
            {"description": "Reduces fine lines and wrinkles."},
            follow=True,
        )
        price_response = self.client.post(
            reverse("stock_product_section", args=[product.pk, "price"]),
            {"actual_price": "360.00", "regular_price": "350.00", "sale_price": "280.00"},
            follow=True,
        )
        image_response = self.client.post(
            reverse("stock_product_section", args=[product.pk, "images"]),
            {"image_url": "https://shop.example.com/images/serum-updated.jpg"},
            follow=True,
        )
        inventory_response = self.client.post(
            reverse("stock_product_section", args=[product.pk, "inventory"]),
            {"sku": "MO-SER-001", "barcode": "890001", "stock_quantity": "8"},
            follow=True,
        )
        category_response = self.client.post(
            reverse("stock_product_section", args=[product.pk, "categories"]),
            {"category_master": str(hair_care.pk)},
            follow=True,
        )

        product.refresh_from_db()
        self.assertEqual(product.description, "Reduces fine lines and wrinkles.")
        self.assertEqual(str(product.actual_price), "360.00")
        self.assertEqual(str(product.regular_price), "350.00")
        self.assertEqual(str(product.sale_price), "280.00")
        self.assertEqual(product.image_url, "https://shop.example.com/images/serum-updated.jpg")
        self.assertEqual(product.stock_quantity, 8)
        self.assertEqual(product.barcode, "890001")
        self.assertEqual(product.category_master, hair_care)
        self.assertEqual(mock_update_product.call_count, 5)
        self.assertContains(description_response, "Updated 24K Gold Serum locally and in WooCommerce.")
        self.assertContains(price_response, "Updated 24K Gold Serum locally and in WooCommerce.")
        self.assertContains(image_response, "Updated 24K Gold Serum locally and in WooCommerce.")
        self.assertContains(inventory_response, "Updated 24K Gold Serum locally and in WooCommerce.")
        self.assertContains(category_response, "Updated 24K Gold Serum locally and in WooCommerce.")

        detail_response = self.client.get(reverse("stock_product_detail", args=[product.pk]))
        self.assertContains(detail_response, "Actual price: Rs 360.00")
        self.assertContains(detail_response, "Regular price: Rs 350.00")
        self.assertContains(detail_response, "Sale price: Rs 280.00")
        self.assertContains(detail_response, "https://shop.example.com/images/serum-updated.jpg")
        self.assertContains(detail_response, "Stock quantity: 8")
        self.assertContains(detail_response, "Hair Care")

    @patch("core.views.update_woocommerce_product")
    def test_ops_viewer_can_upload_product_image_from_device(self, mock_update_product):
        product = Product.objects.create(
            name="Photo Upload Serum",
            sku="PHOTO-UPLOAD-1",
            stock_quantity=4,
            smartbiz_product_id="101",
            is_active=True,
        )
        self.client.force_login(self.viewer)
        upload = SimpleUploadedFile(
            "serum.png",
            b"\x89PNG\r\n\x1a\n",
            content_type="image/png",
        )

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(
                ALLOWED_HOSTS=["mathukai.example.com"],
                MEDIA_ROOT=Path(media_root),
                MEDIA_URL="/media/",
            ):
                response = self.client.post(
                    reverse("stock_product_section", args=[product.pk, "images"]),
                    {"product_image": upload, "image_url": ""},
                    follow=True,
                    **{"HTTP_HOST": "mathukai.example.com"},
                )

        product.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIn("/media/product-images/", product.image_url)
        self.assertTrue(product.image_url.startswith("http://mathukai.example.com/"))
        mock_update_product.assert_called_once()
        self.assertContains(response, "Updated Photo Upload Serum locally and in WooCommerce.")

    def test_ops_viewer_stock_qty_table_is_grouped_by_category(self):
        drink = ProductCategory.objects.create(name="Drink")
        soap = ProductCategory.objects.create(name="Soap")
        Product.objects.create(
            name="Z Soap Product",
            category_master=soap,
            sku="SOAP-ORDER-1",
            stock_quantity=5,
            is_active=True,
        )
        Product.objects.create(
            name="A Drink Product",
            category_master=drink,
            sku="DRINK-ORDER-1",
            stock_quantity=7,
            is_active=True,
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("Drink"), content.index("Soap"))
        self.assertLess(content.index("A Drink Product"), content.index("Z Soap Product"))

    def test_ops_viewer_manage_stock_tab_shows_management_tools(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"), {"view": "manage"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Reconcile Missing Deductions")
        self.assertNotContains(response, "Sync Products from WooCommerce")
        self.assertNotContains(response, "Stock Actions")
        self.assertNotContains(response, "Apply Stock Update")

    def test_ops_viewer_stock_dashboard_shows_products_without_recent_movements(self):
        Product.objects.create(
            name="More Tab Product",
            sku="MORE-TAB-1",
            stock_quantity=6,
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("stock_management"), {"view": "more"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "More Tab Product")
        self.assertNotContains(response, "Recent Movements")
        self.assertNotContains(response, "History")

    def test_ops_viewer_order_management_uses_billing_address_fallback(self):
        order = ShiprocketOrder.objects.create(
            shiprocket_order_id="WC-BILLING-ADDRESS-1",
            channel_order_id="346",
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
            local_status=ShiprocketOrder.STATUS_ACCEPTED,
            customer_name="Ramachandran",
            customer_phone="+919952975768",
            shipping_address={"name": "Ramachandran", "phone": "+919952975768", "address_1": ""},
            billing_address={
                "address_1": "No 38 5th Street jeevan Adambakkam",
                "city": "Chennai",
                "pincode": "600088",
            },
            order_items=[{"name": "Lip Serum", "sku": "MO-SER-007", "quantity": 1, "price": "130"}],
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("order_management"), {"tab": ShiprocketOrder.STATUS_ACCEPTED})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.channel_order_id)
        self.assertContains(response, "No 38 5th Street jeevan Adambakkam")
        self.assertContains(response, "Chennai")
        self.assertContains(response, "600088")
        self.assertNotContains(response, "Address not available")

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

    def test_ops_viewer_can_set_stock_qty_from_stock_table(self):
        product = Product.objects.create(
            name="Viewer Table Product",
            sku="VIEW-TABLE-1",
            stock_quantity=5,
        )
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("stock_management"),
            {
                "form_action": "adjust_stock",
                "return_view": "list",
                "return_query": "view=list",
                "lookup_value": "VIEW-TABLE-1",
                "action": StockAdjustmentForm.ACTION_SET,
                "quantity": 11,
                "notes": "Updated from stock table",
            },
            follow=True,
        )

        self.assertRedirects(response, f"{reverse('stock_management')}?view=list")
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 11)
        self.assertContains(response, "Set stock for Viewer Table Product (VIEW-TABLE-1) to 11.")

    def test_admin_sidebar_shows_full_navigation(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("order_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("stock_management"))
        self.assertContains(response, reverse("expense_tracker"))
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

    @patch("core.views.sync_woocommerce_orders")
    def test_ops_viewer_can_run_woocommerce_sync_from_order_management(self, mock_sync_orders):
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
        self.assertContains(response, "Order sync completed. WooCommerce: 3 orders refreshed.")

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

    @patch("core.views.sync_woocommerce_products")
    def test_stock_management_can_sync_products_from_woocommerce(self, mock_sync_products):
        mock_sync_products.return_value = {
            "products_seen": 2,
            "variations_seen": 1,
            "created": 2,
            "updated": 1,
            "unchanged": 0,
            "skipped": 0,
        }

        response = self.client.post(
            reverse("stock_management"),
            {"form_action": "sync_woocommerce_products"},
            follow=True,
        )

        self.assertRedirects(response, reverse("stock_management"))
        mock_sync_products.assert_called_once()
        self.assertContains(response, "WooCommerce products synced. Created 2, updated 1")
        self.assertContains(response, "Included 1 WooCommerce variation")

    def test_stock_management_shows_category_in_product_table(self):
        Product.objects.create(
            name="Goat Milk Soap",
            category="Soap",
            sku="SKU-CATEGORY-1",
            image_url="https://shop.example.com/images/goat-milk-soap.jpg",
            stock_quantity=8,
        )

        response = self.client.get(reverse("stock_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Category")
        self.assertContains(response, "Soap")
        self.assertContains(response, 'src="https://shop.example.com/images/goat-milk-soap.jpg"', html=False)

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


class PwaEndpointTests(TestCase):
    def test_manifest_webmanifest_exposes_install_metadata(self):
        response = self.client.get(reverse("pwa_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response["Content-Type"])
        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["display"], "standalone")
        self.assertEqual(payload["start_url"], reverse("home"))
        self.assertEqual(payload["short_name"], "Mathukai")
        self.assertGreaterEqual(len(payload["icons"]), 3)

    def test_service_worker_is_served_from_root_scope(self):
        response = self.client.get(reverse("service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, reverse("offline_page"))

    def test_base_template_includes_manifest_and_pwa_bootstrap(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, reverse("pwa_manifest"))
        self.assertContains(response, "pwa-register.js")


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
