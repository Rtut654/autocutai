# AutoCutAI iOS app

React Native on Expo SDK 57 (React Native 0.86). The app picks clips from the
photo library, uploads them, and shows the cut the backend made so you can
adjust it before exporting.

## The flow

1. **New** — pick up to 10 clips (15 minutes total) from the photo library and
   choose the look: caption style, framing, how long scenery shots run, and
   audio cleanup. Uploads run in an iOS background session, so they continue
   if you switch apps.
2. The backend transcribes with Azure Speech, finds filler words and pauses,
   and renders a first cut.
3. **Editor** — every clip lists what was removed and why, with the words that
   were cut. Switch a cut off to keep that moment, drop a clip, or change the
   look, then re-render.
4. Preview the result and save it to your photo library.

## Before the first build

These need your accounts, so they cannot be done from the repo:

1. **Expo project.** Run `npx eas init` once. It links the app to your Expo
   account and writes `extra.eas.projectId` into `app.json`.
2. **Apple.** An Apple Developer membership, and the bundle ID
   `com.bestshotai.app` registered to your team (EAS will offer to create it).
   Enable **Sign in with Apple** on the App ID.
3. **Backend URL.** Builds read `EXPO_PUBLIC_API_BASE_URL` at build time. Set
   it for the preview and production profiles:
   ```bash
   npx eas env:create --name EXPO_PUBLIC_API_BASE_URL --value https://api.your-domain.com \
     --environment preview --environment production
   ```
   A physical iPhone needs **HTTPS** for anything on the internet. Plain
   `http://` works only for addresses on your local network (the app allows
   local networking), which is handy for a backend on your laptop.
4. **Google sign-in** (optional). The backend must list the iOS client ID in
   `GOOGLE_CLIENT_IDS`, or Google sign-in is refused. Email and Apple sign-in
   work without it.

## Putting it on an iPhone

**TestFlight** (recommended — installs like a normal app, up to 10,000 testers):

```bash
npx eas build --platform ios --profile production
npx eas submit --platform ios --latest
```

Then add testers in App Store Connect → TestFlight. Internal testers (your
team) can install within minutes; external testers need a short Beta App
Review the first time.

**Ad hoc** (no App Store Connect, up to 100 registered devices):

```bash
npx eas device:create          # register each iPhone once
npx eas build --platform ios --profile preview
```

Open the link EAS prints on the iPhone to install.

**Simulator** during development:

```bash
npm install
npx expo prebuild --platform ios
npx expo run:ios
```

### Why SDK 57

Since 28 April 2026 App Store Connect, TestFlight included, only accepts
builds made with Xcode 26 and the iOS 26 SDK. SDK 53, which this app used
before, builds with Xcode 16 and was rejected. SDK 57's default EAS image
uses Xcode 26, so nothing extra is needed in `eas.json`.

## Native project

`ios/` and `android/` are **generated** from `app.json` by `expo prebuild` and
are not committed. A stale committed copy silently ignores config changes,
which is how the app once shipped with no photo-library usage descriptions.

## Before the App Store (not needed for TestFlight)

- **Privacy policy and terms** are placeholder pages on the web app.
- **In-app purchases are off.** `expo-in-app-purchases` was archived and does
  not build on current Expo, so it is replaced by a stub in
  `src/billing/purchases.ts` that fails every purchase loudly. The backend
  endpoint that granted plans without payment is disabled. Every feature is
  free until RevenueCat and server-side receipt validation are in place.

## Tests

```bash
npm test             # jest
npm run typecheck    # tsc
npx expo export --platform ios   # full iOS bundle, catches import errors tsc misses
```
