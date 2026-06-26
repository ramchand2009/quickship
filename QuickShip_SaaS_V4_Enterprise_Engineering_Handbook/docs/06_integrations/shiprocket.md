# Shiprocket

Shiprocket support exists in `core.shiprocket` and is simpler than WooCommerce support.

## Configuration

- `SHIPROCKET_BASE_URL`
- `SHIPROCKET_EMAIL`
- `SHIPROCKET_PASSWORD`

## Current Behavior

- Authenticates with Shiprocket and reads `/orders`.
- Imports only orders whose external status normalizes to a new-order style status.
- Normalizes customer fields, billing/shipping address, item name/SKU/channel id, quantity, price, order date, payment method, total, and raw payload.
- Writes records into `ShiprocketOrder` using the external Shiprocket id.

Local workflow, stock, packing, labels, and WhatsApp behavior then operate on the same `ShiprocketOrder` model used for WooCommerce.
