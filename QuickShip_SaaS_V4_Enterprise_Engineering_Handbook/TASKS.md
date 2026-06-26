# Tasks

## Goal

Convert the current single-vendor Mathukai operations dashboard into a multi-vendor SaaS while preserving the existing working order, stock, packing, shipping, WhatsApp, and profit workflows.

## Phase 0 - Discovery And Guardrails

- Freeze application feature work until the tenant design is agreed.
- Document every model that stores business-owned data and must become tenant-scoped.
- Confirm the default tenant name and slug for existing data: `Mathukai`.
- Confirm URL strategy for tenants: path-based, subdomain-based, or session-selected tenant.
- Define SaaS boundaries: vendor users use mobile UI only; super admins use desktop UI only.
- Add a migration rollback plan and backup requirement before data migration.

## Phase 1 - Tenant Model

- [Done] Add `Tenant` model with fields for name, slug, active flag, owner user, contact details, created/updated timestamps.
- Add tenant branding/settings fields only if needed for mobile UI: display name, logo, sender defaults.
- Add tenant-owned integration settings or tenant FK to existing settings tables:
  - WooCommerce settings
  - WhatsApp settings
  - WhatsApp templates/config
  - Sender address
- Decide whether product categories are tenant-owned; default should be tenant-owned.
- [Done] Add `TenantMembership` model linking user, tenant, role, active flag.
- [Done] Add default `Mathukai` tenant migration.
- [Done] Backfill active users into default tenant memberships based on existing `admin` and `ops_viewer` groups.
- [Done] Add tenant permission helpers and foundation mixins.

## Phase 2 - User Roles

- [Done] Define global role: `super_admin`.
- Define tenant roles:
  - [Done] `vendor_owner`
  - [Done] `vendor_operator`
  - [Done] optional `vendor_viewer`
- [Done] Prevent tenant-membership users from falling through to broad legacy ops admin access.
- Keep full removal of the legacy no-group admin fallback for a later hardening pass after all current users have explicit roles.
- [Done] Keep Django `is_superuser` / `is_staff` mapped to super admin desktop access.
- Vendor roles must never see another tenant's data. Foundation helpers exist, but full query/object isolation is Phase 6.
- [Done] Update access helper design before touching business querysets:
  - [Done] `is_super_admin(user)`
  - [Done] `get_active_tenant(request)`
  - [Done] `can_access_tenant(user, tenant)`
  - [Done] `can_manage_vendor_settings(user, tenant)`
  - [Done] `can_operate_vendor_orders(user, tenant)`
- [Done] Add request-scoped active tenant middleware for vendor users.
- [Done] Add role flags for templates/context without changing UI templates.
- [Done] Add tenant-aware login routing:
  - [Done] super admin -> desktop dashboard
  - [Done] vendor/mobile user -> mobile operations dashboard

## Phase 3 - Signup And Login

- [Done] Redesign signup as vendor onboarding:
  - [Done] create tenant
  - [Done] create owner user
  - [Done] create tenant membership
  - [Done] seed default sender/settings rows for that tenant
- [Done] Keep super admin creation separate through Django admin/management command.
- On login, route users by role:
  - [Done in Phase 2] super admin -> desktop dashboard
  - [Done in Phase 2] vendor user -> mobile operations dashboard
- Add tenant selection only if one user can belong to multiple tenants. Deferred because current onboarding creates one tenant per owner.
- [Done] Ensure inactive tenants and inactive memberships cannot access vendor workflow permissions.

## Phase 4 - Tenant Isolation Data Model

- [Done in Phase 1] Add tenant FK to business-owned tables:
  - `ShiprocketOrder`
  - `Product`
  - `ProductCategory`
  - `StockMovement`
  - `OrderActivityLog`
  - `WhatsAppNotificationQueue`
  - `WhatsAppNotificationLog`
  - `WhatsAppTemplate`
  - `WhatsAppStatusTemplateConfig`
  - `WhatsAppSettings`
  - `WooCommerceSettings`
  - `SenderAddress`
  - `BusinessExpense`
  - `ExpensePerson`
  - `WebPushSubscription`
- Review whether `ContactMessage` and `Project` should be global or tenant-owned.
- Update uniqueness constraints to include tenant where needed:
  - product SKU
  - product barcode
  - external product id
  - Shiprocket/WooCommerce order ids
  - [Done] WhatsApp status template config
  - [Done] WhatsApp template name/language
  - sender/default settings
- Add indexes for common tenant-scoped queries:
  - `(tenant, local_status, order_date)`
  - `(tenant, updated_at)`
  - `(tenant, sku)`
  - `(tenant, status, next_retry_at)` for queue jobs

## Phase 5 - Migration Of Existing Mathukai Data

- [Done in Phase 1] Create default tenant row: `Mathukai`.
- [Done in Phase 1] Backfill all existing business-owned rows to the Mathukai tenant.
- [Done in Phase 1] Backfill current users:
  - superusers/staff -> super admin
  - current admin group users -> Mathukai vendor owner/admin membership, unless they should be super admin
  - current ops viewer group users -> Mathukai vendor operator membership
