"""Service boundary for mobile session lifecycle and tenant context."""

from django.db import transaction

from core.models import MobileSession, TenantMembership


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
