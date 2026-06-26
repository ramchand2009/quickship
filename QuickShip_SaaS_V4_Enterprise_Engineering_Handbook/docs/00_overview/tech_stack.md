# Tech Stack

## Backend

- Python with Django.
- Project package: `Ram_codex1`.
- Main app: `core`.
- Django auth, groups, sessions, messages, admin, and templates.
- ReportLab for shipping label PDF generation.
- WhiteNoise for static file serving.

## Database

- Default local database: SQLite at `db.sqlite3`.
- Production database support: PostgreSQL via `DATABASE_URL` or `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`.
- JSON fields are used for external order payloads, billing/shipping addresses, order items, webhook payloads, queue payloads, and template mappings.

## Frontend

- Server-rendered Django templates.
- Bootstrap-like admin theme assets under `static/assets`.
- Mobile ops templates use custom CSS in templates and shared partials.
- PWA support includes manifest, service worker, offline page, icons, and optional Web Push.

## Integrations

- WooCommerce REST API for orders, products, order status updates, and webhooks.
- Shiprocket API for legacy/new-order import.
- Whatomate / WhatsApp Cloud API-compatible message sending, templates, webhooks, queueing, and delivery logs.
- Web Push through VAPID keys.

## Deployment

- `Dockerfile` and `docker-compose.yml` exist for container deployment.
- `DEPLOY_EASYPANEL.md` documents Easypanel Git/Dockerfile deployment.
- `DEPLOY_HOSTINGER_VPS.md` documents VPS Docker deployment.
- Runtime configuration is environment-driven with `.env` local loading.
