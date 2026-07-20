from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from core.access import (
    can_access_tenant,
    can_manage_stock,
    can_operate_any_vendor,
    can_update_order_status,
    has_tenant_role,
    is_vendor_user,
    is_warehouse_operator,
)
from core.models import Tenant, TenantMembership


class WarehouseOperatorRoleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="warehouse-user")
        self.tenant = Tenant.objects.create(name="Warehouse Tenant", slug="warehouse-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.membership = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.ROLE_WAREHOUSE_OPERATOR,
        )

    def test_role_is_tenant_scoped(self):
        self.assertTrue(is_warehouse_operator(self.user, self.tenant))
        self.assertTrue(can_access_tenant(self.user, self.tenant))
        self.assertTrue(
            has_tenant_role(
                self.user,
                self.tenant,
                TenantMembership.ROLE_WAREHOUSE_OPERATOR,
            )
        )
        self.assertFalse(is_warehouse_operator(self.user, self.other_tenant))
        self.assertFalse(can_access_tenant(self.user, self.other_tenant))

    def test_role_does_not_inherit_legacy_vendor_or_global_access(self):
        self.assertFalse(is_vendor_user(self.user))
        self.assertFalse(can_operate_any_vendor(self.user))
        self.assertFalse(can_update_order_status(self.user))
        self.assertFalse(can_manage_stock(self.user))
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(Group.objects.filter(name="warehouse_operator").exists())

    def test_inactive_membership_grants_no_tenant_access(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        self.assertFalse(is_warehouse_operator(self.user, self.tenant))
        self.assertFalse(can_access_tenant(self.user, self.tenant))

    def test_existing_vendor_role_values_are_unchanged(self):
        self.assertEqual(TenantMembership.ROLE_VENDOR_OWNER, "vendor_owner")
        self.assertEqual(TenantMembership.ROLE_VENDOR_OPERATOR, "vendor_operator")
        self.assertEqual(TenantMembership.ROLE_VENDOR_VIEWER, "vendor_viewer")


class WarehouseRoleBootstrapTests(TestCase):
    def test_bootstrap_dry_run_does_not_create_global_warehouse_group(self):
        Group.objects.filter(name__in=["admin", "ops_viewer", "warehouse_operator"]).delete()
        stdout = StringIO()

        call_command("bootstrap_roles", "--dry-run", stdout=stdout)

        self.assertFalse(Group.objects.filter(name__in=["admin", "ops_viewer"]).exists())
        self.assertFalse(Group.objects.filter(name="warehouse_operator").exists())
        self.assertIn("warehouse_operator remains tenant-membership-only", stdout.getvalue())
