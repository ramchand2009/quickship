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

## Important Recent Changes

- Monthly sales/profit now count only value-bearing statuses: accepted, packed, shipped, delivery issue, out for delivery, delivered, completed.
- Profit no longer treats missing `actual_price` as full profit.
- Profit can calculate for order items without SKU when there is exactly one product with the same name.
- AI project memory and handbook files are stored under `QuickShip_SaaS_V4_Enterprise_Engineering_Handbook/`.
- Phase 1 SaaS foundation added tenant/membership models, default tenant migrations for users and business-owned data, user membership backfill by existing groups, and tenant permission/queryset mixins.

## Known Constraints

- Business data tables have tenant FKs, but existing views, UI routing, queries, and integrations are not tenant-filtered yet.
- There is no Celery dependency in the current code; queue workers are Django management commands.
- WooCommerce is the richer current commerce integration; Shiprocket sync is simpler and imports only new-like orders.
- App code should not be changed while updating this handbook unless explicitly requested.
