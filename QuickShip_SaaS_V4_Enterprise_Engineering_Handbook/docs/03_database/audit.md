# Audit

Audit coverage is model-backed and exportable.

- `OrderActivityLog` records order-related operational events: status change, manual update, WhatsApp queue events, WhatsApp webhook, label printed, stock deducted/restored, and stock warnings.
- `WhatsAppNotificationLog` records send attempts, delivery status, webhook event id, request/response payloads, idempotency key, external message id, trigger, and success/error state.
- `StockMovement` records stock before/after, quantity delta, movement type, SKU/barcode snapshots, optional order link, reference key, actor, notes, and issue details.
- CSV export endpoints exist for order management, WhatsApp delivery logs, and audit export.
- Runtime diagnostics include webhook diagnostics, system status cards, health endpoint, metrics endpoint, integration smoke, and preflight management commands.

Audit is not immutable at the database permission level; it is append-oriented by application convention.
