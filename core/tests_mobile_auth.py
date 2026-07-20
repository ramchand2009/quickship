from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import hashlib
import json
import threading
import uuid
from unittest.mock import patch

import jwt
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.checks import Tags, run_checks
from django.core.cache import cache
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
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
from core.api.v1.cleanup import cleanup_mobile_auth
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

    @override_settings(
        DEBUG=False,
        SECRET_KEY="django-secret-that-is-long-and-distinct-123456789",
        MOBILE_JWT_SIGNING_KEY_EXPLICIT=False,
        MOBILE_JWT_SIGNING_KEY="django-secret-that-is-long-and-distinct-123456789",
        MOBILE_REFRESH_TOKEN_HASH_KEY="change-me",
    )
    def test_deployment_check_rejects_missing_or_coupled_mobile_secrets(self):
        errors = run_checks(tags=[Tags.security], include_deployment_checks=True)

        mobile_errors = [error for error in errors if error.id.startswith("core.E")]
        self.assertGreaterEqual(len(mobile_errors), 3)

    @override_settings(
        DEBUG=False,
        SECRET_KEY="django-secret-that-is-long-and-distinct-123456789",
        MOBILE_JWT_SIGNING_KEY_EXPLICIT=True,
        MOBILE_JWT_SIGNING_KEY="mobile-jwt-secret-that-is-long-and-distinct-123456789",
        MOBILE_REFRESH_TOKEN_HASH_KEY="mobile-refresh-hash-secret-long-and-distinct-123456789",
    )
    def test_deployment_check_accepts_separated_mobile_secrets(self):
        errors = run_checks(tags=[Tags.security], include_deployment_checks=True)

        self.assertFalse([error for error in errors if error.id.startswith("core.E")])


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


