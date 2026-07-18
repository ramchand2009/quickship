# Mathukai Android app

This folder packages the existing Mathukai PWA as an Android Trusted Web
Activity (TWA). The website remains the application source, so deployed fixes
reach web and Android users together. The generated Android project can produce
both a test APK and the Play Store AAB.

## One-time production setup

1. Deploy the Django app on its final HTTPS domain.
2. From the repository root, initialize the Android project:

   ```powershell
   .\mobile\android\Initialize-MobileApp.ps1 -SiteUrl https://your-domain.com
   ```

3. Accept Bubblewrap's Android SDK/JDK setup when prompted. Use package ID
   `com.mathukai.dashboard`. Store the signing key outside source control and
   back it up securely.
4. Add both the local release-key and Google Play App Signing SHA-256
   fingerprints to production as a comma-separated environment value:

   ```text
   ANDROID_APP_PACKAGE_ID=com.mathukai.dashboard
   ANDROID_APP_SHA256_FINGERPRINTS=AA:BB:...,11:22:...
   ```

5. Redeploy and confirm that
   `https://your-domain.com/.well-known/assetlinks.json` returns those values.

The fingerprint shown in Google Play Console under **Setup > App integrity** is
the fingerprint users receive from Play. It may differ from the upload key.

## Build and test

Run these commands inside `mobile/android`:

```powershell
npm install
npx bubblewrap validate --url https://your-domain.com
npm run build
npm run install:device
```

The build creates an APK for device testing and an AAB for Google Play. Before
release, test login persistence, camera/barcode scanning, PDF downloads, offline
screen behavior, push notification permission, notification delivery, and deep
links to order pages on a physical Android phone.

## Release rules

- Never commit the `.jks`/`.keystore`, its passwords, APKs, or AABs.
- Increment `appVersionCode` for every Play Store upload.
- Keep the package ID permanent after the first Play Store release.
- Do not publish until Digital Asset Links verification succeeds; otherwise the
  app opens with browser chrome instead of as a verified full-screen app.
