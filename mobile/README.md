# AutoCutAI iOS app

React Native (Expo SDK 53). The app picks clips from the photo library,
uploads them, and shows the cut the backend produced so you can adjust it
before exporting.

## The flow

1. **New** — pick up to 10 clips (15 minutes total) from the photo library.
   Each is uploaded with a progress bar; the first creates the project and the
   rest are added as tracks.
2. The backend transcribes with Azure Speech, detects filler words and pauses,
   and proposes a cut per clip.
3. **Editor** — every clip shows what the AI removed and why, with the
   transcript of each cut. Switch any cut off to keep that moment, drop a whole
   clip, then render.
4. Preview the finished cut in the app and save it to your photo library.

## Native project

`ios/` and `android/` are **generated** from `app.json` and are not committed.
Run `npx expo prebuild` to create them. A stale committed copy silently ignores
config changes, which is how the app previously ended up with no photo-library
usage descriptions.

## Running against a backend

The API base URL comes from `EXPO_PUBLIC_API_BASE_URL`:

```bash
cp .env.example .env      # then edit
npm install
npx expo prebuild --platform ios
npx expo run:ios          # simulator or a connected device
```

A simulator cannot reach `localhost` on the host by that name in all setups —
use your machine's LAN IP (`http://192.168.x.x:8000`) if the simulator cannot
connect. A physical device always needs the LAN IP or a public URL.

The backend must be reachable over **HTTPS** from a physical device unless you
add an ATS exception; the simulator is more permissive.

## TestFlight build

```bash
npx eas build --profile preview --platform ios     # internal distribution
npx eas build --profile production --platform ios  # App Store
```

`preview` produces an internally distributable build for real-device testing.
Both need an Apple Developer account configured in EAS.

## Before submitting to the App Store

These are not blockers for internal testing but will fail review:

- **Privacy policy and terms** are placeholder pages on the web app.
- **In-app purchases are disabled.** `expo-in-app-purchases` was archived by
  Expo and does not build on SDK 53, so it was replaced by a stub in
  `src/billing/purchases.ts` that fails every purchase loudly. Wiring up
  RevenueCat with server-side receipt validation is required before charging
  anyone — see the notes in that file.

## Tests

```bash
npm test           # jest
npx tsc --noEmit   # typecheck
```