@skipUnlessDBFeature("has_select_for_update")
class MobileRefreshConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="concurrent-rotation-user")
        self.tenant = Tenant.objects.order_by("pk").first()
        if self.tenant is None:
            self.tenant = Tenant.objects.create(
                name="Concurrent Tenant",
                slug="concurrent-tenant",
            )
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

    def test_same_refresh_token_has_one_rotation_winner_and_reuse_revokes_family(self):
        raw_token, _ = issue_refresh_token(self.session)
        start = threading.Barrier(3)

        def rotate_once():
            close_old_connections()
            try:
                start.wait(timeout=10)
                rotate_refresh_token(
                    raw_token=raw_token,
                    installation_id=self.session.installation_id,
                )
                return "rotated"
            except RefreshTokenReuseDetected:
                return "reuse_detected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(rotate_once) for _ in range(2)]
            start.wait(timeout=10)
            outcomes = sorted(future.result(timeout=15) for future in futures)

        self.assertEqual(outcomes, ["reuse_detected", "rotated"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(self.session.revocation_reason, "refresh_token_reuse")
        self.assertFalse(self.session.refresh_tokens.filter(revoked_at__isnull=True).exists())


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


class MobileLoginEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "Strong-Mobile-Password-123"
        self.user = get_user_model().objects.create_user(
            username="mobile-login-user",
            password=self.password,
            email="mobile@example.com",
        )
        self.tenant = Tenant.objects.create(name="Login Tenant", slug="login-tenant")
        self.membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=TenantMembership.ROLE_VENDOR_OWNER,
        )
        self.installation_id = uuid.uuid4()

    def tearDown(self):
        cache.clear()

    def payload(self, **overrides):
        values = {
            "username": self.user.username,
            "password": self.password,
            "installation_id": str(self.installation_id),
            "platform": "android",
            "app_version": "1.0.0",
        }
        values.update(overrides)
        return values

    def post_login(self, **overrides):
        return self.client.post(
            "/api/v1/auth/login",
            data=json.dumps(self.payload(**overrides)),
            content_type="application/json",
        )

    def test_single_tenant_login_creates_scoped_session_and_token_pair(self):
        response = self.post_login()

        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        session = MobileSession.objects.get(user=self.user, installation_id=self.installation_id)
        self.assertEqual(body["session"]["active_tenant"]["tenant_id"], self.tenant.pk)
        self.assertEqual(body["session"]["available_tenants"][0]["role"], "vendor_owner")
        self.assertIn("orders.update_status", body["session"]["permissions"])
        self.assertEqual(decode_access_token(body["tokens"]["access_token"])["sid"], str(session.pk))
        self.assertEqual(
            session.refresh_tokens.get().token_hash,
            hash_refresh_token(body["tokens"]["refresh_token"]),
        )
        self.assertNotContains(response, self.password)

    @override_settings(MOBILE_AUTH_ENABLED=False)
    def test_auth_kill_switch_hides_every_auth_endpoint(self):
        for method, path in [
            ("post", "/api/v1/auth/login"),
            ("post", "/api/v1/auth/refresh"),
            ("post", "/api/v1/auth/logout"),
            ("get", "/api/v1/auth/me"),
            ("post", "/api/v1/auth/select-tenant"),
        ]:
            with self.subTest(path=path):
                if method == "get":
                    response = self.client.get(path)
                else:
                    response = self.client.post(
                        path,
                        data=json.dumps({}),
                        content_type="application/json",
                    )
                self.assertEqual(response.status_code, 404)

    @override_settings(MOBILE_API_ENABLED=False)
    def test_api_kill_switch_hides_auth_endpoint(self):
        response = self.post_login()

        self.assertEqual(response.status_code, 404)

    def test_revoke_all_command_supports_dry_run_and_rollback(self):
        login = self.post_login()
        session = MobileSession.objects.get(user=self.user)
        output = StringIO()

        call_command("revoke_mobile_sessions", "--dry-run", stdout=output)
        session.refresh_from_db()
        self.assertEqual(session.status, MobileSession.STATUS_ACTIVE)
        self.assertIn('"sessions_revoked": 1', output.getvalue())

        call_command("revoke_mobile_sessions", "--reason", "gate_rollback")
        session.refresh_from_db()
        self.assertEqual(session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(session.revocation_reason, "gate_rollback")
        self.assertFalse(session.refresh_tokens.filter(revoked_at__isnull=True).exists())
        self.assertTrue(login.json()["data"]["tokens"]["access_token"])

    def test_multiple_tenants_require_explicit_selection(self):
        second_tenant = Tenant.objects.create(name="Second Login Tenant", slug="second-login-tenant")
        TenantMembership.objects.create(
            user=self.user,
            tenant=second_tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )

        response = self.post_login()

        body = response.json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(body["session"]["active_tenant"])
        self.assertEqual(len(body["session"]["available_tenants"]), 2)
        self.assertEqual(body["session"]["permissions"], [])
        self.assertIsNone(decode_access_token(body["tokens"]["access_token"])["tenant_id"])

    def test_bad_credentials_and_inactive_user_are_generic(self):
        bad_password = self.post_login(password="incorrect-password")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.post_login()

        for response in (bad_password, inactive):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "authentication_required")
            self.assertNotContains(response, self.user.username, status_code=401)

    def test_inactive_tenant_cannot_start_mobile_session(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        response = self.post_login()

        self.assertEqual(response.status_code, 401)
        self.assertFalse(MobileSession.objects.filter(user=self.user).exists())

    @override_settings(
        LOGIN_LOCKOUT_ATTEMPTS=2,
        LOGIN_LOCKOUT_WINDOW_SECONDS=60,
        LOGIN_LOCKOUT_DURATION_SECONDS=60,
    )
    def test_existing_lockout_policy_returns_retryable_rate_limit(self):
        first = self.post_login(password="wrong-one")
        locked = self.post_login(password="wrong-two")

        self.assertEqual(first.status_code, 401)
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()["error"]["code"], "rate_limited")
        self.assertIn("Retry-After", locked)

    @override_settings(
        LOGIN_LOCKOUT_ATTEMPTS=2,
        LOGIN_LOCKOUT_WINDOW_SECONDS=60,
        LOGIN_LOCKOUT_DURATION_SECONDS=60,
        LOGIN_TRUSTED_PROXY_COUNT=0,
    )
    def test_spoofed_forwarding_headers_cannot_bypass_username_lockout(self):
        first = self.client.post(
            "/api/v1/auth/login",
            data=json.dumps(self.payload(password="wrong-one")),
            content_type="application/json",
            headers={"X-Forwarded-For": "198.51.100.10"},
        )
        second = self.client.post(
            "/api/v1/auth/login",
            data=json.dumps(self.payload(password="wrong-two")),
            content_type="application/json",
            headers={"X-Forwarded-For": "203.0.113.20"},
        )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)

    def test_invalid_platform_is_rejected_before_authentication(self):
        response = self.post_login(platform="ios")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertIn("platform", response.json()["error"]["fields"])

    def test_relogin_same_installation_revokes_old_refresh_family(self):
        first = self.post_login()
        old_access = first.json()["data"]["tokens"]["access_token"]
        old_refresh = first.json()["data"]["tokens"]["refresh_token"]
        second = self.post_login(app_version="1.0.1")

        self.assertEqual(second.status_code, 200)
        self.assertEqual(MobileSession.objects.filter(user=self.user).count(), 1)
        session = MobileSession.objects.get(user=self.user)
        self.assertEqual(session.app_version, "1.0.1")
        self.assertIsNotNone(
            session.refresh_tokens.get(token_hash=hash_refresh_token(old_refresh)).revoked_at
        )
        self.assertEqual(session.auth_generation, 2)
        rejected = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {old_access}"},
        )
        self.assertEqual(rejected.status_code, 401)

    def test_refresh_rotates_pair_and_reuse_revokes_session(self):
        login = self.post_login()
        tokens = login.json()["data"]["tokens"]
        refresh_payload = {
            "refresh_token": tokens["refresh_token"],
            "installation_id": str(self.installation_id),
        }

        refreshed = self.client.post(
            "/api/v1/auth/refresh",
            data=json.dumps(refresh_payload),
            content_type="application/json",
        )
        reused = self.client.post(
            "/api/v1/auth/refresh",
            data=json.dumps(refresh_payload),
            content_type="application/json",
        )

        self.assertEqual(refreshed.status_code, 200)
        self.assertNotEqual(
            refreshed.json()["data"]["refresh_token"],
            tokens["refresh_token"],
        )
        self.assertEqual(reused.status_code, 401)
        session = MobileSession.objects.get(user=self.user)
        self.assertEqual(session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(session.revocation_reason, "refresh_token_reuse")

    def test_refresh_rejects_wrong_installation_without_consuming_token(self):
        login = self.post_login()
        refresh_token = login.json()["data"]["tokens"]["refresh_token"]

        response = self.client.post(
            "/api/v1/auth/refresh",
            data=json.dumps(
                {
                    "refresh_token": refresh_token,
                    "installation_id": str(uuid.uuid4()),
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        token = MobileRefreshToken.objects.get(token_hash=hash_refresh_token(refresh_token))
        self.assertIsNone(token.consumed_at)

    def test_logout_revokes_only_mobile_session_not_browser_session(self):
        self.client.force_login(self.user)
        browser_user_id = self.client.session[SESSION_KEY]
        login = self.post_login()
        tokens = login.json()["data"]["tokens"]

        response = self.client.post(
            "/api/v1/auth/logout",
            data=json.dumps(
                {
                    "refresh_token": tokens["refresh_token"],
                    "installation_id": str(self.installation_id),
                }
            ),
            content_type="application/json",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        self.assertEqual(response.status_code, 204)
        session = MobileSession.objects.get(user=self.user)
        self.assertEqual(session.status, MobileSession.STATUS_REVOKED)
        self.assertEqual(session.revocation_reason, "logout")
        self.assertFalse(session.refresh_tokens.filter(revoked_at__isnull=True).exists())
        self.assertEqual(self.client.session[SESSION_KEY], browser_user_id)

    def test_multi_tenant_me_and_selection_rotate_to_new_context(self):
        second_tenant = Tenant.objects.create(name="Selectable Tenant", slug="selectable-tenant")
        TenantMembership.objects.create(
            user=self.user,
            tenant=second_tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )
        login = self.post_login()
        tokens = login.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        current = self.client.get("/api/v1/auth/me", headers=headers)
        selected = self.client.post(
            "/api/v1/auth/select-tenant",
            data=json.dumps(
                {
                    "tenant_id": second_tenant.pk,
                    "refresh_token": tokens["refresh_token"],
                }
            ),
            content_type="application/json",
            headers=headers,
        )

        self.assertEqual(current.status_code, 200)
        self.assertIsNone(current.json()["data"]["active_tenant"])
        self.assertEqual(len(current.json()["data"]["available_tenants"]), 2)
        self.assertEqual(selected.status_code, 200)
        selected_data = selected.json()["data"]
        self.assertEqual(selected_data["session"]["active_tenant"]["tenant_id"], second_tenant.pk)
        self.assertNotIn("orders.update_status", selected_data["session"]["permissions"])
        self.assertEqual(
            decode_access_token(selected_data["tokens"]["access_token"])["tenant_id"],
            second_tenant.pk,
        )
        old_token = MobileRefreshToken.objects.get(token_hash=hash_refresh_token(tokens["refresh_token"]))
        self.assertIsNotNone(old_token.consumed_at)

    def test_tenant_selection_rejects_cross_tenant_without_consuming_refresh(self):
        second_tenant = Tenant.objects.create(name="Owned Second Tenant", slug="owned-second-tenant")
        TenantMembership.objects.create(
            user=self.user,
            tenant=second_tenant,
            role=TenantMembership.ROLE_VENDOR_VIEWER,
        )
        forbidden_tenant = Tenant.objects.create(name="Forbidden Tenant", slug="forbidden-tenant")
        login = self.post_login()
        tokens = login.json()["data"]["tokens"]

        response = self.client.post(
            "/api/v1/auth/select-tenant",
            data=json.dumps(
                {
                    "tenant_id": forbidden_tenant.pk,
                    "refresh_token": tokens["refresh_token"],
                }
            ),
            content_type="application/json",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        self.assertEqual(response.status_code, 403)
        session = MobileSession.objects.get(user=self.user)
        self.assertIsNone(session.active_tenant_id)
        refresh = session.refresh_tokens.get(token_hash=hash_refresh_token(tokens["refresh_token"]))
        self.assertIsNone(refresh.consumed_at)

    def test_me_returns_normalized_permission_matrix_for_all_roles(self):
        expected_write_access = {
            TenantMembership.ROLE_VENDOR_OWNER: True,
            TenantMembership.ROLE_VENDOR_OPERATOR: True,
            TenantMembership.ROLE_VENDOR_VIEWER: False,
            TenantMembership.ROLE_WAREHOUSE_OPERATOR: False,
        }

        for index, (role, can_write) in enumerate(expected_write_access.items()):
            with self.subTest(role=role):
                user = get_user_model().objects.create_user(
                    username=f"role-user-{index}",
                    password=self.password,
                )
                tenant = Tenant.objects.create(name=f"Role Tenant {index}", slug=f"role-tenant-{index}")
                TenantMembership.objects.create(user=user, tenant=tenant, role=role)
                login = self.client.post(
                    "/api/v1/auth/login",
                    data=json.dumps(
                        {
                            "username": user.username,
                            "password": self.password,
                            "installation_id": str(uuid.uuid4()),
                            "platform": "android",
                            "app_version": "1.0.0",
                        }
                    ),
                    content_type="application/json",
                )
                token = login.json()["data"]["tokens"]["access_token"]
                current = self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )

                self.assertEqual(login.status_code, 200)
                self.assertEqual(current.status_code, 200)
                permissions = current.json()["data"]["permissions"]
                self.assertEqual("orders.update_status" in permissions, can_write)
                self.assertIn("orders.view", permissions)
                self.assertIn("stock.view", permissions)


class MobileAuthCleanupTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user(username="cleanup-user")
        self.session = create_mobile_session(
            user=self.user,
            installation_id=uuid.uuid4(),
            app_version="1.0.0",
        )

    def expire_session(self, session=None, days=1):
        target = session or self.session
        MobileSession.objects.filter(pk=target.pk).update(
            expires_at=self.now - timedelta(days=days)
        )
        target.refresh_from_db()
        return target

    def test_dry_run_reports_without_changing_records(self):
        self.expire_session()
        token = persist_refresh_token(
            session=self.session,
            raw_token="expired-dry-run",
            expires_at=self.now - timedelta(days=1),
        )

        summary = cleanup_mobile_auth(dry_run=True, now=self.now)

        self.session.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(summary["sessions_expired"], 1)
        self.assertEqual(summary["refresh_tokens_revoked"], 1)
        self.assertEqual(self.session.status, MobileSession.STATUS_ACTIVE)
        self.assertIsNone(token.revoked_at)

    def test_cleanup_is_bounded_and_rerunnable(self):
        sessions = [self.expire_session()]
        for index in range(2):
            user = get_user_model().objects.create_user(username=f"cleanup-{index}")
            sessions.append(
                self.expire_session(
                    create_mobile_session(
                        user=user,
                        installation_id=uuid.uuid4(),
                        app_version="1.0.0",
                    )
                )
            )

        first = cleanup_mobile_auth(batch_size=2, now=self.now)
        second = cleanup_mobile_auth(batch_size=2, now=self.now)
        third = cleanup_mobile_auth(batch_size=2, now=self.now)

        self.assertEqual(first["sessions_expired"], 2)
        self.assertEqual(second["sessions_expired"], 1)
        self.assertEqual(third["sessions_expired"], 0)
        self.assertEqual(
            MobileSession.objects.filter(status=MobileSession.STATUS_EXPIRED).count(),
            3,
        )

    def test_cleanup_preserves_live_session_and_refresh_token(self):
        raw_token, token = issue_refresh_token(self.session, now=self.now)

        summary = cleanup_mobile_auth(retention_days=0, now=self.now)

        self.session.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(raw_token)
        self.assertEqual(self.session.status, MobileSession.STATUS_ACTIVE)
        self.assertIsNone(token.revoked_at)
        self.assertEqual(summary["sessions_deleted"], 0)
        self.assertEqual(summary["refresh_tokens_deleted"], 0)

    def test_cleanup_deletes_only_terminal_history_past_retention(self):
        self.expire_session(days=40)
        MobileSession.objects.filter(pk=self.session.pk).update(
            status=MobileSession.STATUS_EXPIRED,
            revoked_at=self.now - timedelta(days=40),
        )
        token = persist_refresh_token(
            session=self.session,
            raw_token="old-terminal-token",
            expires_at=self.now - timedelta(days=40),
        )
        MobileRefreshToken.objects.filter(pk=token.pk).update(
            revoked_at=self.now - timedelta(days=40)
        )

        summary = cleanup_mobile_auth(retention_days=30, now=self.now)

        self.assertEqual(summary["refresh_tokens_deleted"], 1)
        self.assertEqual(summary["sessions_deleted"], 1)
        self.assertFalse(MobileSession.objects.filter(pk=self.session.pk).exists())
        self.assertFalse(MobileRefreshToken.objects.filter(pk=token.pk).exists())

    def test_management_command_supports_dry_run(self):
        self.expire_session()
        output = StringIO()

        call_command("cleanup_mobile_auth", "--dry-run", stdout=output)

        self.session.refresh_from_db()
        self.assertIn('"dry_run": true', output.getvalue())
        self.assertEqual(self.session.status, MobileSession.STATUS_ACTIVE)

    @patch("core.tasks.write_system_heartbeat")
    def test_celery_task_records_cleanup_heartbeat(self, heartbeat):
        from core.tasks import cleanup_mobile_auth as cleanup_task

        self.expire_session()
        result = cleanup_task.run()

        self.assertEqual(result["sessions_expired"], 1)
        heartbeat.assert_called_once_with("mobile_auth_cleanup", result)
