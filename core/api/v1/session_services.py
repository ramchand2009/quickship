"""Service boundary for mobile session lifecycle and tenant context."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import MobileRefreshToken, MobileSession, TenantMembership


class InvalidActiveTenant(ValueError):
    pass


def _has_active_membership(user, tenant):
    return bool(
        getattr(user, "is_active", False)
        and tenant is not None
        and tenant.is_active
        and TenantMembership.objects.filter(
            user=user,
            tenant=tenant,
            is_active=True,
        ).exists()
    )


def validate_active_tenant(user, tenant):
    if tenant is not None and not _has_active_membership(user, tenant):
        raise InvalidActiveTenant("The selected tenant is unavailable.")


@transaction.atomic
def create_mobile_session(*, user, installation_id, app_version, active_tenant=None):
    validate_active_tenant(user, active_tenant)
    return MobileSession.objects.create(
        user=user,
        installation_id=installation_id,
        platform=MobileSession.PLATFORM_ANDROID,
        app_version=str(app_version or "").strip(),
        active_tenant=active_tenant,
    )


@transaction.atomic
def select_session_tenant(*, session, tenant):
    locked_session = MobileSession.objects.select_for_update().select_related("user").get(pk=session.pk)
    validate_active_tenant(locked_session.user, tenant)
    locked_session.active_tenant = tenant
    locked_session.save(update_fields=["active_tenant"])
    return locked_session


@transaction.atomic
def start_mobile_session(*, user, installation_id, app_version, active_tenant=None):
    validate_active_tenant(user, active_tenant)
    now = timezone.now()
    expires_at = now + timedelta(days=settings.MOBILE_SESSION_ABSOLUTE_LIFETIME_DAYS)
    session = (
        MobileSession.objects.select_for_update()
        .filter(user=user, installation_id=installation_id)
        .first()
    )
    if session is None:
        return MobileSession.objects.create(
            user=user,
            installation_id=installation_id,
            platform=MobileSession.PLATFORM_ANDROID,
            app_version=str(app_version or "").strip(),
            active_tenant=active_tenant,
            expires_at=expires_at,
        )

    MobileRefreshToken.objects.filter(session=session, revoked_at__isnull=True).update(revoked_at=now)
    session.platform = MobileSession.PLATFORM_ANDROID
    session.app_version = str(app_version or "").strip()
    session.active_tenant = active_tenant
    session.status = MobileSession.STATUS_ACTIVE
    session.auth_generation += 1
    session.last_seen_at = now
    session.expires_at = expires_at
    session.revoked_at = None
    session.revocation_reason = ""
    session.save(
        update_fields=[
            "platform",
            "app_version",
            "active_tenant",
            "status",
            "auth_generation",
            "last_seen_at",
            "expires_at",
            "revoked_at",
            "revocation_reason",
        ]
    )
    return session


ROLE_PERMISSIONS = {
    TenantMembership.ROLE_VENDOR_OWNER: [
        "dashboard.view",
        "orders.view",
        "orders.update_status",
        "orders.mark_payment_received",
        "stock.view",
        "notifications.view",
    ],
    TenantMembership.ROLE_VENDOR_OPERATOR: [
        "dashboard.view",
        "orders.view",
        "orders.update_status",
        "orders.mark_payment_received",
        "stock.view",
        "notifications.view",
    ],
    TenantMembership.ROLE_VENDOR_VIEWER: [
        "dashboard.view",
        "orders.view",
        "stock.view",
        "notifications.view",
    ],
    TenantMembership.ROLE_WAREHOUSE_OPERATOR: [
        "dashboard.view",
        "orders.view",
        "stock.view",
        "notifications.view",
    ],
}


def serialize_membership(membership):
    return {
        "tenant_id": membership.tenant_id,
        "tenant_name": membership.tenant.name,
        "tenant_slug": membership.tenant.slug,
        "role": membership.role,
        "role_label": membership.get_role_display(),
    }


def serialize_mobile_session(session, memberships):
    membership_by_tenant = {membership.tenant_id: membership for membership in memberships}
    active_membership = membership_by_tenant.get(session.active_tenant_id)
    return {
        "user": {
            "id": session.user_id,
            "username": session.user.username,
            "display_name": session.user.get_full_name() or session.user.username,
            "email": session.user.email or None,
        },
        "active_tenant": serialize_membership(active_membership) if active_membership else None,
        "available_tenants": [serialize_membership(membership) for membership in memberships],
        "permissions": list(ROLE_PERMISSIONS.get(active_membership.role, [])) if active_membership else [],
    }
