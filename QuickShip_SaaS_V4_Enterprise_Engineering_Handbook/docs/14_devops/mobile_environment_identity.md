# Mathukai Operations Mobile Environment Identity

**Status:** Milestone 0 inventory - external prerequisites pending

**Date:** 19 July 2026

This inventory contains identifiers and ownership status only. Passwords,
private keys, signing files, tokens, certificates, provisioning profiles, and
service-account JSON files must never be added to this document or repository.

## Application identity

| Environment | Display name | Android package | iOS bundle ID |
| --- | --- | --- | --- |
| Development | Mathukai Operations Dev | `com.mathukai.operations.dev` | `com.mathukai.operations.dev` |
| Staging | Mathukai Operations Staging | `com.mathukai.operations.staging` | `com.mathukai.operations.staging` |
| Production | Mathukai Operations | `com.mathukai.operations` | `com.mathukai.operations` |

Package and bundle identifiers are permanent after the corresponding store app
is created. Production uses the product defaults approved on 19 July 2026.

## Network identity

| Environment | API origin | Verified-link origin | Status |
| --- | --- | --- | --- |
| Development | Local/LAN HTTPS or tunnel | Development origin | To be assigned |
| Staging | Company staging HTTPS domain | Same staging origin | Required before shared build |
| Production | Company production HTTPS domain | Same production origin | Required before store build |

Environment hosts are selected at build time. Ordinary users cannot change the
API origin. Development and staging builds must never connect to the production
database or production push project.

## Expo identity

| Environment | Suggested EAS channel | Suggested Expo project | Status |
| --- | --- | --- | --- |
| Development | `development` | `mathukai-operations-dev` | Company account required |
| Staging | `preview` | `mathukai-operations-staging` | Company account required |
| Production | `production` | `mathukai-operations` | Company account required |

Final Expo organization/project IDs are recorded after the company-owned account
is created with multi-factor authentication.

## External ownership inventory

| System | Required owner | MFA | Current status | Secret location |
| --- | --- | --- | --- | --- |
| Expo/EAS | Company organization | Required | Pending confirmation | Company secret manager |
| Google Play Console | Company account | Required | Pending confirmation | Google-managed access |
| Apple Developer | Company organization | Required | Pending confirmation | Apple-managed access |
| Firebase development | Company project | Required | Pending creation/confirmation | Company secret manager |
| Firebase staging | Company project | Required | Pending creation/confirmation | Company secret manager |
| Firebase production | Company project | Required | Pending creation/confirmation | Company secret manager |
| Crash/error monitoring | Company organization | Required | Pending selection/confirmation | Company secret manager |
| DNS/HTTPS domains | Company account | Required | Pending domain values | DNS provider |
| Android signing backup | Named company custodians | Required | Created during release setup | Offline encrypted backup |
| Apple signing/profiles | Company team | Required | Created during release setup | Apple/EAS managed plus backup |

No personal account is accepted as the sole production owner.

## Website/application associations

### Android

- Association URL: `/.well-known/assetlinks.json`
- Package ID comes from `ANDROID_APP_PACKAGE_ID`.
- SHA-256 values come from `ANDROID_APP_SHA256_FINGERPRINTS`.
- Development, staging, upload-key, and Play App Signing fingerprints are added
  only to their appropriate deployment.
- With no fingerprint configured, the endpoint returns an empty array and claims
  no application.

### iOS

- Association URL: `/.well-known/apple-app-site-association`
- Bundle ID comes from `IOS_APP_BUNDLE_ID`.
- Apple team ID comes from `IOS_APP_TEAM_ID`.
- With no team ID configured, the endpoint returns no application details and
  claims no application.

Both endpoints are public JSON documents. They contain public application
identifiers, never signing private keys.

## Mobile configuration inventory

The following names are configuration inputs; values are provided per
environment during later milestones:

```text
MOBILE_APP_ENVIRONMENT
MOBILE_API_BASE_URL
MOBILE_DEEP_LINK_ORIGIN
ANDROID_APP_PACKAGE_ID
ANDROID_APP_SHA256_FINGERPRINTS
IOS_APP_BUNDLE_ID
IOS_APP_TEAM_ID
EXPO_PUBLIC_PROJECT_ID
MOBILE_MONITORING_DSN
```

Public client configuration is not treated as a server secret, but it must not
include database credentials, Django secrets, API private keys, refresh tokens,
Firebase server credentials, Apple private keys, or signing passwords.

## Milestone 0 external completion checklist

- [ ] Confirm the company-owned Expo organization and MFA owners.
- [ ] Confirm the Google Play company account and MFA owners.
- [ ] Confirm the Apple Developer company team and MFA owners.
- [ ] Supply staging API/deep-link HTTPS domain.
- [ ] Supply production API/deep-link HTTPS domain.
- [ ] Confirm separate Firebase projects or approved environment separation.
- [ ] Confirm monitoring provider and company project ownership.
- [ ] Supply final logo, application icon, splash asset, and approved brand colors.
- [ ] Name at least two signing-credential custodians.
- [ ] Approve the secret-manager and offline-backup locations.

Gate M0 remains open until the external checklist is completed. Code and tests
for safe association defaults can be completed without those values.
