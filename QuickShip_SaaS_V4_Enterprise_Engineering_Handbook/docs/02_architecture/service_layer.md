# Service Layer

The current service layer is module-based rather than class-based.

- `core.stock`: product matching, stock availability, profit summaries, packing scan requirements, packing scan validation, manual stock movements, special stock issues, automatic order stock deduction/restore, missed-deduction reconciliation.
- `core.woocommerce`: WooCommerce configuration, API requests, order import, product sync, product update, local-to-WooCommerce status mapping, webhook secret lookup.
- `core.shiprocket`: Shiprocket authentication, order fetch, payload normalization, import of new-like orders.
- `core.whatomate`: WhatsApp/Whatomate runtime config, text/template sending, template sync, Cloud API compatibility, status notification plans, payment reminder plans.
- `core.whatsapp_queue`: notification enqueueing, idempotency, processing, retry behavior.
- `core.activity`: helper for writing `OrderActivityLog`.
- `core.monitoring` and `core.system_status`: health payloads, operational counters, heartbeat reads/writes.
- `core.queue_alerts`: WhatsApp queue alert testing and queue failure notification support.

Views should call these modules instead of duplicating integration or stock logic inline. Existing views still contain orchestration and UI context building, so refactors should be incremental and covered by tests.
