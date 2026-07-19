# Mathukai Mobile App - Phase 1 Product Requirements

**Status:** Approved for detailed API design

**Approved:** 19 July 2026

**Platform:** Android only

**Mobile stack:** React Native, Expo, TypeScript

**Backend:** Existing Django application with Django REST Framework

## 1. Purpose

Create a secure, role-aware Mathukai mobile application for vendor and warehouse
users. Phase 1 focuses on daily order visibility, permitted order management,
read-only stock visibility, and operational notifications.

The existing Django web application remains operational and continues to provide
administration and advanced workflows. The mobile application consumes a new,
versioned REST API and does not duplicate business rules from Django.

## 2. Goals

- Give vendor users a focused Android experience for daily operations.
- Make current order workload and urgent issues visible immediately.
- Allow authorized users to review orders and perform permitted status changes.
- Provide read-only product and stock visibility.
- Deliver role-appropriate push notifications that open the relevant screen.
- Preserve the existing tenant isolation, audit logging, idempotency, and
  integration behavior.
- Release one React Native codebase for Android.

## 3. Non-goals for Phase 1

- Barcode scanning.
- Packing scan validation or reconciliation.
- Packing completion.
- Offline write synchronization.
- Stock adjustments or inventory reconciliation.
- Shipping-label or packing-list printing from the mobile app.
- Platform superadmin configuration.
- Vendor onboarding and complex product administration.
- Replacing the existing Django PWA.

Barcode scanning and packing completion are explicitly reserved for Phase 2.

## 4. Target users

### Vendor owner

Views tenant operations, manages permitted order states, reviews stock, receives
alerts, and sees limited product-routing and integration health.

### Vendor operator

Performs daily order work within permissions granted by the tenant and platform.

### Vendor viewer

Has read-only access to permitted operational information.

### Warehouse user

Views assigned operational orders and stock information. Packing and scanning
actions are not available in Phase 1.

Platform superadmins continue using the web application for Phase 1.

## 5. Product principles

- Django is the source of truth for all business rules and permissions.
- Every API operation is scoped to a server-validated active tenant.
- The app never trusts a tenant ID, role, status transition, or price supplied
  by the client without server validation.
- Mobile screens show only actions returned as allowed by the API.
- External integrations run asynchronously where possible.
- Sensitive information is minimized in notifications and local storage.
- Every screen has loading, empty, offline, permission-denied, and retry states.

## 6. Navigation

The Phase 1 bottom navigation contains four destinations:

1. **Home** — workload summary, alerts, and quick actions.
2. **Orders** — order search, filters, details, and permitted status changes.
3. **Stock** — read-only products, quantities, routing readiness, and movement
   history.
4. **More** — notifications, active vendor, profile, app information, and
   logout.

The Scan destination is added only in Phase 2.

## 7. Functional requirements

### 7.1 Authentication

- Users can sign in with existing Mathukai credentials.
- The app never stores the user's password.
- The access token is short-lived and held in application memory.
- The rotating refresh token is stored in Expo SecureStore.
- The app restores a valid session after restart.
- Only one refresh request can run at a time.
- Logout revokes the mobile device session and clears local user data.
- Disabled users, revoked sessions, and removed tenant memberships return the
  user to Login.

Recommended initial policy:

- Access token lifetime: approximately 10 minutes.
- Refresh token lifetime: approximately 30 days.
- Refresh-token rotation and reuse detection enabled.

### 7.2 Tenant selection

- A user with one valid tenant enters that tenant automatically.
- A user with multiple memberships chooses an active tenant after login.
- Django revalidates membership and permissions before selecting a tenant.
- Switching tenant clears tenant-specific cached orders, products, and
  notifications before loading the new tenant.
- Tenant membership is revalidated on every authenticated API request.

### 7.3 Home

The Home screen provides a role-aware summary containing permitted subsets of:

- Pending orders.
- Accepted orders.
- Orders requiring attention.
- Unread notifications.
- Low-stock products.
- Shared-store routing health.

Quick actions open filtered Orders, Stock, or Notifications screens. Phase 1 does
not require analytical charts; it prioritizes current workload and actionable
alerts.

### 7.4 Order list

- Search by order number, permitted customer information, or tracking number.
- Filter by status, date, and payment state.
- Status choices include All, Pending, Accepted, Shipped, Completed, and
  Cancelled where relevant.
- Use cursor pagination with an initial target page size of 25.
- Support pull-to-refresh and show the last successful update time.
- Each order summary shows order number, status, order time, item count, total,
  payment state, shipping state, and attention indicator as permitted.

### 7.5 Order detail

The detail screen can contain:

- Order number and current status.
- Payment and shipping state.
- Allowed actions.
- Permitted customer and delivery summary.
- Ordered products.
- Tracking information.
- Status and activity history.
- Operational notes.

Customer fields are returned only when the authenticated role already has
permission to view them in the tenant-scoped workflow.

### 7.6 Order status changes

