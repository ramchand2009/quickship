# Migrations

Migrations live in `core/migrations/`. The current history includes:

- Initial site/project/contact/order models.
- Shiprocket order workflow fields and status timestamps.
- Sender address and 4x6 shipping label support.
- WhatsApp settings, templates, notification logs, and queue jobs.
- Product, category, stock movement, stock issue, and product image fields.
- WooCommerce settings, source fields, webhook secret, web push subscriptions.
- Packing, tracking, shipping cost, payment, and profit support fields.

When changing models:

- Add a Django migration with `python manage.py makemigrations core`.
- Keep data migrations explicit and reversible when practical.
- Be careful with unique nullable fields like product barcode and external product id.
- Test migrations with `python manage.py migrate`.
