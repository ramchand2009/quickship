# Mathukai Mobile Phase 1 Delivery Plan

**Status:** Approved

**Approved:** 19 July 2026

**Inputs:** Approved Phase 1 PRD, validated OpenAPI contract, approved data model,
and approved runtime architecture.

This document defines implementation order and quality gates. It does not begin
implementation or authorize production deployment.

## 1. Delivery strategy

Deliver Phase 1 through small, independently reviewable slices. Keep the
existing Django PWA available throughout development and rollout. Introduce
read-only API capability before mobile writes, and prove server-side tenant
isolation before connecting a mobile build to production-like data.

Every slice must:

- Have focused acceptance criteria.
- Include regression and tenant-isolation tests proportional to risk.
- Pass `manage.py check` and relevant existing tests.
- Keep the OpenAPI contract synchronized.
- Avoid unrelated changes.
- Have an explicit rollback or disable path.
- Be committed separately after verification.

## 2. Preconditions

Before implementation begins, supply or create:

- Company-owned Expo account with multi-factor authentication.
- Company-owned Google Play and Apple Developer accounts.
- Company-owned Firebase project for Android notifications.
- Staging HTTPS domain and production HTTPS domain.
- Company-owned monitoring project.
- Final logo, icon, splash assets, and approved brand colors.
- Named owners for signing credentials, backups, store submission, and incident
  response.

Development may begin with placeholder brand assets, but package IDs, bundle
IDs, signing ownership, and environment separation must be fixed before the
first shared build.

## 3. Superseded TWA prototype

The earlier Trusted Web Activity prototype is not the production mobile
architecture.

During the first implementation slice:

- Remove the Bubblewrap-specific `mobile/android` packaging workspace.
- Retain the general concept of website/application association.
- Change the Android association package from `com.mathukai.dashboard` to the
  approved `com.mathukai.operations`.
- Adapt `assetlinks.json` for React Native Android App Links.
- Add the Apple App Site Association endpoint for iOS Universal Links.
- Keep association fingerprints/team identifiers environment-configurable.

No signing key, provisioning profile, APK, AAB, IPA, push credential, or store
secret is committed.

## 4. Milestone 0 - implementation readiness

### Deliverables

- Record the approved architecture status.
- Select dependency versions compatible with the stable Expo SDK at kickoff.
- Confirm Python JWT signing/verification library and security review.
- Confirm API hostname and mobile deep-link hostname per environment.
- Define application variants:
  - `com.mathukai.operations.dev`
  - `com.mathukai.operations.staging`
  - `com.mathukai.operations`
- Add a mobile-specific environment and secrets inventory.
- Remove or adapt the superseded TWA prototype as described above.

### Gate 0

- No unresolved production identifier conflict.
- No credentials in Git history or working tree.
- Development, staging, and production cannot accidentally share databases or
  push credentials.
- Architecture documents and OpenAPI pass review.

### Rollback

Documentation/configuration-only. Revert the slice without affecting runtime
traffic.

## 5. Milestone 1 - API foundation

### Slice 1.1: Django REST foundation

- Add Django REST Framework.
- Mount API URLs under `/api/v1` without expanding `core/views.py`.
- Add common response metadata and request IDs.
- Add standard exception mapping and error codes.
- Add cursor pagination.
- Add API throttling policies.
- Add OpenAPI schema publication in non-production or protected environments.
- Add contract linting to CI.

### Tests

- Anonymous access is denied by default.
- Request IDs appear on success and failure.
- Error envelopes match the OpenAPI contract.
- Pagination cursors reject malformed or tampered values.
- API does not expose Django debug details.
- Existing web URLs and tests remain unchanged.

### Gate 1

- OpenAPI validation has no errors or warnings.
- `manage.py check` passes.
- API foundation tests pass.
- Existing authentication and tenant tests pass.

### Rollback

Remove the unreferenced API URL include and dependency. No business tables or
mobile clients depend on it yet.

## 6. Milestone 2 - mobile authentication and tenant security

