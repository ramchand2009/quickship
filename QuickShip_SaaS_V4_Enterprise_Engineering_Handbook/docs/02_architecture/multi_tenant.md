# Multi Tenant

The current application has the foundation for multi-tenancy plus a first vendor-facing tenant isolation slice.

`Tenant` and `TenantMembership` models exist. Existing business-owned rows and new rows default to the `Mathukai` tenant with slug `mathukai`. `ActiveTenantMiddleware` resolves a vendor user's first active membership into `request.tenant` and `request.tenant_membership`; super admins remain platform-scoped by default.

Vendor signup creates a new tenant, owner user, `vendor_owner` membership, and default tenant-owned rows for sender address, WooCommerce settings, and WhatsApp settings.

## Current Isolation

- User permissions have tenant-aware helpers.
- Vendor-facing dashboard, order list/detail/update, labels/print queues, stock/product screens, and expense tracker scope by active tenant.
- Order item product matching, profit summaries, packing scan requirements, and stock reconciliation use tenant-aware product lookups.
- Business-owned tables have tenant FKs, including orders, products, stock movements, sender address, WooCommerce settings, WhatsApp settings/templates/logs/queue jobs, expenses, and web push subscriptions.
- WooCommerce, Shiprocket, WhatsApp queue/webhook processing, notification logs/settings, remaining admin/config screens, and tenant-aware uniqueness constraints must still be completed before production multi-vendor integrations are enabled.

## Completed Foundation

- Add a tenant/business model.
- Attach tenant foreign keys to orders, products, stock movements, settings, templates, logs, queue jobs, sender addresses, expenses, and web push subscriptions.
- Backfill existing data into the Mathukai tenant.
- Add request active-tenant resolver and role helpers.
- Add vendor signup/onboarding for tenant owners.
- Add first tenant isolation slice for vendor dashboard/orders/labels/stock/expenses.

## Remaining Work

- Make external integration credentials tenant-owned.
- Add tenant-aware uniqueness constraints for SKU, barcode, external order id, and template config.
- Keep integration jobs tenant-scoped before processing external webhook or queue work for multiple vendors.
- Scope remaining WhatsApp logs/settings, webhooks, admin/reporting, and configuration surfaces by tenant.
