from .access import (
    can_manage_vendor_settings,
    can_manage_stock,
    can_sync_orders,
    can_update_order_status,
    get_active_tenant,
    is_ops_admin,
    is_ops_viewer,
    is_super_admin,
    is_vendor_user,
)
from .models import ProductChangeRequest


def role_flags(request):
    user = getattr(request, "user", None)
    viewer_only_access = bool(getattr(user, "is_authenticated", False) and is_ops_viewer(user))
    active_tenant = get_active_tenant(request)
    pending_product_change_notification_count = 0
    if getattr(user, "is_authenticated", False):
        if is_super_admin(user):
            pending_product_change_notification_count = ProductChangeRequest.objects.filter(
                status=ProductChangeRequest.STATUS_PENDING,
            ).count()
        elif active_tenant and is_vendor_user(user):
            pending_product_change_notification_count = ProductChangeRequest.objects.filter(
                tenant=active_tenant,
                status=ProductChangeRequest.STATUS_PENDING,
            ).count()
    return {
        "active_tenant": active_tenant,
        "active_tenant_membership": getattr(request, "tenant_membership", None),
        "is_ops_admin": is_ops_admin(user),
        "is_ops_viewer": is_ops_viewer(user),
        "is_super_admin": is_super_admin(user),
        "is_vendor_user": is_vendor_user(user),
        "viewer_order_management_only": viewer_only_access,
        "pending_product_change_notification_count": pending_product_change_notification_count,
        "can_sync_orders": can_sync_orders(user),
        "can_update_order_status": can_update_order_status(user),
        "can_manage_stock": can_manage_stock(user),
        "can_manage_current_vendor_settings": is_super_admin(user)
        or can_manage_vendor_settings(user, active_tenant),
    }
