# WhatsApp

WhatsApp messaging is implemented through `core.whatomate` and `core.whatsapp_queue`.

## Configuration Sources

- Environment variables: `WHATOMATE_*`, `WHATSAPP_ALERT_*`.
- Tenant database row: `WhatsAppSettings`.
- Tenant status template rows: `WhatsAppStatusTemplateConfig`.
- Tenant synced templates: `WhatsAppTemplate`.

The default Mathukai tenant can still use environment fallback values for backward compatibility. Non-default vendor tenants load only their own `WhatsAppSettings` row and do not fall back to Mathukai/global credentials.

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
