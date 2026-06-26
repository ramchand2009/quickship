# Coding Guidelines

- Preserve existing Django patterns: forms validate workflow rules, views orchestrate actions, service modules hold integration/domain helpers, templates render desktop/mobile variants.
- Do not bypass `ShiprocketOrder.ALLOWED_STATUS_TRANSITIONS` or form validation when changing order workflow.
- Keep local fulfilment status separate from external WooCommerce/Shiprocket status.
- Use `core.stock` helpers for stock deduction, restore, packing requirements, scan validation, and profit calculations.
- Use `core.activity.log_order_activity` for operational events that matter to support or audit.
- Use `enqueue_whatsapp_notification` rather than directly sending status notifications from views.
- Keep idempotency keys and stock `reference_key` behavior intact.
- Treat `actual_price` as the cost basis for profit. Missing product mapping or missing actual price should make profit incomplete, not inflated.
- When adding data fields, include migrations and tests for the affected workflow.
- For docs-only tasks, do not edit application code.
