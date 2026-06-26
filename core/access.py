OPS_ADMIN_GROUP = "admin"
OPS_VIEWER_GROUP = "ops_viewer"
SUPER_ADMIN_ROLE = "super_admin"


def user_group_names(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(user.groups.values_list("name", flat=True))


def is_super_admin(user):
    return bool(getattr(user, "is_authenticated", False) and (user.is_superuser or user.is_staff))


def is_ops_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if is_super_admin(user):
        return True
    group_names = user_group_names(user)
    if OPS_ADMIN_GROUP in group_names:
        return True
    if OPS_VIEWER_GROUP in group_names:
        return False
    # Backward compatible default for existing users without explicit role assignment.
    return True


def is_ops_viewer(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return False
    group_names = user_group_names(user)
    return OPS_VIEWER_GROUP in group_names and OPS_ADMIN_GROUP not in group_names


def can_edit_operations(user):
    return is_ops_admin(user)


def can_edit_manual_order_details(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)


def can_sync_orders(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)


def can_update_order_status(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)


def can_manage_stock(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)


def active_tenant_memberships(user):
    if not getattr(user, "is_authenticated", False):
        return []
    from .models import TenantMembership

    return list(
        TenantMembership.objects.select_related("tenant")
        .filter(user=user, is_active=True, tenant__is_active=True)
        .order_by("tenant__name", "role")
    )


def get_user_default_tenant(user):
    memberships = active_tenant_memberships(user)
    if memberships:
        return memberships[0].tenant
    return None


def can_access_tenant(user, tenant):
    if not getattr(user, "is_authenticated", False) or tenant is None:
        return False
    if is_super_admin(user):
        return True
    return any(membership.tenant_id == tenant.pk for membership in active_tenant_memberships(user))


def has_tenant_role(user, tenant, roles):
    if not getattr(user, "is_authenticated", False) or tenant is None:
        return False
    if is_super_admin(user):
        return True
    role_set = {roles} if isinstance(roles, str) else set(roles or [])
    return any(
        membership.tenant_id == tenant.pk and membership.role in role_set
        for membership in active_tenant_memberships(user)
    )


def can_manage_vendor_settings(user, tenant):
    from .models import TenantMembership

    return has_tenant_role(user, tenant, {TenantMembership.ROLE_VENDOR_OWNER})


def can_operate_vendor_orders(user, tenant):
    from .models import TenantMembership

    return has_tenant_role(
        user,
        tenant,
        {
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
        },
    )


class TenantPermissionMixin:
    """Foundation mixin for future tenant-aware class-based views."""

    tenant_kwarg = "tenant"

    def get_active_tenant(self):
        tenant = getattr(self, "tenant", None)
        if tenant is not None:
            return tenant
        request = getattr(self, "request", None)
        if request is None:
            return None
        tenant = getattr(request, "tenant", None)
        if tenant is not None:
            return tenant
        return get_user_default_tenant(request.user)

    def has_tenant_permission(self):
        request = getattr(self, "request", None)
        if request is None:
            return False
        return can_access_tenant(request.user, self.get_active_tenant())


class TenantScopedQuerysetMixin(TenantPermissionMixin):
    tenant_field = "tenant"

    def scope_queryset_to_tenant(self, queryset):
        tenant = self.get_active_tenant()
        if tenant is None:
            return queryset.none()
        return queryset.filter(**{self.tenant_field: tenant})
