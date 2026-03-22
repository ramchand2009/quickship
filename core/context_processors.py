from .access import can_sync_orders, can_update_order_status, is_ops_admin, is_ops_viewer


def role_flags(request):
    user = getattr(request, "user", None)
    viewer_only_access = bool(getattr(user, "is_authenticated", False) and is_ops_viewer(user))
    return {
        "is_ops_admin": is_ops_admin(user),
        "is_ops_viewer": is_ops_viewer(user),
        "viewer_order_management_only": viewer_only_access,
        "can_sync_orders": can_sync_orders(user),
        "can_update_order_status": can_update_order_status(user),
    }