- The app does not hard-code allowed status transitions.
- The order-detail API returns current version and `allowed_actions` metadata.
- Confirmation is required for sensitive transitions.
- Cancellation requires a reason when specified by the API.
- Every write contains a unique idempotency key.
- The server applies the transition transactionally and returns the updated
  order.
- The UI changes only after server confirmation; status changes are not
  optimistic.
- A stale order version produces a conflict response and reload prompt.
- Existing stock, audit, WhatsApp, and integration side effects remain owned by
  the Django business layer.

### 7.7 Stock

- Search by product name, SKU, or barcode text.
- Filter by in-stock, low-stock, and out-of-stock states.
- List product image, SKU, last-known quantity, stock state, and update time.
- Product detail can show permitted prices, WooCommerce identifiers,
  product-routing readiness, and recent stock movements.
- Phase 1 stock is read-only.
- Cached stock values display their last successful synchronization time.

### 7.8 Notifications

Initial notification events:

- New order received.
- Order assigned to the active vendor.
- Important order-status change.
- Order requiring attention.
- Integration or routing issue visible to the user's role.

Notification content must not include customer phone numbers, full addresses,
payment details, API credentials, or raw provider payloads.

The More area includes a notification inbox with unread state, history, mark as
read, related-order navigation, and preferences.

### 7.9 Deep links

- Notification taps open the relevant order or notification screen.
- Both an application scheme and verified HTTPS links are supported.
- If logged out, the app retains the intended destination through login and
  tenant validation.
- The API revalidates access before returning a deep-linked resource.
- A deep link never bypasses role or tenant permissions.

## 8. Phase 1 API surface

All mobile endpoints are versioned under `/api/v1/`.

