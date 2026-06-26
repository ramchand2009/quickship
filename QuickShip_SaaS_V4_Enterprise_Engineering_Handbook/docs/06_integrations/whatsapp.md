# WhatsApp

WhatsApp messaging is implemented through `core.whatomate` and `core.whatsapp_queue`.

## Configuration Sources

- Environment variables: `WHATOMATE_*`, `WHATSAPP_ALERT_*`.
- Database row: `WhatsAppSettings`.
- Status template rows: `WhatsAppStatusTemplateConfig`.
- Synced templates: `WhatsAppTemplate`.

## Supported Modes

- Text message for accepted-order fallback.
- Template messages using template name/id and mapped placeholders.
- Whatomate-style API endpoints.
- Libromi/WhatsApp Cloud API-compatible endpoints.
- Direct Meta Cloud API when configured with phone number id.

## Queue and Logging

Status changes, resends, and payment reminders enqueue `WhatsAppNotificationQueue` jobs with idempotency keys. Processing writes request/response payloads, external message ids, status, retries, and errors. `WhatsAppNotificationLog` stores the auditable send/webhook record.

Operators can process the queue now, retry failed jobs, resend per order, bulk resend, and view/export delivery logs.
