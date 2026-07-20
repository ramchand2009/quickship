"""Tenant and role permission policies for the version 1 mobile API."""

from rest_framework.permissions import BasePermission


class HasActiveMobileTenant(BasePermission):
    message = "Select an active tenant to continue."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request, "mobile_session", None)
            and getattr(request, "tenant", None)
            and getattr(request, "tenant_membership", None)
        )


class HasMobileTenantRole(HasActiveMobileTenant):
    message = "Your tenant role cannot perform this action."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        allowed_roles = set(getattr(view, "mobile_allowed_roles", ()) or ())
        return bool(allowed_roles and request.tenant_membership.role in allowed_roles)