### Authentication

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/select-tenant
```

### Dashboard

```text
GET /api/v1/dashboard
```

### Orders

```text
GET  /api/v1/orders
GET  /api/v1/orders/{id}
POST /api/v1/orders/{id}/status
POST /api/v1/orders/{id}/payment-received
```

The payment-received action is exposed only if confirmed as required and
permitted for Phase 1 during API contract review.

### Products and stock

```text
GET /api/v1/products
GET /api/v1/products/{id}
GET /api/v1/stock/movements
```

### Notifications and devices

```text
GET    /api/v1/notifications
POST   /api/v1/notifications/{id}/read
GET    /api/v1/notification-preferences
PATCH  /api/v1/notification-preferences
POST   /api/v1/devices/push-token
DELETE /api/v1/devices/{id}
```

## 9. API conventions

- JSON requests and responses.
- HTTPS only outside local development.
- ISO 8601 UTC timestamps.
- Monetary values represented as decimal strings with currency.
- Cursor pagination for operational lists.
- Stable machine-readable codes with separate user-facing labels.
- A request ID returned with every response.
- Standard machine-readable validation and error structure.
- No raw integration credentials or provider payloads.
- Idempotency keys required for status-changing operations.
- Version checks required for concurrent order updates.

Expected error categories include validation, authentication, permission,
missing resource, conflict, rate limit, temporary integration failure, and
unexpected server failure.

## 10. Roles and permissions

| Capability | Vendor owner | Vendor operator | Vendor viewer | Warehouse user |
| --- | --- | --- | --- | --- |
| View dashboard | Yes | Yes | Yes | Yes |
| View orders | Yes | Yes | Yes | Assigned scope |
| View order details | Yes | Yes | Yes | Assigned scope |
| View customer details | Yes | Permission-based | Limited | Operational fields |
| Update order status | Permission-based | Permission-based | No | No in Phase 1 |
| Cancel order | Permission-based | Permission-based | No | No |
| View products and stock | Yes | Yes | Yes | Yes |
| Adjust stock | No | No | No | No |
| View routing health | Yes | Limited | No | No |
| Receive notifications | Yes | Yes | Yes | Relevant alerts |
| Switch tenants | Membership-based | Membership-based | Membership-based | Membership-based |

The table describes intended product behavior. Existing server-side access rules
remain authoritative and must be reviewed before final endpoint acceptance.

## 11. Offline behavior and local data

Phase 1 supports read-only cached data, not offline writes.

- Show recently synchronized dashboard, orders, products, and stock where safe.
- Clearly mark cached data and show its last update time.
- Do not queue order-status changes while offline.
- Credentials are stored only in SecureStore.
- SQLite contains only the minimum operational cache.
- Tenant-specific cache is cleared on tenant switch.
- User-specific cache is cleared on logout.
- Customer and payment data are not retained longer than operationally needed.
- API keys and external integration credentials are never stored on the device.

## 12. Non-functional requirements

### Performance targets

- Normal API response: under 500 ms at the application service boundary.
- Dashboard response: under 1 second.
- First order page: under 1 second.
- Cached startup to usable screen: under 2 seconds on a supported device.
- Mobile-sized image thumbnails used in list screens.
- Slow external integrations executed through background jobs.

### Reliability

- Retriable read failures offer retry without losing navigation state.
- Write retries reuse the same idempotency key.
- Push-token delivery failures disable invalid device registrations.
- Mobile errors carry a server request ID for diagnostics.

### Accessibility

- Support system font scaling.
- Provide accessible labels for icons and status controls.
- Do not communicate status through color alone.
- Maintain usable touch targets and screen-reader navigation.
- Support reduced-motion preferences where applicable.

### Observability

- Capture application crashes and handled API failures without sensitive data.
- Record app version, platform, endpoint, response class, and request ID.
- Monitor authentication failures, API latency, push failures, and conflict
  rates.

## 13. Technical architecture

### Mobile

- React Native with Expo and TypeScript.
- Expo Router for navigation.
- TanStack Query for server data and cache state.
- Zustand only for small cross-screen client state where required.
- React Hook Form and Zod for client-side form validation.
- Expo SecureStore for refresh tokens.
- Expo Notifications backed by FCM.
- Local Android development builds initially; Expo EAS may be adopted later for
  shared signed builds.

### Backend

- Existing Django monolith and PostgreSQL database.
- Django REST Framework for the versioned mobile API.
- Existing Celery and Redis foundation for asynchronous work.
- Existing tenant, membership, access, order, stock, audit, WooCommerce,
  WhatsApp, and Shiprocket business logic reused through service boundaries.

## 14. Testing requirements

- Tenant-isolation tests for every endpoint.
- Role and permission tests for every protected field and action.
- Token rotation, logout, revocation, and tenant-switch tests.
- Idempotency and concurrent-update tests.
- Pagination, filtering, and validation tests.
- Push registration and invalid-token tests.
- Mobile unit and component tests for critical states.
- Android emulator and physical-device testing.
- Poor-network, expired-session, and interrupted-request testing.
- Accessibility review of every Phase 1 screen.

## 15. Release approach

1. Development builds connected to a local or development API.
2. Staging API and internal Android builds.
3. Internal Android testing through the Play Console when the company account
   is available.
4. Role-based user acceptance testing with representative tenant data.
5. Security, privacy, performance, and release checklist approval.
6. Controlled production rollout.
7. Monitor crashes, API errors, authentication failures, and notification
   delivery before broad release.

The existing web application remains available throughout rollout and serves as
the operational fallback.

## 16. Phase 2 boundary

Phase 2 can add:

- Scan bottom-navigation destination.
- Packing queue.
- Barcode scanner.
- Local scan progress.
- Server-side scan validation.
- Scan reconciliation.
- Packing completion.
- Offline scan synchronization.
- Related printing or shipping workflows if separately approved.

Candidate Phase 2 endpoints:

```text
GET  /api/v1/packing/queue
GET  /api/v1/packing/orders/{id}/requirements
POST /api/v1/packing/orders/{id}/scan
POST /api/v1/packing/orders/{id}/complete
```

No Phase 2 packing endpoint is part of Phase 1 implementation acceptance.

## 17. Phase 1 acceptance criteria

Phase 1 is ready for production approval when:

- Users can securely log in, restore a session, switch permitted tenants, and
  log out.
- Removed memberships and revoked sessions stop access promptly.
- Each role sees only permitted tenant data and actions.
- Dashboard data is accurate and role-aware.
- Order search, filters, pagination, and details work on Android.
- Permitted status updates are transactional, idempotent, audited, and protected
  against stale data.
- Stock data is read-only and shows synchronization time.
- Push notifications register, deliver, open the correct screen, and contain no
  prohibited sensitive information.
- Offline screens clearly distinguish cached from current information.
- Required tenant-isolation, permission, security, and concurrency tests pass.
- Internal Android acceptance testing passes.
- Privacy, store metadata, signing, monitoring, and rollback plans are approved.

## 18. Approved product defaults

- Application display name: **Mathukai Operations**.
- Android package ID: `com.mathukai.operations`.
- Product support baseline: Android 10 or later.
- Authentication baseline: 10-minute access token and 30-day rotating refresh
  token with revocation and reuse detection.
- Customer visibility: full permitted details for owners, operational details
  for operators, masked details for viewers, and fulfilment-only fields for
  warehouse users.
- Order actions reuse existing Django permissions and add no mobile-only
  privileges.
- Payment-received is included for authorized owners and operators.
- Notification categories: new orders, attention-required orders, status
  changes, and permitted routing or integration alerts.
- Read-only offline cache retention: no more than 24 hours, with immediate clear
  on logout or tenant switch.
- All Phase 1 writes require connectivity.
- Google Play, Firebase, Expo, and monitoring accounts must be
  company-owned and protected with multi-factor authentication when created.
- EAS cloud builds are optional for initial development; an Expo project is
  required later if the approved Expo Push Service design is retained.
- Local Android development builds and the Android emulator require no store
  account. Expo Go is not used for production acceptance.
- Barcode scanning, packing, and offline write synchronization remain Phase 2.

Staging and production domains, final brand assets, and Google Play account
contacts must be supplied before release. Firebase ownership is required before
push-notification acceptance; neither is a blocker for the API foundation.
