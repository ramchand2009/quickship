# RBAC

Role checks live in `core/access.py`.

## Groups

- `admin`: operations admin. Also implied by Django `is_superuser` or `is_staff`.
- `ops_viewer`: mobile/operator role.

## Permission Helpers

- `can_edit_operations`: admin only.
- `can_edit_manual_order_details`: admin and ops viewer.
- `can_sync_orders`: admin and ops viewer.
- `can_update_order_status`: admin and ops viewer.
- `can_manage_stock`: admin and ops viewer.

Users without explicit groups currently default to admin-like access for backward compatibility unless they are in `ops_viewer`. This is important: a future hardening pass should require explicit role assignment before production multi-user expansion.

## UI Split

- Desktop admin surfaces include broader configuration and management screens.
- Ops viewer/mobile surfaces focus on order handling, status updates, packing, shipping, stock visibility, and label tasks.
