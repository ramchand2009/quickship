# Models

Main models live in `core/models.py`.

- `ShiprocketOrder`: central order table for Shiprocket and WooCommerce orders. Stores source ids, customer/contact fields, manual overrides, local workflow status, cancellation fields, tracking, shipping cost, status timestamps, labels, addresses, items, raw payload, and sync timestamps.
- `Product`: local product/stock table with name, category, SKU, barcode, external product id, image, description, actual cost, regular/sale prices, stock quantity, reorder level, active flag.
- `ProductCategory`: normalized category list used by products.
- `StockMovement`: append-only stock ledger for manual add/remove/set, special issue, order acceptance deduction, and cancellation restore.
- `OrderActivityLog`: operational audit trail for status changes, manual updates, WhatsApp events, label prints, stock events, and warnings.
- `WhatsAppSettings`: runtime WhatsApp/Whatomate configuration.
- `WhatsAppTemplate`: synced external template metadata and raw payload.
- `WhatsAppStatusTemplateConfig`: local-status-to-template mapping and placeholder mapping.
- `WhatsAppNotificationQueue`: queued notification work with retry/lock/result fields.
- `WhatsAppNotificationLog`: send/webhook audit log.
- `WooCommerceSettings`: runtime WooCommerce store credentials, webhook secret, import statuses, and status mapping.
- `TenantWooCommerceMappingRule`: maps shared WooCommerce data to tenants by category, tag, SKU prefix, or product id.
- `WebPushSubscription`: browser push subscription keys.
- `SenderAddress`: default sender details for shipping labels.
- `BusinessExpense` and `ExpensePerson`: basic expense tracking.
- `ContactMessage` and `Project`: simple site/project support models.

## SaaS Foundation Models

- `Tenant`: vendor/business account with name, slug, active flag, owner, contact fields, and timestamps.
- `TenantMembership`: user-to-tenant role mapping with `vendor_owner`, `vendor_operator`, and `vendor_viewer` roles plus active flag.

Most business-owned models now include a tenant FK defaulting to the Mathukai tenant. WooCommerce and WhatsApp credentials are shared platform settings; vendor data ownership is handled by tenant FKs and WooCommerce mapping rules.
