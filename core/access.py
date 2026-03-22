OPS_ADMIN_GROUP = "admin"
OPS_VIEWER_GROUP = "ops_viewer"


def user_group_names(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(user.groups.values_list("name", flat=True))


def is_ops_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
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


def can_sync_orders(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)


def can_update_order_status(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_ops_admin(user) or is_ops_viewer(user)
