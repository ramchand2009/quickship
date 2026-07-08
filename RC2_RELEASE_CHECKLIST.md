# RC2 Release Checklist

Date: 2026-07-08

## Scope

RC2 is a production-hardening checkpoint for the Django SaaS multi-vendor WhatsApp ordering platform. The goal is to reduce the highest-risk production failure modes before continuing toward RC3 API work.

## Hardening Completed

- Tenant and vendor operations hardened.
  - Activity logs now keep the related order tenant.
  - WhatsApp runtime settings are tenant-scoped.
  - Vendor roles allow owner/operator actions while keeping vendor viewers read-only.
  - Print/label sender fallbacks avoid crashes when sender address data is missing.

- WooCommerce webhook and order import idempotency hardened.
  - WooCommerce imports no longer fall back to the wrong tenant for unmapped orders.
  - WooCommerce order identity is tenant-scoped.
  - Replayed WooCommerce webhooks update the existing order instead of creating duplicates.
  - Added migration `core.0059_unique_tenant_woocommerce_order`.

- Stock movement idempotency hardened.
  - Repeated accept/reconcile attempts do not deduct stock twice.
  - Repeated cancel/restore attempts do not restore stock twice.
  - Stock movement `reference_key` collision handling is race-safe.

- WhatsApp queue idempotency hardened.
  - Duplicate active queue jobs are blocked per tenant/idempotency key.
  - Already successful notifications are not resent by the worker.
  - Failed jobs remain retryable/requeueable according to existing rules.
  - Added migration `core.0060_unique_active_whatsapp_queue_idempotency`.

- Migration safety audit added.
  - `python manage.py audit_rc2_migration_safety`
  - Reports duplicate WooCommerce order groups and duplicate active WhatsApp queue groups.
  - Supports `--strict` for deploy blocking.

- Preflight deploy gate updated.
  - `python manage.py preflight_check` now includes the RC2 duplicate-data audits.

## Validation Commands Run

- `python manage.py check`
  - Result: passed, no issues.

- `python manage.py preflight_check`
  - Result: passed locally.
  - Notes: warned that `DEBUG` is enabled, `CSRF_TRUSTED_ORIGINS` is empty, and `WHATOMATE_WEBHOOK_TOKEN` is missing in local environment.

- `python manage.py audit_rc2_migration_safety --strict`
  - Result: passed.
  - Duplicate WooCommerce order groups: 0.
  - Duplicate active WhatsApp queue groups: 0.

- `python manage.py migrate`
  - Result: passed.
  - Applied `core.0059_unique_tenant_woocommerce_order`.
  - Applied `core.0060_unique_active_whatsapp_queue_idempotency`.

- Broad RC2 regression group:
  - `core.tests.WooCommerceSyncTests`
  - `core.tests.WhatsAppQueueProcessingTests`
  - `core.tests.WhatsAppTenantIsolationTests`
  - `core.tests.TenantFoundationTests`
  - `core.tests.RoleAccessTests`
  - `core.tests.PreflightCheckCommandTests`
  - `core.tests.Rc2MigrationSafetyAuditCommandTests`
  - Result: 175 tests passed.

## Deploy Steps

1. Backup database.
2. Run `python manage.py audit_rc2_migration_safety --strict` before migration.
3. Run `python manage.py preflight_check --strict`.
4. Apply migrations with `python manage.py migrate`.
5. Restart application workers/web process.
6. Run a smoke test:
   - WooCommerce webhook health.
   - Order list/detail access for vendor tenant.
   - Order accept stock deduction.
   - WhatsApp queue enqueue/process.
   - Label/packing print page.

## Rollback Notes

- Code rollback can revert to the previous deployment artifact.
- Database rollback is more sensitive because RC2 includes uniqueness constraints.
- If rollback is required after migrations:
  - Stop app traffic first.
  - Take a fresh database backup.
  - Roll back code.
  - Only reverse migrations after checking whether newer duplicate-protected writes happened.
  - Avoid deleting data automatically; inspect duplicate/order/queue rows manually.

## Known Remaining Risks

- Full test suite still contains older stale workflow expectations outside the RC2 stable regression group.
- Production environment must set `DEBUG=False`, proper `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and webhook secrets.
- `core.views.py` remains large and should be decomposed during later architecture work.
- Background work still runs through management/inline queue paths; Celery or a managed worker model remains an RC3/RC4 concern.
- Android API surface is not yet implemented.

## RC2 Commit Checkpoints

- `3b4662a Harden RC2 tenant and vendor operations`
- `b00b9a9 Harden WooCommerce webhook idempotency`
- `0cb9457 Harden stock movement idempotency`
- `ba0d471 Harden WhatsApp queue idempotency`
- `d71465a Add RC2 migration safety audit`
- `4ed1f09 Gate preflight on RC2 migration audit`

## Next Recommended RC3 Work

- Define REST API versioning and authentication strategy.
- Add current-user/current-tenant API endpoints.
- Add Android MVP order list/detail/update APIs.
- Add packing scan API.
- Add product and stock summary APIs.
- Add API tests and tenant-isolation tests for every endpoint.
