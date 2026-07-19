# Mathukai Operations Android Environment Identity

**Status:** Milestone 0 ready for API implementation; external release
prerequisites deferred

**Date:** 19 July 2026

The approved mobile scope is Android only. The project currently has no Expo,
Google Play, or Firebase account. Those accounts are not required for the Django
REST API foundation or for initial local Android development.

This inventory contains identifiers and ownership status only. Passwords,
private keys, signing files, tokens, certificates, and service-account JSON
files must never be added to this document or repository.

## Application identity

| Environment | Display name | Android package |
| --- | --- | --- |
| Development | Mathukai Operations Dev | `com.mathukai.operations.dev` |
| Staging | Mathukai Operations Staging | `com.mathukai.operations.staging` |
| Production | Mathukai Operations | `com.mathukai.operations` |

Package identifiers become difficult to change after a Play Console application
is created. Production uses the product default approved on 19 July 2026.

## Development path without accounts

Initial development uses Android Studio, its bundled JDK, the Android SDK, and
an Android emulator. A local Expo development build can be created and installed
without a Google Play, Firebase, or Expo account. A physical Android device can
also run a locally built development client over USB or the approved development
network.

Account requirements are deferred to the milestone that needs them:

| Capability | Account needed | Required by |
| --- | --- | --- |
| Django REST API foundation | None | Milestone 1 |
| Local Android UI and emulator build | None | Milestone 4 |
| Expo EAS cloud build | Expo account | Only if EAS is adopted |
| Android push notifications through Expo | Expo and Firebase projects | Milestone 8 |
| Internal/production Play distribution | Google Play Console | Milestone 9 |

Expo remains the application framework. EAS is optional for initial development;
it can be adopted later if shared cloud builds are useful.

## Network identity

| Environment | API origin | Android App Link origin | Status |
| --- | --- | --- | --- |
| Development | Local/LAN HTTPS or tunnel | Development origin | Assign before mobile integration |
| Staging | Company staging HTTPS domain | Same staging origin | Required before shared testing |
| Production | Company production HTTPS domain | Same production origin | Required before Play release |

Environment hosts are selected at build time. Ordinary users cannot change the
API origin. Development and staging builds must never connect to the production
database or production Firebase project.

## External ownership inventory

| System | Required owner | Current status | Required by |
| --- | --- | --- | --- |
| Local Android toolchain | Development workstation | Not yet verified | Milestone 4 |
| Expo/EAS | Company organization with MFA | No account; deferred | Milestone 8 or first EAS cloud build |
| Google Play Console | Company account with MFA | No account | Milestone 9 |
| Firebase | Company project with MFA | No account/project | Milestone 8 |
| Crash/error monitoring | Company organization with MFA | Pending selection | Before production pilot |
| DNS/HTTPS domains | Company account with MFA | Pending domain values | Shared staging build |
| Android signing backup | At least two company custodians | Pending | First shared signed build |

No personal account is accepted as the sole production owner.

## Android website/application association

- Association URL: `/.well-known/assetlinks.json`
- Package ID comes from `ANDROID_APP_PACKAGE_ID`.
- SHA-256 values come from `ANDROID_APP_SHA256_FINGERPRINTS`.
- Development, staging, upload-key, and Play App Signing fingerprints are added
  only to their appropriate deployment.
- With no fingerprint configured, the endpoint returns an empty array and claims
  no application.

The endpoint is a public JSON document. It contains public application
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
EXPO_PUBLIC_PROJECT_ID
MOBILE_MONITORING_DSN
```

`EXPO_PUBLIC_PROJECT_ID` remains unset until an Expo project is created and is
not required for the API foundation or initial local Android build.

Public client configuration is not treated as a server secret, but it must not
include database credentials, Django secrets, API private keys, refresh tokens,
Firebase server credentials, or signing passwords.

## Milestone status and deferred checklist

Milestone 0 is complete for Android API implementation. The lack of external
accounts does not block Milestones 1 through 3.

Before Milestone 4 device work:

- [ ] Install or verify Android Studio, bundled JDK, Android SDK, and emulator.
- [ ] Supply a development API origin reachable from the emulator/device.

Before Milestone 8 push acceptance:

- [ ] Create a company-owned Expo organization/project and confirm MFA owners.
- [ ] Create a company-owned Firebase project and confirm MFA owners.

Before Milestone 9 store release:

- [ ] Create a company-owned Google Play Console account and confirm MFA owners.
- [ ] Supply production API/App Link HTTPS domain.
- [ ] Supply final logo, application icon, splash asset, and approved colors.
- [ ] Name at least two signing-credential custodians.
- [ ] Approve secret-manager and offline-backup locations.

EAS cloud builds may be enabled after the Expo organization exists, but they are
not required for initial local Android development.
