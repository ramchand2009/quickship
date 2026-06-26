# Event Flow

## Order Import

1. WooCommerce or Shiprocket sync fetches external orders.
2. Payloads are normalized into `ShiprocketOrder`.
3. External payloads are retained in `raw_payload`.
4. Local status starts from source mapping, usually `new_order`.

## Status Update

1. Operator submits `ShiprocketOrderStatusForm`.
2. Form validates allowed transition, packing requirements, tracking/shipping cost requirements, and locked states.
3. View applies timestamps such as packed, shipped, out for delivery, delivered, completed.
4. `sync_stock_for_status_transition` deducts or restores stock where relevant.
5. WooCommerce status is updated for WooCommerce-sourced orders.
6. WhatsApp notification is enqueued.
7. `OrderActivityLog` records the status event and related stock/queue outcomes.

## Packing

1. Accepted order shows packing requirements from product mappings.
2. Operator scans SKU/barcode values.
3. `validate_packing_scans` checks unmatched items, missing product codes, unexpected scans, over-scans, and missing quantities.
4. Only valid scan sets allow transition to packed.

## Notifications

1. Status change or payment reminder creates a queue job with idempotency data.
2. Worker or inline processing sends through Whatomate/Cloud API.
3. Result is written to queue job, WhatsApp log, and activity log.