### Slice 2.1: tenant-scoped warehouse role

- Add `warehouse_operator` to tenant membership choices.
- Create a safe migration and explicit membership bootstrap procedure.
- Do not automatically give a legacy global group access to every tenant.
- Preserve the existing web flow during transition.

### Slice 2.2: session and refresh-token persistence

- Add `MobileSession` and `MobileRefreshToken` models.
- Store only refresh-token hashes.
- Implement 10-minute access tokens and 30-day rotating refresh tokens.
- Add reuse detection, revocation and expiry cleanup.
- Validate active user and active tenant membership during protected requests.

### Slice 2.3: authentication endpoints

- Implement login, refresh, logout, current session and tenant selection.
- Reuse the current login lockout policy.
- Return normalized permissions and memberships.
- Ensure a session without active tenant can call only auth/selection endpoints.

### Tests

- Valid login and failed login behavior.
- Lockout and throttling.
- No raw refresh token stored or logged.
- Refresh rotation invalidates the previous token.
- Reuse revokes the token family.
- Logout is idempotent.
- Disabled users cannot refresh.
- Removed membership stops tenant access.
- Tenant switching cannot select another user's tenant.
- Warehouse membership is tenant-scoped.
- Concurrent refresh requests have one valid winner.

### Gate 2

- Security review of token claims, signing keys, rotation and logging.
- Migration safety audit passes.
- Authentication and tenant-isolation suites pass.
- No mobile business endpoint is enabled until this gate passes.

### Rollback

- Feature flag mobile authentication endpoints off.
- Revoke all mobile sessions.
- Keep additive tables until a later cleanup migration.
- Existing web sessions remain unaffected.

## 7. Milestone 3 - read-only Phase 1 API

### Slice 3.1: current session and dashboard

- Implement `/auth/me` and role-aware `/dashboard`.
- Return only permitted cards, alerts and actions.
- Add bounded queries and indexes proven by query-plan review.

### Slice 3.2: orders read API

- Implement cursor-paginated order list.
- Add search and approved filters.
- Implement order detail and field-level customer masking.
- Return server-calculated `allowed_actions`.
- Return an opaque order version.
- Reuse existing tenant scoping and status labels.

### Slice 3.3: products and stock read API

- Implement product list and detail.
- Implement read-only stock movement list.
- Return current stock state, routing readiness and update timestamps.
- Apply role-based price visibility.

### Tests

- Cross-tenant object IDs never return another tenant's data.
- Search cannot leak masked customer fields.
- Owner, operator, viewer and warehouse field matrices.
- Pagination stability under new records.
- Status and stock labels match existing domain constants.
- Query counts remain bounded per page.
- Responses conform to OpenAPI schemas.

### Gate 3

- Full tenant-isolation matrix passes.
- Representative page responses meet performance targets.
- Read-only endpoints are approved against staging data.
- No write endpoints are exposed.

### Rollback

Disable read endpoints through API feature flags. The web application remains
the operational client.

## 8. Milestone 4 - Expo application foundation

### Slice 4.1: repository and build foundation

- Create `mobile/app` using stable Expo, React Native and TypeScript versions.
- Configure Expo Router and development builds.
- Configure development, staging and production variants.
- Configure EAS Build without committing credentials.
- Add linting, formatting, type checking and test commands.

### Slice 4.2: shared mobile infrastructure

- Generate or validate the typed client from OpenAPI.
- Add request IDs and standard error handling.
- Add TanStack Query.
- Add SecureStore refresh-token adapter.
- Add partitioned read-only cache and 24-hour purge policy.
- Add crash reporting with privacy-safe filtering.
- Establish accessible design tokens and shared components.

### Tests

- TypeScript strict mode passes.
- API models compile from the contract.
- Environment builds cannot point to the wrong API host.
- Secure storage never falls back to plaintext storage.
- Tenant switch and logout purge cached queries.
- Error views display request IDs without sensitive data.

### Gate 4