- Preserve existing order ids, product SKUs, stock movements, queue jobs, logs, and settings.
- Run migration in staging with production data backup before applying live.
- Add verification report:
  - count orders by tenant
  - count products by tenant
  - count stock movements by tenant
  - count WhatsApp jobs/logs by tenant
  - users without membership
  - rows with null tenant after migration

## Phase 6 - Queryset Isolation

- [Partial] Add tenant filtering to data access paths before enabling multiple tenants.
- [Done - vendor-facing slice] Scope dashboards, order lists, order detail, order updates, stock, products, labels/print queues, packing lists, expenses, and order export by active tenant.
- Still pending: admin/reporting paths, category management hardening, sender-label configuration, web push, and non-WooCommerce integration workers.
- [Done - vendor-facing slice] Add defensive checks on object detail/update views: object tenant must match active tenant.
- Scope service functions by tenant:
  - [Done] product matching
  - [Done] stock deduction/restore through order tenant product matching
  - [Done] profit calculation
  - [Done] packing scan requirements
  - [Done] WooCommerce sync
  - [Done] WhatsApp queue processing
- [Partial] Add tests proving vendor A cannot access vendor B records:
  - [Done] orders
  - [Done] order detail/update
  - [Done] products/stock
  - [Done] expenses
  - [Done] WooCommerce sync/webhook tenant behavior
  - [Done] WhatsApp settings/templates/queue worker tenant behavior
  - Pending: labels/export edge cases and remaining admin/config/reporting surfaces

## Phase 7 - Vendor Mobile UI Only

- Route vendor users only to mobile ops templates.
- Hide or block desktop admin dashboards for vendor roles.
- Keep vendor UI focused on:
  - home/mobile dashboard
  - order tabs
  - order detail
  - accept/reject
  - delivery edits
  - packing scan
  - shipping/tracking
  - labels/print queue
  - stock/product operations allowed for vendors
  - WhatsApp resend/payment reminder if allowed
- Remove tenant-global controls from vendor pages unless explicitly tenant-scoped.

## Phase 8 - Super Admin Desktop UI Only

- Super admin desktop dashboard should manage SaaS-wide concerns:
  - tenants
  - tenant users/memberships
  - tenant status/activation
  - support impersonation or tenant switch, if approved
  - platform health and metrics
  - cross-tenant audit overview
- Super admin should not use mobile vendor UI as the default.
- Any support access into a tenant must be explicit and logged.

## Phase 9 - Integration Isolation

- [Done] WooCommerce credentials and webhook secrets are tenant-owned for non-default vendor tenants.
- [Done] WooCommerce webhook resolution identifies tenant by signature/query secret before importing.
- [Done] WhatsApp settings/templates are tenant-owned.
- [Done] WhatsApp queue workers can process tenant-scoped jobs and load that tenant's credentials.
- Sender address and shipping labels must use the order tenant's sender config.
- Web Push subscriptions must be tenant/user scoped.

## Phase 10 - Testing And Release

- Add model migration tests where possible.
- Add tenant isolation tests for all major views and services.
- Add role routing tests for login/signup.
- Add regression tests for Mathukai default tenant migration.
- Run `manage.py check`, focused workflow tests, and full test suite before release.
- Deploy to staging first and run smoke/preflight checks.
- Take production backup before live migration.
- After live migration, verify no business-owned row has null tenant.

## Review

- Confirm whether the handbook folder should remain nested or move to repo root.
- Confirm production deploy target: Easypanel Git deployment or Hostinger VPS Docker.
- Confirm what to do with untracked `TODAY_SUMMARY_2026-06-23.md`.

## Done

- Documented current app scope, tech stack, architecture, database model, integrations, and workflows.
- Added project memory for recent profit/dashboard behavior.
- Created phased implementation plan for multi-vendor SaaS conversion.
- Implemented Phase 1 tenant foundation: `Tenant`, `TenantMembership`, default Mathukai tenant migration, business data tenant backfill, membership backfill, access helpers, and foundation tests.
- Implemented Phase 2 role foundation: vendor role helpers, active tenant request middleware, tenant-aware login routing, template context flags, and focused tenant foundation tests.
- Implemented Phase 3 vendor onboarding: signup creates tenant, owner membership, default sender/WooCommerce/WhatsApp settings, routes to mobile operations, and blocks inactive tenant users from legacy admin fallback.
- Implemented Phase 6 first isolation slice: vendor-facing dashboard/orders/labels/stock/expenses now scope to active tenant, stock/product service matching uses tenant context, and isolation tests cover order, stock, and expense access.
- Implemented WooCommerce tenant isolation: vendor sync and product sync use active tenant settings, webhooks resolve tenant by secret/signature, imports assign tenant-owned orders/products/categories, and tests cover tenant-scoped behavior.
- Implemented WhatsApp tenant isolation: non-default vendor tenants use their own settings only, template/status uniqueness is tenant-scoped, queue jobs/logs carry tenant, queue workers can process by tenant, and tests cover tenant-scoped behavior.
