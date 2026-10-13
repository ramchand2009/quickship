# India Post sandbox testing

The super-admin-only page is `/post-office/sandbox/`. It calls only the India Post UAT host
`https://test.cept.gov.in/beextcustomer` and stores redacted check results. It does
not connect booking or tracking to the mobile app or the live order workflow.

Set these server environment variables before testing:

- `INDIA_POST_SANDBOX_USERNAME` and `INDIA_POST_SANDBOX_PASSWORD`: UAT login.
- `INDIA_POST_SANDBOX_CUSTOMER_ID`: customer ID assigned for UAT booking.
- `INDIA_POST_WEBHOOK_TOKEN`: long, random shared secret for inbound events.
- `INDIA_POST_SANDBOX_EVENT_PATH`: optional path (default `/event/download`). The
  supplied document does not identify the full event request contract. Confirm
  its method, filters and path with India Post.

The public receiver is `https://<your-host>/post-office/webhook/`. India Post
must send `X-India-Post-Webhook-Token` with the configured secret. The supplied
document gives an example event payload but no authentication mechanism; this
header arrangement must be agreed during onboarding. The endpoint accepts a
JSON object with `article_number`, `event_code`, and `event_date`. Repeated events
are acknowledged without duplicate storage. No order status is changed by UAT
webhooks.

After migrating the database, run each check with assigned UAT data. The
booking form is a starter payload only: replace the placeholder IDs and AWB and
complete any fields India Post requires for your product. The API may return a
partial result with per-article errors even when the HTTP status is 200; the
page records that as `partial` or `failed`. Download the evidence JSON after
testing, review it, and include the successful API responses, webhook delivery
proof, static egress IP and sign-off contacts in the production approval
submission. A green checklist item means this environment received a response;
it does not substitute for India Post's approval.
