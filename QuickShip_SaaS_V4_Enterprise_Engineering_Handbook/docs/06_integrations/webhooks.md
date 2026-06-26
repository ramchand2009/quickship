# Webhooks

## WooCommerce

Endpoint: `/webhooks/woocommerce/`

The view validates WooCommerce webhook signatures when a webhook secret is configured. Incoming payloads are imported through `core.woocommerce.import_order_payload`.

## Whatomate / WhatsApp

Endpoint: `/webhooks/whatomate/`

The view normalizes incoming status/incoming-message events, resolves orders by order id or phone/idempotency data, records WhatsApp logs, and supports diagnostics/testing helpers.

## Diagnostics

The UI includes webhook diagnostics, an internal webhook test payload, stale webhook checks, recent failure summaries, and queue diagnostics.