- Android and iOS development builds install on physical devices.
- Staging build connects only to staging API.
- No secret is present in JavaScript bundles or build logs.
- Base accessibility and navigation review passes.

### Rollback

Mobile code is not yet distributed publicly; remove the build or revoke its
development credentials.

## 9. Milestone 5 - authentication mobile experience

### Deliverables

- Login screen.
- Secure session restoration.
- Single-flight token refresh.
- Tenant-selection screen.
- Permission-aware main navigation.
- Logout and local-data purge.
- Forced return to Login for revoked/expired sessions.

### Tests

- Correct and incorrect credentials.
- Multiple-tenant and single-tenant users.
- Refresh during concurrent API requests.
- Session revocation while app is backgrounded.
- Offline startup with and without a valid cached session.
- Screen-reader labels, keyboard handling and font scaling.

### Gate 5

- Authentication passes on physical Android and iOS devices.
- Security review confirms token and cache behavior.
- No protected screen renders data from a prior tenant.

## 10. Milestone 6 - dashboard, orders and stock screens

### Slice 6.1: dashboard

- Operational cards, alerts, quick actions and pull-to-refresh.
- Cached state with visible update age.
- Loading, empty, offline, permission and failure states.

### Slice 6.2: order list and detail

- Search, filters, cursor pagination and refresh.
- Detail sections, allowed actions and activity history.
- Customer masking exactly matching API permissions.
- Deep-link destination resolution after authentication.

### Slice 6.3: stock

- Read-only product list and filters.
- Product detail, permitted prices, routing readiness and recent movements.
- Cached-state timestamp and no stock mutation controls.

### Tests

- Component tests for all screen states.
- Pagination and search interaction tests.
- Tenant switch invalidates visible data.
- Deep links cannot reveal unauthorized objects.
- Poor-network behavior retains safe cache.
- Android and iOS visual/accessibility review.

### Gate 6

- Vendor owner, operator, viewer and warehouse acceptance passes.
- No Phase 2 Scan destination or packing completion action is present.
- Read-only beta is acceptable before write capability is enabled.

## 11. Milestone 7 - safe order writes

### Slice 7.1: shared order mutation service

- Extract current web order transition orchestration behind regression tests.
- Keep stock, audit, WooCommerce and WhatsApp behavior equivalent.
- Make the existing web flow call the shared service.
- Do not change status behavior during extraction.

### Slice 7.2: order version and idempotency

- Add atomic order version.
- Update every relevant web mutation to increment it.
- Add `ApiIdempotencyRecord` and cleanup.
- Add request hash conflict handling and semantic replay.

### Slice 7.3: mobile status and payment endpoints

- Implement permitted status updates.
- Exclude mobile `order_packed` initiation.
- Implement authorized payment received.
- Queue external work after commit.
- Return safe effect states and updated order.

### Slice 7.4: mobile write UI

- Render actions only from `allowed_actions`.
- Add confirmation and required reason fields.
- Keep one idempotency key per user intent.
- Handle conflicts with explicit reload/review.
- Never apply an optimistic status change.

### Tests

- Existing web status regression suite.
- Every allowed and forbidden transition.
- Owner/operator allowed; viewer/warehouse denied.
- Cross-tenant writes denied.
- Duplicate same-key requests mutate once.
- Same key/different payload returns conflict.
- Concurrent version updates have one winner.
- Stock and activity effects occur once.
- WhatsApp/WooCommerce failure does not corrupt committed order state.
- Payment received is idempotent and versioned.

### Gate 7

- Full web and API order test suites pass.
- Production-like concurrency test passes.
- Tenant isolation and stock idempotency are independently reviewed.
- Feature flags default mobile writes off until staging approval.

### Rollback

- Disable mobile writes while keeping read API and app functional.
- Continue using the shared service from web only.
- Do not roll back additive version/idempotency schema during an incident.

## 12. Milestone 8 - notifications and deep links

### Slice 8.1: persistence and preferences

