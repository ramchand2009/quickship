# WooCommerce

WooCommerce uses one shared store/API connection for all vendors. `WooCommerceSettings` remains the runtime credential/settings model, but it is managed as a platform/Super Admin setting, not a vendor-entered credential.

Vendor ownership of WooCommerce data is resolved through `TenantWooCommerceMappingRule` rows. Rules can match WooCommerce category name, tag name, SKU prefix, or product id.

Super Admin users manage a tenant's mapping rules from the tenant detail page at `/tenants/<id>/`. Vendor users cannot access or mutate these rules.

## Capabilities

- Check API connection.
- Import recent orders for configured statuses.
- Import and sync products, including variable product variations.
- Update WooCommerce product stock/name/category/description/prices from local product detail screens.
- Update WooCommerce order status from local fulfilment status.
- Receive WooCommerce webhooks at `/webhooks/woocommerce/`.
- Authenticate webhooks with the shared webhook secret before importing.
- Assign product/order tenant from WooCommerce mapping rules.

## Order Import

`core.woocommerce.import_order_payload` creates or updates tenant-owned `ShiprocketOrder` records using ids like `WC-<id>`.

Billing and shipping addresses are compacted into JSON fields. Line items are normalized into `order_items` with name, SKU, channel SKU, product id, variation id, quantity, price, and image.

Orders without a billing delivery address are skipped unless a local record already exists.

Tenant assignment checks line item SKU/product id first, then existing local products, then product category/tag payloads when category/tag mapping rules exist. Unmapped orders fall back to the default Mathukai tenant.

## Product Sync

Product/category sync uses the shared WooCommerce connection. Each product is assigned to a tenant from mapping rules. Unmapped products fall back to the default Mathukai tenant.

Product updates and refreshes use the shared WooCommerce connection.

## Webhooks

The webhook handler checks the signature/query secret against `WooCommerceSettings.webhook_secret`. The imported order's tenant comes from mapping rules, not from the settings row.

## Status Mapping

Default local-to-WooCommerce mapping sends accepted/packed/issue/out-for-delivery to processing, shipped/delivered/completed to completed, and cancelled to cancelled. A custom JSON status map can override defaults.
