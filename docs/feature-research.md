# What users want from an automatic video editor

Research notes for AutoCutAI, September 2026. The question: what do people
expect when an app edits their travel footage for them, how far does
AutoCutAI get today, and what should come next.

The sources are listed at the end. Most are vendor blogs and review roundups,
not independent studies, so treat the percentages as direction rather than
measurement. Where sources disagree, the notes say so.

## What users need

### 1. Captions that follow the voice

This comes up more than anything else, and the numbers explain why.

- Most short-form video is watched with the sound off. One guide puts it at
  85% of YouTube Shorts views; others land in the same range.
- Burned-in captions are credited with 15–25% better retention, and captions
  that highlight each word as it is spoken hold attention better than static
  lines, because the eye follows the moving word.
- Every editor in the category competes on caption styles: Submagic sells
  12+ animated styles, CapCut captions 100+ languages, and Instagram Edits
  added auto-translated bilingual captions in 15 languages on 2 July 2026.

What users want: captions on by default, word-by-word highlight, big enough to
read on a phone, placed above the platform's buttons, and a choice of look.

### 2. Cuts that don't clip words or feel choppy

Automatic cutting is expected; what users complain about is *how* it cuts.
AutoCut reviewers ask for cuts that are "more precise" and less "monotonous".
Wisecut reviewers say the result still needs manual fixing. Opus Clip is
described as inconsistent at cut points.

The failures behind those complaints are:

- cutting flush against a word, so its first or last sound is lost;
- removing every tiny pause, which makes a string of jump cuts;
- no way to undo a single cut without redoing the edit.

### 3. The first three seconds

50–60% of viewers who leave do so in the first three seconds, and 71% decide
early whether to keep watching. Editors respond with "hook" features: Opus
Clip scores moments for their pull and leads with the strongest; CapCut
publishes first-three-second templates.

For a travel edit this means dead air at the start is the worst possible
place for dead air, and the strongest shot or line should come first.

### 4. Music that moves with the cuts

Beat-synced music is the most requested feature in one CapCut survey figure
(92%, from a roundup; we could not find the original survey). GoPro Quik is
built around it: Auto HiLights picks moments and Beat Sync times the cuts to
the music. Google Photos' highlight videos do the same thing, more simply.

### 5. No black bars

TikTok is reported to penalise videos with black bars, and the standard fix
for landscape footage in a vertical video is a blurred, enlarged copy of the
same clip behind it (CapCut and FlexClip both default to it). Cropping is the
alternative when the subject is centred.

Travel footage mixes a vertical selfie clip with a horizontal drone or
landscape shot all the time, so this is not an edge case for us.

### 6. Audio that is audible and clean

Travel audio is recorded outdoors, so wind noise is the top audio complaint
and there is a whole category of wind-removal tools. On loudness the sources
disagree: most cite -14 LUFS (YouTube's normalisation target and the common
recommendation), while one argues Instagram and TikTok reward louder audio
around -10 to -12 LUFS. Both agree that quiet phone audio loses against
everything else in the feed.

### 7. Scenery that doesn't drag

A travel edit mixes talking clips with scenery (b-roll). Quik and Google
Photos keep scenery short and moving. A 40-second pan held in full is what
makes an automatic edit feel unedited.

### 8. Control after the automatic pass

Every tool reviewed pairs the automatic edit with a way to fix it, and the
Descript/Opus/CapCut comparisons treat that as the difference between a toy
and a tool. Users want to see *why* something was cut and put it back.

### 9. Travel-specific