- Add mobile device, notification, delivery and preference models.
- Encrypt Expo tokens and store lookup hashes.
- Implement registration, disable, inbox, read and preference endpoints.

### Slice 8.2: delivery worker

- Create deduplicated recipient inbox records.
- Queue delivery after transaction commit.
- Send minimal payloads through Expo.
- Check receipts and disable permanent invalid tokens.
- Add bounded retry and monitoring.

### Slice 8.3: mobile integration

- Request push permission contextually.
- Register/refresh token after authentication.
- Add notification inbox and unread state.
- Add internal deep links, Android App Links and iOS Universal Links.
- Revalidate every destination through API access.

### Tests

- Token encryption and redaction.
- Tenant/user recipient selection.
- Preference and mandatory-category behavior.
- Deduplication and retry behavior.
- Invalid-token disablement.
- Logged-in and logged-out deep links.
- Unauthorized and removed-order deep links.
- Notification payload contains no prohibited data.
- Physical Android and iOS notification delivery.

### Gate 8

- End-to-end staging delivery works on Android and iOS.
- Association files validate for approved domains.
- Push credentials are company-owned and backed up.
- Privacy review passes.

## 13. Milestone 9 - hardening and release

### Security gate

- Threat-model review completed.
- Authentication, tenant, field, idempotency and deep-link tests pass.
- Dependency and secret scans pass.
- Logs and crash reports contain no prohibited sensitive data.
- Session revocation and incident procedure tested.

### Performance gate

- Normal API responses meet the approved target under representative load.
- Dashboard and first order page meet approved targets.
- Query counts and database plans are reviewed.
- Mobile startup and scrolling are acceptable on the minimum device profile.

### Reliability gate

- Poor network, timeout, retry and background/foreground transitions tested.
- Celery/push outages do not block order commits.
- Cleanup jobs are bounded, observable and rerunnable.
- Feature flags and rollback procedures are exercised.

### Store gate

- Final icons, splash, name, descriptions and privacy policy approved.
- Android/iOS signing ownership and backups verified.
- Data-safety and privacy declarations reviewed.
- Internal Play and TestFlight testing completed.
- Minimum supported app version mechanism verified.

### Production rollout

1. Staff-only internal build.
2. Small vendor pilot with mobile writes disabled.
3. Pilot read-only monitoring and feedback.
4. Enable writes for pilot tenants only.
5. Expand by tenant cohort after stability review.
6. Maintain PWA fallback throughout Phase 1.

## 14. Feature flags

Recommended server-side flags:

```text
MOBILE_API_ENABLED
MOBILE_AUTH_ENABLED
MOBILE_READ_API_ENABLED
MOBILE_ORDER_WRITES_ENABLED
MOBILE_PAYMENT_WRITES_ENABLED
MOBILE_PUSH_ENABLED
MOBILE_ALLOWED_TENANT_IDS
MOBILE_MINIMUM_APP_VERSION
```

Production flags default off until their milestone gate passes. Tenant allowlist
supports controlled pilot rollout without shipping a different app binary.

## 15. Definition of done

Phase 1 is complete only when:

- All PRD acceptance criteria pass.
- OpenAPI and implementation contract tests agree.
- Approved roles work on Android and iOS physical devices.
- Tenant isolation is proven for every endpoint and protected field.
- Order and payment writes are idempotent, versioned and audited.
- Notification and deep-link flows pass privacy and authorization review.
- Monitoring, feature flags, runbooks and rollback have been tested.
- Internal Play and TestFlight acceptance is complete.
- The PWA remains operational.
- Barcode scanning and packing completion remain outside the release.

## 16. Delivery approval checklist

- Approve the milestone order and gates.
- Approve removal of the Bubblewrap/TWA packaging workspace.
- Approve retaining and adapting domain-association endpoints.
- Approve feature-flagged, tenant-cohort rollout.
- Confirm accounts/domains/assets can be provided before shared builds.

This plan was approved on 19 July 2026. Approval authorizes implementation
planning at task level. It does not by itself authorize implementation,
production deployment, or app-store submission.
