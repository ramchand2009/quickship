# Indexes

Explicit indexing is limited today.

Current notable indexes/constraints:

- `ShiprocketOrder.shiprocket_order_id` is unique.
- `ShiprocketOrder.woocommerce_order_id` has `db_index=True`.
- `Product.sku` is unique.
- `Product.barcode` is unique and nullable.
- `Product.smartbiz_product_id` is unique and nullable.
- `StockMovement.reference_key` is unique and nullable for idempotent stock movements.
- `WhatsAppTemplate` has a unique constraint on `(name, language)`.
- `WhatsAppStatusTemplateConfig.local_status` is unique.
- `WebPushSubscription.endpoint` is unique.

Potential future indexes:

- `ShiprocketOrder.local_status`, `order_date`, `updated_at`, and `(local_status, order_date)` for dashboard tabs.
- `WhatsAppNotificationQueue.status`, `next_retry_at`, `locked_at` for worker polling.
- `OrderActivityLog.event_type`, `created_at`, `shiprocket_order_id` for audit screens.
- `WhatsAppNotificationLog.trigger`, `delivery_status`, `created_at` for log filtering.
