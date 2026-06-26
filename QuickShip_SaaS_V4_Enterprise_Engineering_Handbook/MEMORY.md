# Project Memory

## Current Codebase Facts

- Django project: `Ram_codex1`.
- Main app: `core`.
- Primary operational model: `ShiprocketOrder`, used for both Shiprocket and WooCommerce sourced orders.
- Local fulfilment status is stored in `ShiprocketOrder.local_status`.
- WooCommerce-specific fields live on the same order model: `woocommerce_order_id`, `woocommerce_order_key`, `woocommerce_status`, sync timestamps, and raw payload.
- Products use normalized uppercase SKU and optional barcode / SmartBiz or WooCommerce product id mapping.
- Product `actual_price` is the cost basis for profit calculations.
- Profit calculation matches order items to products by SKU, channel SKU, channel product id, SmartBiz id, and unique exact product name fallback.
- Stock deductions happen when an order transitions into `order_accepted`; stock restore happens when an order transitions into `order_cancelled`.
- Packing requires valid product mapping and SKU/barcode scan completion before moving to packed.
- Shipping label generation supports individual and bulk 4x6 layouts and PDF output.
- WhatsApp notifications are queued through `WhatsAppNotificationQueue`, sent through Whatomate / Cloud API helpers, and audited in `WhatsAppNotificationLog`.
- System/runtime status uses heartbeat files and diagnostics helpers rather than a separate observability service.
- Tenant foundation models now exist: `Tenant` and `TenantMembership`.
- The default tenant is `Mathukai` with slug `mathukai`.
- Tenant roles currently defined in code are `vendor_owner`, `vendor_operator`, and `vendor_viewer`.
- Super admin is currently represented by Django `is_superuser` or `is_staff`.
- Business-owned models now have a tenant FK that defaults existing and newly-created rows to the Mathukai tenant.
- `ActiveTenantMiddleware` attaches `request.tenant` and `request.tenant_membership` for authenticated vendor users.
- Super admin requests remain platform-scoped by default with no implicit active tenant.
- `TenantAwareLoginView` routes super admins to the desktop dashboard and vendor/mobile users to order management.
- Signup now creates a vendor workspace: tenant, owner user, owner membership, sender address, WooCommerce settings, and WhatsApp settings.
- Vendor-facing dashboard, order list/detail/update, labels/print queues, stock/product screens, and expense tracker now scope querysets and object access to `request.tenant`.
- Order item product matching, profit summaries, packing scan requirements, and stock reconciliation now use the order or active tenant when matching products.
- WooCommerce settings, manual order sync, product sync, product updates, order status updates, and webhooks are tenant-aware. Non-default vendor tenants use their own WooCommerce settings only.
- WhatsApp settings, templates, status configs, queue jobs, queue workers, and delivery logs are tenant-aware. Non-default vendor tenants use their own WhatsApp settings only and do not fall back to Mathukai/global environment credentials.

## Important Recent Changes

- Monthly sales/profit now count only value-bearing statuses: accepted, packed, shipped, delivery issue, out for delivery, delivered, completed.
- Profit no longer treats missing `actual_price` as full profit.
- Profit can calculate for order items without SKU when there is exactly one product with the same name.
- AI project memory and handbook files are stored under `QuickShip_SaaS_V4_Enterprise_Engineering_Handbook/`.
- Phase 1 SaaS foundation added tenant/membership models, default tenant migrations for users and business-owned data, user membership backfill by existing groups, and tenant permission/queryset mixins.
- Phase 2 SaaS role groundwork added vendor role helpers, active tenant request resolution, tenant-aware login routing, context flags, and tests for the role/middleware/login foundation.
- Phase 3 SaaS onboarding changed signup into vendor onboarding and added tests for workspace creation and inactive tenant/membership access blocking.
- Phase 6 first slice added tenant isolation for vendor dashboard/orders/labels/stock/expenses and focused tests proving vendor A cannot view or mutate vendor B records through those screens.
- WooCommerce tenant isolation was added for the active commerce integration: sync uses the active vendor tenant, webhooks resolve tenant by secret/signature, imports write tenant-owned orders/products/categories, and tests cover tenant-scoped sync/webhook behavior.
- WhatsApp tenant isolation was added: settings lookup is tenant-specific, template/status config uniqueness is tenant-scoped, queue jobs/logs carry tenant, workers can process one tenant at a time, and tests cover cross-tenant queue/template behavior.

## Known Constraints

- Business data tables have tenant FKs, core vendor-facing screens scope by tenant, WooCommerce is tenant-aware, and WhatsApp runtime/queue/settings are tenant-aware.
- Do not enable full production multi-vendor automation until remaining admin/config/reporting surfaces, sender-label configuration, web push, and the rest of the tenant-aware uniqueness constraints are hardened.
- Legacy users without explicit groups or tenant memberships still fall back to admin-like access for backward compatibility; any user with tenant memberships no longer falls through to ops admin, even if the membership or tenant is inactive.
- There is no Celery dependency in the current code; queue workers are Django management commands.
- WooCommerce is the active commerce integration. Shiprocket remains legacy naming/integration code and is not the current implementation priority.
- App code should not be changed while updating this handbook unless explicitly requested.
