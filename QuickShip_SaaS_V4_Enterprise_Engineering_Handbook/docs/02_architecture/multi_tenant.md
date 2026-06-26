# Multi Tenant

The current application is not multi-tenant.

There is no tenant/company/store model, no tenant foreign key on orders/products/settings, and no request-scoped tenant resolver. The active deployment assumption is a single Mathukai/QuickShip business instance.

## Current Isolation

- User permissions are role-based, not tenant-based.
- WooCommerce, Shiprocket, WhatsApp, sender address, templates, products, orders, stock, and logs are global within the database.

## If SaaS Multi-Tenancy Becomes Required

- Add a tenant/business model.
- Attach tenant foreign keys to orders, products, stock movements, settings, templates, logs, queue jobs, sender addresses, expenses, and web push subscriptions.
- Scope all querysets by tenant.
- Make external integration credentials tenant-owned.
- Add tenant-aware uniqueness constraints for SKU, barcode, external order id, and template config.
- Add migration/backfill strategy for the existing single tenant.
