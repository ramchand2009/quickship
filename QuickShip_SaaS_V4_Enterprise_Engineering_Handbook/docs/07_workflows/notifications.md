# Notifications

Notifications are centered on WhatsApp and optional browser push.

## WhatsApp

- Status changes enqueue notification jobs.
- Operators can resend single or multiple order messages.
- Payment reminders enqueue a dedicated WhatsApp template message.
- Failed jobs can be retried.
- Queue processing can run inline from the UI or through management commands.
- Delivery logs show success/failure, delivery status, request/response payloads, and webhook data.

## Push Notifications

The app exposes Web Push config and subscription endpoints. New WooCommerce order polling/push support is present through PWA service worker and VAPID settings.

## Alerts

WhatsApp queue alert thresholds and cooldowns are configured through `WHATSAPP_ALERT_*` settings and supporting management commands.
