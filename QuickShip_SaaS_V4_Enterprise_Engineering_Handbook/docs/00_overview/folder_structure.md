# Folder Structure

- `Ram_codex1/`: Django project settings, URL root, ASGI/WSGI.
- `core/`: main app with models, views, forms, access rules, service helpers, queue logic, monitoring, and management commands.
- `core/management/commands/`: operational commands for roles, backups, smoke checks, preflight checks, queue workers, cleanup, and fresh-start utilities.
- `core/migrations/`: Django migrations. Current model history includes order workflow, WooCommerce settings, web push, product pricing, and stock/order operational fields.
- `templates/`: server-rendered UI templates.
- `templates/core/`: dashboards, order management, order detail, packing, labels, stock, WhatsApp, webhook, admin utility, and ops mobile templates.
- `templates/registration/`: login and signup.
- `templates/pwa/`: offline page.
- `static/`: theme assets, PWA icons, custom CSS and JavaScript.
- `media/`: runtime uploaded product images.
- `docker/`: container entrypoint script.
- `scripts/`: Windows helper scripts for worker/backup task registration.
- `QuickShip_SaaS_V4_Enterprise_Engineering_Handbook/`: AI memory and engineering documentation.
- `DEPLOY_EASYPANEL.md` and `DEPLOY_HOSTINGER_VPS.md`: deployment guides.

The codebase is intentionally compact: most user-facing behavior currently sits in `core/views.py`, with domain helpers split into service modules.
