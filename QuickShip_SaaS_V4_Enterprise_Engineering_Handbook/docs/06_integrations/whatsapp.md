# WhatsApp

WhatsApp messaging is implemented through `core.whatomate` and `core.whatsapp_queue`.

## Configuration Sources

- Environment variables: `WHATOMATE_*`, `WHATSAPP_ALERT_*`.
- Shared platform database row: `WhatsAppSettings`.
- Tenant status template rows: `WhatsAppStatusTemplateConfig`.
- Tenant synced templates: `WhatsAppTemplate`.

All vendors currently send through the same Libromi/WhatsApp number. Non-default vendor queue jobs and logs keep their tenant for audit and dashboard isolation, but runtime sends load the shared/default `WhatsAppSettings` row and environment fallback values.

Template names/languages and status template configs are unique per tenant, so two vendors can use the same approved WhatsApp template names independently.

## Supported Modes

- Text message for accepted-order fallback.
- Template messages using template name/id and mapped placeholders.
- Whatomate-style API endpoints.
- Libromi/WhatsApp Cloud API-compatible endpoints.
- Direct Meta Cloud API when configured with phone number id.

## Queue and Logging

Status changes, resends, and payment reminders enqueue `WhatsAppNotificationQueue` jobs with idempotency keys. Processing writes request/response payloads, external message ids, status, retries, and errors. `WhatsAppNotificationLog` stores the auditable send/webhook record.

Queue jobs and delivery logs carry tenant. Queue workers can process all pending jobs or only one tenant's jobs, and duplicate/idempotency checks are tenant-scoped.

Operators can process the queue now, retry failed jobs, resend per order, bulk resend, and view/export delivery logs. Vendor-scoped requests see tenant-filtered logs/jobs; super admin/platform requests remain cross-tenant unless a later super-admin tenant switch is added.
