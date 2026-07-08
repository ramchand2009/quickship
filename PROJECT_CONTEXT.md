# Project Context

This file is the working memory for future Codex sessions. Read it before planning or implementing changes so the project does not need to be explained again.

## Product

- Project: `Ram_codex1`
- Main app: `core`
- Stack: Django monolith, PostgreSQL, Docker deployment
- Stage: code completed, deployed, production testing/hardening phase
- Business: multi-vendor SaaS for WhatsApp-driven product ordering and vendor operations
- Main users: platform superadmin, vendor owner, vendor operator, vendor viewer, warehouse/packing users
- Vendor usage model: mobile-first; vendors should be able to handle daily work from phone screens

## Operating Model

- WooCommerce is platform-owned and shared across vendors.
- WhatsApp/Whatomate sender is platform-owned and shared across vendors.
- Vendors do not connect their own WooCommerce store.
- Vendors do not connect their own WhatsApp number.
- Vendors add/manage their products in the platform/shared catalog flow.
- Orders from the shared WooCommerce store must be routed to the correct vendor by product ownership, WooCommerce product IDs, SKU/category/tag/product mapping rules, or equivalent routing metadata.
- Vendor-facing UI should say product routing, product mapping, shared store, and shared sender. Avoid language like "connect your WooCommerce" or "connect your WhatsApp" for vendors.

## Current Architecture

- Django monolith with a large `core` app.
- `core.views.py` is still large and should be changed carefully.
- Tenant scoping is implemented through `Tenant`, `TenantMembership`, active tenant helpers, and tenant fields on main business models.
- Core domains:
  - Orders
  - Inventory/products/stock
  - Packing and shipping labels
  - Vendors/tenants/RBAC
  - WooCommerce sync and webhooks
  - WhatsApp notifications and queue
  - Shiprocket sync
  - Audit logs/activity logs
  - Expenses and vendor settlement
  - Dashboard/reports
  - Monitoring/preflight checks

## Production Rules

- Production is already deployed/testing. Do not rewrite broad workflows.
- Prefer small, safe, reversible changes.
- Preserve existing UI and current vendor workflow unless the change explicitly targets UI.
- Before implementing, inspect and confirm what is already implemented.
- Add focused tests for every risky fix.
- Run relevant tests and `manage.py check`.
- Commit each safe slice separately.
- Do not commit unrelated files.
- Existing untracked file `TODAY_SUMMARY_2026-06-23.md` has been left untouched.

## Recent Completed Work

- `3b4662a` Harden RC2 tenant and vendor operations
- `b00b9a9` Harden WooCommerce webhook idempotency
- `0cb9457` Harden stock movement idempotency
- `ba0d471` Harden WhatsApp queue idempotency
- `d71465a` Add RC2 migration safety audit
- `4ed1f09` Gate preflight on RC2 migration audit
- `773242a` Add RC2 release validation checklist
- `a1526be` Add vendor product routing health card
- `ae4bc84` Add shared store routing diagnostics
- `b12cfe3` Add vendor product routing detail panel
- `b316567` Add vendor WhatsApp delivery health card
- `e392c71` Harden shared WooCommerce order routing
- Pending/current: make order status updates queue WhatsApp without inline sending by default

## Latest Implemented Slice

Commit `a1526be` added a mobile vendor product routing health card to `order_management_ops.html`.

It shows:
- active products
- route-ready product IDs
- recent WooCommerce routed orders
- pickup address readiness
- shared WooCommerce store status
- shared WhatsApp sender status

It also added a tenant-scoping test so another vendor's products/orders/rules do not leak into the card.

Commit `ae4bc84` added a superadmin Shared Store Routing Control Room to `tenant_mapping_health.html`.

It shows:
- products with no active route
- products that match the wrong vendor route
- products that match multiple vendor routes
- vendor-level risk status for shared WooCommerce routing
- missing order product identifiers in the existing mapping health workflow

It also added tests proving the page flags no-route, wrong-vendor, and ambiguous-route cases.

Commit `b12cfe3` added a mobile product routing detail panel to `stock_product_detail_ops.html`.

It shows:
- shared WooCommerce store status as platform-managed
- route-ready status for the product
- WooCommerce product/variation identifiers
- matching vendor routing rules
- tenant-scoped product detail access

It also added tests proving vendors see only their own product routing details and cannot open another tenant's product detail.

Commit `b316567` added a mobile vendor WhatsApp delivery health card to `order_management_ops.html`.

It shows:
- shared WhatsApp sender status as platform-managed
- failed WhatsApp queue count
- pending WhatsApp queue count
- retrying WhatsApp queue count
- safe vendor wording that hides payloads and API credentials

It also added tests proving vendors see only their own tenant's WhatsApp queue counts and do not see raw errors, payloads, API keys, or admin log links.

Commit `e392c71` hardened shared WooCommerce order routing.

It changed:
- mixed-vendor WooCommerce orders are no longer silently assigned to the first matching vendor
- existing product lookup now checks WooCommerce product ID and variation ID, not only legacy product ID/SKU
- variation line items can route by mapped variation ID

It also added regression tests for mixed-vendor order skip, variation-ID routing, and existing product Woo ID routing.

Current implementation slice: status-change WhatsApp delivery is being made queue-first for faster mobile UI.

Design:
- all order status updates should still save the local status immediately
- stock sync remains in the request
- WhatsApp notification queue creation remains in the request
- inline WhatsApp sending for status changes is disabled by default
- the old inline status-send behavior is available only with `WHATSAPP_INLINE_STATUS_SEND_ENABLED=True`

## Roadmap Direction

Near-term priorities:
- Continue mobile vendor UI hardening.
- Keep shared WooCommerce/shared WhatsApp model clear in UI.
- Keep status-update UI fast by queueing external notifications instead of waiting on WhatsApp sends.
- Keep improving product routing visibility and admin mapping diagnostics.
- Add Android-ready REST API later as RC3, not before the current UI/production hardening is stable.
- Keep tenant isolation and idempotency as highest-risk areas.

Likely next safe slices:
- Start API design only after current production UI flow is stable.

## How To Continue

When the user says "next":
- Review this file.
- Check `git status --short`.
- Inspect the relevant current code before recommending or implementing.
- Propose one small safe production slice.

When the user says "ok implement":
- Re-confirm what already exists in the code path.
- Implement only the current slice.
- Add focused tests.
- Run checks/tests.
- Commit separately with a clear message.
