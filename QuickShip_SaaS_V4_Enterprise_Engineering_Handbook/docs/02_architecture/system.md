# System

The system is a monolithic Django application with one primary app, `core`.

## Runtime Shape

- Browser clients hit Django views under `core/urls.py`.
- Views render Django templates for desktop and mobile operations.
- Domain state is stored in Django models in the default database.
- External systems are called from service modules using Python standard-library HTTP clients.
- Long-running or scheduled work is handled by management commands, not Celery.

## Main Subsystems

- Order dashboard and management: `core.views`, `ShiprocketOrder`, order templates.
- Stock and packing: `core.stock`, `Product`, `StockMovement`.
- WooCommerce sync: `core.woocommerce`, `WooCommerceSettings`.
- Shiprocket sync: `core.shiprocket`.
- WhatsApp messaging: `core.whatomate`, `core.whatsapp_queue`, `WhatsApp*` models.
- Activity/audit: `core.activity`, `OrderActivityLog`, export views.
- Monitoring: `core.monitoring`, `core.system_status`, `/healthz/`, `/metrics/`.
- PWA/push: manifest, service worker, `WebPushSubscription`.

## Source of Truth

Local fulfilment state is `ShiprocketOrder.local_status`. External statuses are imported from WooCommerce/Shiprocket and optionally synchronized back to WooCommerce, but local workflow drives stock, packing, labels, notifications, and dashboard metrics.
