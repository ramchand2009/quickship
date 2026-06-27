# Multi Tenant

The current application has the foundation for multi-tenancy plus a first vendor-facing tenant isolation slice.

`Tenant` and `TenantMembership` models exist. Existing business-owned rows and new rows default to the `Mathukai` tenant with slug `mathukai`. `ActiveTenantMiddleware` resolves a vendor user's first active membership into `request.tenant` and `request.tenant_membership`; super admins remain platform-scoped by default.

Vendor signup creates a new tenant, owner user, `vendor_owner` membership, and tenant-owned sender/settings placeholders. Current production integration design uses shared WooCommerce and Libromi credentials managed by Super Admin.

## Current Isolation

- User permissions have tenant-aware helpers.
- Vendor-facing dashboard, order list/detail/update, labels/print queues, stock/product screens, and expense tracker scope by active tenant.
- Order item product matching, profit summaries, packing scan requirements, and stock reconciliation use tenant-aware product lookups.
- Business-owned tables have tenant FKs, including orders, products, stock movements, sender address, WooCommerce settings, WhatsApp settings/templates/logs/queue jobs, expenses, and web push subscriptions.
- WooCommerce and WhatsApp runtime paths use shared platform credentials. WooCommerce products/orders are assigned to tenants by `TenantWooCommerceMappingRule` rows. WhatsApp queue/log records remain tenant-owned while sends use the shared Libromi settings.
- Super admins have read-only tenant list/detail pages for tenant operations overview. Shiprocket legacy integration paths, remaining admin/config actions, and the remaining tenant-aware uniqueness constraints must still be completed before production multi-vendor integrations are enabled.

## Completed Foundation

- Add a tenant/business model.
- Attach tenant foreign keys to orders, products, stock movements, settings, templates, logs, queue jobs, sender addresses, expenses, and web push subscriptions.
- Backfill existing data into the Mathukai tenant.
- Add request active-tenant resolver and role helpers.
- Add vendor signup/onboarding for tenant owners.
- Add first tenant isolation slice for vendor dashboard/orders/labels/stock/expenses.
- Add Super Admin tenant list/detail pages for read-only tenant oversight.
- Add WooCommerce tenant mapping rules for shared-store product/order assignment.

## Remaining Work

- Add Super Admin tenant management actions for activation and membership management.
- Add tenant-aware uniqueness constraints for SKU, barcode, external order id, and remaining settings defaults.
- Keep legacy/non-WooCommerce integration jobs tenant-scoped before processing external webhook or queue work for multiple vendors.
- Scope remaining admin/reporting and configuration surfaces by tenant.
