# QuickShip / Mathukai Operations Dashboard

QuickShip is a Django operations dashboard for Mathukai order fulfilment. It centralizes WooCommerce and Shiprocket-style orders, local order status workflow, stock control, packing verification, shipping-label printing, WhatsApp customer updates, profit reporting, and operational diagnostics.

## Current Product Scope

- Desktop admin dashboard for order, stock, WhatsApp, webhook, system, and utility management.
- Mobile operations UI for day-to-day order acceptance, packing, shipping, and queue work.
- WooCommerce order and product sync with local order/product models.
- Legacy Shiprocket order sync support for new-order imports.
- Local fulfilment workflow: new order, accepted, packed, shipped, delivery issue, out for delivery, delivered, completed, cancelled.
- Stock movement ledger for manual adjustments, special issues, order acceptance deductions, and cancellation restores.
- Packing checks using SKU/barcode scan requirements before packing.
- Shipping labels and bulk packing/shipping print flows.
- WhatsApp notification queue, logs, template configuration, retries, webhooks, and payment reminders.
- Profit calculation based on order line revenue minus local product actual cost.
- Health, metrics, audit export, smoke checks, preflight checks, backup/restore utilities, and runtime cleanup commands.

## Technology Stack

- Python / Django app: `Ram_codex1` project with single main app `core`.
- Database: SQLite for local default; PostgreSQL supported through `DATABASE_URL` or `POSTGRES_*`.
- Templates: Django templates under `templates/`, including desktop and ops-mobile variants.
- Static/media: WhiteNoise for static files, `staticfiles` collect target, `media/product-images` for uploaded product images.
- Integrations: WooCommerce REST API, Shiprocket API, Whatomate/WhatsApp Cloud API-compatible messaging, Web Push VAPID.
- Deployment: Dockerfile, Docker Compose, Easypanel guide, Hostinger VPS guide.

## Architecture Summary

The app is currently a monolithic Django application. Views orchestrate forms, service modules, and models. Core domain behavior lives in `core/stock.py`, `core/woocommerce.py`, `core/shiprocket.py`, `core/whatomate.py`, `core/whatsapp_queue.py`, `core/activity.py`, and `core/system_status.py`.

Orders are stored in `ShiprocketOrder` even when the source is WooCommerce. Local status is the source of truth for fulfilment. External status is synchronized back to WooCommerce when applicable. Stock is tracked on `Product` and audited through `StockMovement`. Operational history is recorded in `OrderActivityLog`; WhatsApp delivery and queue state are recorded separately.

## Current Reality Notes

- This is not yet a true multi-tenant SaaS codebase. There is no tenant model or tenant foreign key on domain data.
- The AI handbook folder documents a future enterprise direction, but the live code is a single-business operations dashboard.
- Role boundaries exist through Django groups: `admin` and `ops_viewer`.
- The untracked `TODAY_SUMMARY_2026-06-23.md` file is local and not part of committed project documentation.
