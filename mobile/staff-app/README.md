# Mathukai Staff Android App

Android-only React Native staff application built with Expo SDK 57, Expo Router, and strict TypeScript.

This app uses the same Mathukai backend API as the owner app, but the visible mobile experience is limited to basic staff workflows:

- Order processing
- Shipping label viewing, sharing, and printing
- Stock lookup and stock quantity updates
- Account/logout

Owner-only areas such as sales dashboards, expenses, product sales reports, and profit views are not part of this app shell.

## Local setup

1. Install Node.js LTS, Android Studio, an Android SDK, and an emulator.
2. Run `npm install` in this directory.
3. Create the local native project with `npm run prebuild:android`.
4. Build the development client with `npx expo run:android`.
5. For later sessions, start Metro with `npm run android`.

An Expo account is not required for this local Android workflow.

## Build notes

The staff app intentionally uses a separate Android package: `com.mathukai.staff`.

Before enabling push notifications for staff, create a new Firebase Android app for this package and add its own `google-services.json`.
