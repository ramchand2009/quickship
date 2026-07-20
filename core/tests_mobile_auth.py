from io import StringIO
import hashlib
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.access import (
    can_access_tenant,
    can_manage_stock,
    can_operate_any_vendor,
    can_update_order_status,
    has_tenant_role,
    is_vendor_user,
    is_warehouse_operator,
)
from core.api.v1.session_services import (
    InvalidActiveTenant,
    create_mobile_session,
    select_session_tenant,
)
from core.api.v1.token_services import hash_refresh_token, persist_refresh_token
from core.models import MobileRefreshToken, MobileSession, Tenant, TenantMembership


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


class MobileSessionPersistenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mobile-session-user")
        self.other_user = get_user_model().objects.create_user(username="other-mobile-user")
        self.tenant = Tenant.objects.create(name="Session Tenant", slug="session-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Session Tenant", slug="other-session-tenant")
        self.membership = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )

    def test_service_creates_android_session_for_active_membership(self):
        installation_id = uuid.uuid4()

        session = create_mobile_session(
            user=self.user,
            installation_id=installation_id,
            app_version="1.0.0",
            active_tenant=self.tenant,
        )

        self.assertEqual(session.platform, MobileSession.PLATFORM_ANDROID)
        self.assertEqual(session.status, MobileSession.STATUS_ACTIVE)
        self.assertEqual(session.active_tenant, self.tenant)
        self.assertEqual(session.installation_id, installation_id)
        self.assertGreater(session.expires_at, timezone.now())
        self.assertEqual(str(session), f"MobileSession {session.pk}")

    def test_user_and_installation_are_unique(self):
        installation_id = uuid.uuid4()
        create_mobile_session(
            user=self.user,
            installation_id=installation_id,
            app_version="1.0.0",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            create_mobile_session(
                user=self.user,
                installation_id=installation_id,
                app_version="1.0.1",
            )

        other_session = create_mobile_session(
            user=self.other_user,
            installation_id=installation_id,
            app_version="1.0.0",
        )
        self.assertEqual(other_session.user, self.other_user)

    def test_service_rejects_cross_tenant_or_inactive_membership(self):
        with self.assertRaises(InvalidActiveTenant):
            create_mobile_session(
                user=self.user,
                installation_id=uuid.uuid4(),
                app_version="1.0.0",
                active_tenant=self.other_tenant,
            )

        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        with self.assertRaises(InvalidActiveTenant):
            create_mobile_session(
                user=self.user,
                installation_id=uuid.uuid4(),
                app_version="1.0.0",
                active_tenant=self.tenant,
            )

    def test_tenant_selection_revalidates_membership(self):
        session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
        )

        selected = select_session_tenant(session=session, tenant=self.tenant)
        self.assertEqual(selected.active_tenant, self.tenant)

        with self.assertRaises(InvalidActiveTenant):
            select_session_tenant(session=session, tenant=self.other_tenant)


class MobileRefreshTokenPersistenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="refresh-user")
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
        )

    def test_raw_refresh_token_is_hashed_before_persistence(self):
        raw_token = "raw-refresh-secret-that-must-not-be-stored"

        token = persist_refresh_token(session=self.session, raw_token=raw_token)
        token.refresh_from_db()

        self.assertEqual(token.token_hash, hash_refresh_token(raw_token))
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertNotIn(raw_token, str(token))
        self.assertNotIn(raw_token, repr(token))
        self.assertNotIn("raw_token", {field.name for field in token._meta.get_fields()})

    def test_hash_is_deterministic_and_keyed(self):
        first = hash_refresh_token("same-token")
        second = hash_refresh_token("same-token")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, hashlib.sha256(b"same-token").hexdigest())

    def test_token_hash_is_unique(self):
        persist_refresh_token(session=self.session, raw_token="duplicate-token")

        with self.assertRaises(IntegrityError), transaction.atomic():
            persist_refresh_token(session=self.session, raw_token="duplicate-token")

    def test_rotation_parent_keeps_lineage_without_raw_secret(self):
        parent = persist_refresh_token(session=self.session, raw_token="parent-token")
        child = persist_refresh_token(
            session=self.session,
            raw_token="child-token",
            parent=parent,
        )

        self.assertEqual(child.parent, parent)
        self.assertEqual(list(parent.children.all()), [child])
        self.assertGreater(child.expires_at, timezone.now())
