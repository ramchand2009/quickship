from io import StringIO
import hashlib
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

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
from core.api.v1.permissions import HasActiveMobileTenant, HasMobileTenantRole
from core.api.v1.token_services import hash_refresh_token, persist_refresh_token
from core.api.v1.token_services import (
    ACCESS_TOKEN_ALGORITHM,
    InvalidAccessToken,
    InvalidRefreshToken,
    RefreshTokenReuseDetected,
    decode_access_token,
    issue_refresh_token,
    issue_token_pair,
    issue_access_token,
    rotate_refresh_token,
)
from core.models import MobileRefreshToken, MobileSession, Tenant, TenantMembership


class MobileContextProbeView(APIView):
    permission_classes = [HasActiveMobileTenant]

    def get(self, request):
        return Response(
            {
                "data": {
                    "user_id": request.user.pk,
                    "session_id": str(request.mobile_session.pk),
                    "tenant_id": request.tenant.pk,
                    "role": request.tenant_membership.role,
                }
            }
        )


class WarehouseOnlyProbeView(MobileContextProbeView):
    permission_classes = [HasMobileTenantRole]
    mobile_allowed_roles = [TenantMembership.ROLE_WAREHOUSE_OPERATOR]


urlpatterns = [
    path("api/v1/auth-context/", MobileContextProbeView.as_view()),
    path("api/v1/warehouse-context/", WarehouseOnlyProbeView.as_view()),
]


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


class MobileAccessTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="access-token-user")
        self.tenant = Tenant.objects.create(name="Access Tenant", slug="access-tenant")
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )

    def test_access_token_has_required_scoped_claims(self):
        encoded, expires_at = issue_access_token(self.session)

        payload = decode_access_token(encoded)

        self.assertEqual(payload["sub"], str(self.user.pk))
        self.assertEqual(payload["sid"], str(self.session.pk))
        self.assertEqual(payload["tenant_id"], self.tenant.pk)
        self.assertEqual(payload["token_type"], "access")
        self.assertEqual(payload["iss"], settings.MOBILE_ACCESS_TOKEN_ISSUER)
        self.assertEqual(payload["aud"], settings.MOBILE_ACCESS_TOKEN_AUDIENCE)
        self.assertLessEqual(expires_at, self.session.expires_at)

    def test_access_token_expiry_is_capped_by_session(self):
        now = timezone.now()
        self.session.expires_at = now + timedelta(seconds=60)
        self.session.save(update_fields=["expires_at"])

        _, expires_at = issue_access_token(self.session, now=now)

        self.assertEqual(expires_at, self.session.expires_at)

    def test_expired_or_wrongly_signed_tokens_are_rejected(self):
        now = timezone.now()
        base_payload = {
            "iss": settings.MOBILE_ACCESS_TOKEN_ISSUER,
            "aud": settings.MOBILE_ACCESS_TOKEN_AUDIENCE,
            "iat": now - timedelta(minutes=20),
            "nbf": now - timedelta(minutes=20),
            "exp": now - timedelta(minutes=10),
            "jti": str(uuid.uuid4()),
            "sub": str(self.user.pk),
            "sid": str(self.session.pk),
            "tenant_id": self.tenant.pk,
            "token_type": "access",
        }
        expired = jwt.encode(
            base_payload,
            settings.MOBILE_JWT_SIGNING_KEY,
            algorithm=ACCESS_TOKEN_ALGORITHM,
        )
        wrongly_signed = jwt.encode(
            {**base_payload, "exp": now + timedelta(minutes=10)},
            "wrong-key-that-is-at-least-32-bytes-long",
            algorithm=ACCESS_TOKEN_ALGORITHM,
        )

        for encoded in (expired, wrongly_signed):
            with self.subTest(encoded=encoded[:16]), self.assertRaises(InvalidAccessToken):
                decode_access_token(encoded)

    def test_wrong_issuer_audience_missing_claim_and_token_type_are_rejected(self):
        encoded, _ = issue_access_token(self.session)
        payload = jwt.decode(
            encoded,
            settings.MOBILE_JWT_SIGNING_KEY,
            algorithms=[ACCESS_TOKEN_ALGORITHM],
            options={"verify_signature": True, "verify_aud": False},
        )
        invalid_payloads = [
            {**payload, "iss": "wrong-issuer"},
            {**payload, "aud": "wrong-audience"},
            {key: value for key, value in payload.items() if key != "sid"},
            {**payload, "token_type": "refresh"},
        ]

        for invalid_payload in invalid_payloads:
            encoded_invalid = jwt.encode(
                invalid_payload,
                settings.MOBILE_JWT_SIGNING_KEY,
                algorithm=ACCESS_TOKEN_ALGORITHM,
            )
            with self.subTest(payload=invalid_payload), self.assertRaises(InvalidAccessToken):
                decode_access_token(encoded_invalid)


class MobileRefreshRotationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rotation-user")
        self.tenant = Tenant.objects.create(name="Rotation Tenant", slug="rotation-tenant")
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )

    def test_issued_pair_returns_raw_secret_once_and_persists_only_hash(self):
        pair = issue_token_pair(self.session)
        stored = self.session.refresh_tokens.get()

        self.assertGreaterEqual(len(pair["refresh_token"]), 43)
        self.assertEqual(stored.token_hash, hash_refresh_token(pair["refresh_token"]))
        self.assertNotEqual(stored.token_hash, pair["refresh_token"])
        self.assertEqual(decode_access_token(pair["access_token"])["sid"], str(self.session.pk))

    def test_rotation_consumes_parent_and_creates_one_child(self):
        raw_token, parent = issue_refresh_token(self.session)

        pair = rotate_refresh_token(
            raw_token=raw_token,
            installation_id=self.session.installation_id,
        )

        parent.refresh_from_db()
        child = parent.children.get()
        self.assertIsNotNone(parent.consumed_at)
        self.assertEqual(child.token_hash, hash_refresh_token(pair["refresh_token"]))
        self.assertIsNone(child.consumed_at)
        self.assertEqual(self.session.refresh_tokens.count(), 2)

    def test_competing_reuse_revokes_session_and_entire_family(self):
        raw_token, parent = issue_refresh_token(self.session)
        rotate_refresh_token(
            raw_token=raw_token,
            installation_id=self.session.installation_id,
        )

        with self.assertRaises(RefreshTokenReuseDetected):
            rotate_refresh_token(
                raw_token=raw_token,
                installation_id=self.session.installation_id,
            )

        self.session.refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(self.session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(self.session.revocation_reason, "refresh_token_reuse")
        self.assertEqual(self.session.refresh_tokens.filter(revoked_at__isnull=False).count(), 2)

    def test_wrong_installation_does_not_consume_token(self):
        raw_token, token = issue_refresh_token(self.session)

        with self.assertRaises(InvalidRefreshToken):
            rotate_refresh_token(raw_token=raw_token, installation_id=uuid.uuid4())

        token.refresh_from_db()
        self.session.refresh_from_db()
        self.assertIsNone(token.consumed_at)
        self.assertEqual(self.session.status, MobileSession.STATUS_ACTIVE)

    def test_expired_token_is_rejected(self):
        raw_token, token = issue_refresh_token(self.session)
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=["expires_at"])

        with self.assertRaises(InvalidRefreshToken):
            rotate_refresh_token(
                raw_token=raw_token,
                installation_id=self.session.installation_id,
            )

    def test_removed_membership_revokes_session(self):
        raw_token, _ = issue_refresh_token(self.session)
        TenantMembership.objects.filter(user=self.user, tenant=self.tenant).update(is_active=False)

        with self.assertRaises(InvalidRefreshToken):
            rotate_refresh_token(
                raw_token=raw_token,
                installation_id=self.session.installation_id,
            )

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(self.session.revocation_reason, "session_ineligible")


@override_settings(ROOT_URLCONF=__name__)
class MobileAuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="authenticated-mobile-user")
        self.tenant = Tenant.objects.create(name="Authenticated Tenant", slug="authenticated-tenant")
        self.other_tenant = Tenant.objects.create(name="Unselected Tenant", slug="unselected-tenant")
        self.membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OPERATOR,
        )
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        self.access_token, _ = issue_access_token(self.session)

    def get(self, path="/api/v1/auth-context/", token=None):
        return self.client.get(
            path,
            headers={"Authorization": f"Bearer {token or self.access_token}"},
        )

    def test_valid_token_exposes_live_session_tenant_and_role(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            {
                "user_id": self.user.pk,
                "session_id": str(self.session.pk),
                "tenant_id": self.tenant.pk,
                "role": TenantMembership.ROLE_VENDOR_OPERATOR,
            },
        )

    def test_disabled_user_revokes_session(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.get()

        self.assertEqual(response.status_code, 401)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(self.session.revocation_reason, "user_inactive")

    def test_expired_database_session_is_rejected_and_revoked(self):
        self.session.expires_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=["expires_at"])

        response = self.get()

        self.assertEqual(response.status_code, 401)
        self.session.refresh_from_db()
        self.assertEqual(self.session.revocation_reason, "session_expired")

    def test_removed_membership_is_rejected_and_revoked(self):
        self.membership.delete()

        response = self.get()

        self.assertEqual(response.status_code, 401)
        self.session.refresh_from_db()
        self.assertEqual(self.session.revocation_reason, "membership_removed")

    def test_token_for_different_tenant_context_is_rejected(self):
        payload = decode_access_token(self.access_token)
        payload["tenant_id"] = self.other_tenant.pk
        mismatched = jwt.encode(
            payload,
            settings.MOBILE_JWT_SIGNING_KEY,
            algorithm=ACCESS_TOKEN_ALGORITHM,
        )

        response = self.get(token=mismatched)

        self.assertEqual(response.status_code, 401)

    def test_warehouse_role_is_allowed_only_for_its_selected_tenant(self):
        warehouse_user = get_user_model().objects.create_user(username="warehouse-mobile-auth")
        TenantMembership.objects.create(
            user=warehouse_user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_WAREHOUSE_OPERATOR,
        )
        warehouse_session = create_mobile_session(
            user=warehouse_user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
            active_tenant=self.tenant,
        )
        warehouse_token, _ = issue_access_token(warehouse_session)

        allowed = self.get("/api/v1/warehouse-context/", warehouse_token)
        vendor_denied = self.get("/api/v1/warehouse-context/")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["data"]["role"], TenantMembership.ROLE_WAREHOUSE_OPERATOR)
        self.assertEqual(vendor_denied.status_code, 403)
