# WooCommerce

WooCommerce is configured through `WooCommerceSettings` or environment variables.

## Capabilities

- Check API connection.
- Import recent orders for configured statuses.
- Import and sync products, including variable product variations.
- Update WooCommerce product stock/name/category/description/prices from local product detail screens.
- Update WooCommerce order status from local fulfilment status.
- Receive WooCommerce webhooks at `/webhooks/woocommerce/`.

## Order Import

`core.woocommerce.import_order_payload` creates or updates `ShiprocketOrder` records using ids like `WC-<id>`. Billing and shipping addresses are compacted into JSON fields. Line items are normalized into `order_items` with name, SKU, channel SKU, product id, variation id, quantity, price, and image.

Orders without a billing delivery address are skipped unless a local record already exists.

## Status Mapping

Default local-to-WooCommerce mapping sends accepted/packed/issue/out-for-delivery to processing, shipped/delivered/completed to completed, and cancelled to cancelled. A custom JSON status map can override defaults.