- **Where and when.** Polarsteps' Trip Reels now include map flyovers of the
  route; Google Photos groups memories by place. Location titles ("Lisbon,
  day 2") are the cheapest version of this.
- **Chronological order by default**, because a trip is a story in time.

### 10. No paywall surprises

The most common CapCut complaints are aggressive paywalls, watermarks on
export, slow exports and billing problems. Watermark-free export is a
selling point in its own right.

## Where AutoCutAI stands

| Need | Before this pass | Now |
| --- | --- | --- |
| Captions | One plain SRT style, system font | Word-by-word highlight in three looks (Bold, Boxed, Clean) or off; bundled Montserrat font; placed above the platform UI; scales with the canvas |
| Precise cuts | Cut flush against words; every pause over the threshold cut | 0.10 s kept after speech and 0.06 s before it; pauses under 0.3 s left alone; filler-word and manual cuts stay exact |
| Cut filler words in captions | A removed "um" could still be captioned | Words are captioned only if most of the word survives the cut |
| Dead air at the start and end | Cut | Cut, up to the first word |
| Beat-synced music | Background music mixed at a fixed level | Unchanged (see next steps) |
| No black bars | Black bars | Blurred fill by default; crop or bars selectable |
| Audio | Passed through | Rumble filter, noise reduction and loudness normalised to -14 LUFS, on by default |
| Scenery pacing | Silent clips kept whole | Scenery trimmed to its middle 6 s by default (4, 6, 10 s or full); a trim set by the user wins |
| Control after the edit | Per-cut review in the editor | Same, plus changing the look and re-rendering from the phone |
| Chronological order | Yes, from capture time | Yes |
| Watermark | None | None |

Also fixed while reviewing the code, because they would have blocked a public
test:

- Google and Apple sign-in accepted any token that looked right; they now
  verify the signature, audience, issuer and expiry.
- Passwords were SHA-256 hashes; they are now bcrypt, and old hashes are
  upgraded on the next login. Session tokens expire and are stored hashed.
- A project update could point a clip at any file on the server, which the
  media endpoint would then serve. Clip paths are now confined to the
  project's own folder.
- An endpoint granted a paid plan without payment. It now refuses unless
  explicitly enabled for testing.
- The iOS app was on Expo SDK 53, which builds with Xcode 16. App Store
  Connect, TestFlight included, has only accepted Xcode 26 builds since
  28 April 2026. The app is now on SDK 57.

## What to build next

In order of value for effort:

1. **Strongest moment first.** Move the best line or shot into the first
   three seconds, or offer it as an option. The transcript and the pause
   detector already give most of what is needed to score moments.
2. **Beat-synced music.** Detect beats in the chosen track (librosa or
   aubio) and snap scenery cuts to them. Needs a licensed music library; do
   not ship with unlicensed tracks.
3. **Location titles.** iOS already stores GPS in the photo library.
   `expo-media-library` returns it per asset; reverse-geocode on the phone
   and send the place name with each clip for a "Lisbon" title card.
4. **Translated captions.** Azure Speech can translate during recognition,
   which would match Instagram Edits' bilingual captions without a second
   service.
5. **Target length.** Let the user ask for 30, 60 or 90 seconds and trim
   scenery and pauses to fit.
6. **Fonts for Thai, CJK and Hindi.** Montserrat covers Latin, Cyrillic and
   Vietnamese. Other scripts fall back to DejaVu, which lacks Thai and CJK;
   add the Noto fonts to the Docker image when those locales are enabled.
7. **Payments.** RevenueCat with server-side receipt validation, before the
   App Store (not needed for TestFlight).
8. **Database.** Projects and accounts are JSON files, which works for one
   backend instance. Move to Postgres before running more than one.

Deliberately not planned: flashy transitions and effects packs. Nothing in the
research ties them to retention, and reviewers of auto-editors complain about
gimmicky output more than they ask for more of it.

## Getting it onto an iPhone

The steps are in [`mobile/README.md`](../mobile/README.md). In short: run
`npx eas init`, register the bundle ID with Sign in with Apple enabled, set
`EXPO_PUBLIC_API_BASE_URL` to the backend's HTTPS address, then
`npx eas build --profile production` and `npx eas submit --latest` for
TestFlight. The backend needs an Azure Speech key and, for Google sign-in,
`GOOGLE_CLIENT_IDS`.

## Sources

User needs and retention
- [Teleprompter.com — short-form video strategy](https://www.teleprompter.com/blog/short-form-video-strategy)
- [TrueFan — silent video hooks](https://www.truefan.ai/blogs/silent-video-hooks-optimization-guide)
- [CapCut — first-three-second hook patterns](https://www.capcut.com/create/short-form-video-hooks-first-3-second-patterns)
- [Opus Clip — ideal YouTube Shorts length and retention](https://www.opus.pro/blog/ideal-youtube-shorts-length-format-retention)
- [Teleprompter.works — YouTube Shorts best practices](https://teleprompter.works/blog/youtube-shorts-best-practices/)
- [Superprompt — best AI video editing apps 2026](https://superprompt.com/blog/best-ai-video-editing-apps-auto-edit-features-2026)
- [Atlantic.Net — top AI video editing platforms](https://www.atlantic.net/gpu-server-hosting/top-ai-video-editing-platforms-choosing-the-right-tool/)

Competitors
- [Restream — Opus Clip vs CapCut](https://restream.io/learn/comparisons/opusclip-vs-capcut/)
- [AI Hustle Guy — Descript vs CapCut vs Opus Clip](https://www.aihustleguy.com/blog/descript-vs-capcut-vs-opus-clip-ai-video-editor)
- [Submagic — AI captions](https://www.submagic.co/ai-caption) and [b-roll](https://www.submagic.co/features/b-roll)
- [Social Media Today — Edits adds bilingual captions](https://www.socialmediatoday.com/news/edits-app-gets-auto-translated-bilingual-captions/824421/)
- [Digital Camera World — GoPro Quik review](https://www.digitalcameraworld.com/reviews/gopro-quik-app-review)
- [GoPro — Quik moments and GPMF](https://gopro.com/en/us/news/quik-moments-updates-and-gpmf)
- [Android Authority — Google Photos memories editing](https://www.androidauthority.com/google-photos-memories-edit-3672506/)
- [Polarsteps — summer release (Trip Reels map scenes)](https://www.polarsteps.com/summer-release)

Complaints
- [Trustpilot — AutoCut reviews](https://www.trustpilot.com/review/autocut.fr)
- [G2 — Wisecut reviews](https://www.g2.com/products/wisecut-video/reviews)
- [eesel — CapCut reviews](https://www.eesel.ai/blog/capcut-reviews)
- [Product Hunt — CapCut reviews](https://www.producthunt.com/products/capcut/reviews)
- [Opus Clip — auto jump-cut editors](https://www.opus.pro/blog/auto-jump-cut-editors)

Framing and audio
- [CapCut — vertical video without black bars](https://www.capcut.com/create/vertical-video-to-widescreen-without-black-bars)
- [FlexClip — filling the sides of vertical video](https://www.flexclip.com/learn/fill-in-sides-of-vertical-video.html)
- [Opus Clip — loudness normalisers](https://www.opus.pro/blog/best-loudness-normalizers)
- [LALAL.AI — fixing audio in social video](https://www.lalal.ai/blog/quick-fixes-for-problematic-audio-in-social-media-videos/)
- [CleanAudio — removing wind noise](https://www.cleanaudio.io/remove-wind-noise-from-video)

iOS release requirements
- [Apple — upcoming requirements](https://developer.apple.com/news/upcoming-requirements/)
- [Expo — App Store Connect minimum SDK 26](https://expo.dev/blog/app-store-connect-minimum-sdk-26)
- [Apple Developer Forums — guideline 4.8 and Sign in with Apple](https://developer.apple.com/forums/thread/765145)
