# WooCommerce

WooCommerce is configured through tenant-owned `WooCommerceSettings`.

The default Mathukai tenant can still use the legacy environment fallback for backward compatibility. Non-default vendor tenants must use their own `WooCommerceSettings` row and do not fall back to global environment credentials.

## Capabilities

- Check API connection.
- Import recent orders for configured statuses.
- Import and sync products, including variable product variations.
- Update WooCommerce product stock/name/category/description/prices from local product detail screens.
- Update WooCommerce order status from local fulfilment status.
- Receive WooCommerce webhooks at `/webhooks/woocommerce/`.
- Resolve webhook tenant by signature or query secret before importing.

## Order Import

`core.woocommerce.import_order_payload` creates or updates tenant-owned `ShiprocketOrder` records using ids like `WC-<id>`. If a WooCommerce id collides across tenants, tenant-prefixed local ids are used for non-default tenants.

Billing and shipping addresses are compacted into JSON fields. Line items are normalized into `order_items` with name, SKU, channel SKU, product id, variation id, quantity, price, and image.

Orders without a billing delivery address are skipped unless a local record already exists.

## Product Sync

Product and category sync accepts tenant context. Non-default vendor sync creates and updates products/categories only inside that vendor tenant.

Product updates and refreshes use the product's tenant settings.

## Webhooks

The webhook handler checks the signature against active tenant `WooCommerceSettings.webhook_secret` rows or accepts the query-secret fallback. The matched tenant is passed to the import path so webhook-created orders belong to the correct vendor.

## Status Mapping

Default local-to-WooCommerce mapping sends accepted/packed/issue/out-for-delivery to processing, shipped/delivered/completed to completed, and cancelled to cancelled. A custom JSON status map can override defaults.
