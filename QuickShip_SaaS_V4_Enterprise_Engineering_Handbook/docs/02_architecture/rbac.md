# RBAC

Role checks live in `core/access.py`.

## Groups

- `admin`: operations admin. Also implied by Django `is_superuser` or `is_staff`.
- `ops_viewer`: mobile/operator role.

## SaaS Roles

- `super_admin`: represented by Django `is_superuser` or `is_staff`. Super admins use desktop/platform views by default.
- `vendor_owner`: tenant user that can manage vendor settings for their own tenant.
- `vendor_operator`: tenant user that can operate vendor order workflows for their own tenant.
- `vendor_viewer`: reserved tenant viewer role for read-focused vendor access.

## Permission Helpers

- `is_super_admin`: platform admin check.
- `is_vendor_user`: true for active non-superuser tenant memberships.
- `get_active_tenant`: request tenant resolver.
- `can_access_tenant`: tenant membership or super admin check.
- `can_manage_vendor_settings`: tenant owner or super admin check.
- `can_operate_vendor_orders`: tenant owner/operator or super admin check.
- `can_edit_operations`: admin only.
- `can_edit_manual_order_details`: admin and ops viewer.
- `can_sync_orders`: admin and ops viewer.
- `can_update_order_status`: admin and ops viewer.
- `can_manage_stock`: admin and ops viewer.

Users without explicit groups or tenant memberships currently default to admin-like access for backward compatibility unless they are in `ops_viewer`. Users with any tenant membership do not fall through to ops admin, even when the membership or tenant is inactive. A future hardening pass should require explicit role assignment before production multi-user expansion.

## UI Split

- Desktop admin surfaces include broader configuration and management screens.
- Ops viewer/vendor mobile surfaces focus on order handling, status updates, packing, shipping, stock visibility, and label tasks.
- `TenantAwareLoginView` routes super admins to `home` and vendor/mobile users to `order_management`.
- Vendor signup creates a `vendor_owner` membership and routes the new user to `order_management`.
