# BestShotAI Monorepo (FastAPI + Next.js + React Native)

This repo now follows the ShapeMiles-style stack:
- `backend/` FastAPI backend (pipeline-first)
- `mobile/` React Native (Expo) iOS client
- `web/` Next.js web shell

## Confirmed Processing Order
1. User selects multiple videos on iOS.
2. Backend stores uploads and orders them chronologically (`recorded_at`).
3. Backend checks voice presence and transcribes with `testsucceed.com/whisper` (or configured `WHISPER_API_URL`).
4. Backend preserves word-level timestamps for all tracks and builds combined transcript.
5. Auto pre-edit runs:
   - Detect gaps `> 1s`
   - Build remove-gap plan
   - Generate word-level SRT and subtitles
   - Generate insertion suggestions from OpenAI API prompt
6. Backend renders final stacked output and exposes downloadable file.
7. Mobile downloads and saves final video locally.

## Backend Run
```bash
pip install -r backend/requirements.txt
python backend/main.py
```

## Core Backend API
- `POST /api/projects/` create project with multi-file upload
- `POST /api/projects/{id}/process-sync` run full pipeline immediately
- `GET /api/projects/{id}/timeline` timeline + transcript + gaps + insertions
- `GET /api/projects/{id}/gaps` pause intervals to remove
- `GET /api/projects/{id}/insertions` AI insertion suggestions
- `GET /api/projects/{id}/word-srt` full word-level SRT
- `GET /api/projects/{id}/subtitles` generated subtitle SRT
- `GET /api/projects/{id}/download` final rendered video

## Auth / Onboarding / Payments (scaffold)
- `POST /api/auth/signup`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `PUT /api/auth/onboarding`
- `POST /api/auth/payments/start`
- `POST /api/auth/payments/activate`

## Notes
- Current auth/payment implementation is in-memory scaffold.
- For production: persist users/projects in DB and replace payment placeholder with Stripe.
