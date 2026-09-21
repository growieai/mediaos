# Speaking-video setup and review

This implementation produces a talking presenter from **exact approved content**:

`approved carousel excerpts → ElevenLabs speech/alignment → HeyGen photo avatar → local captions/fact-card cutaways/disclosure → media QA → separate human approval`

It does not publish a video. Text skills remain in mock mode. The earlier silent motion concept
is separate and cannot become an approved media run.

## Dependencies

Start with [the guided Sofía voice audition](VOICE_SELECTION.md); a voice ID should be chosen
by listening before configuring the runtime profile.

1. Configure `ELEVENLABS_API_KEY` and `HEYGEN_API_KEY` in the ignored root `.env` or a secret manager.
   Never paste credentials into chat, a profile JSON, or the console.
2. Install FFmpeg and FFprobe on the host. The backend Docker image installs FFmpeg.
3. Choose a voice you are entitled to use, suitable for Spanish from Spain. Record its actual
   ElevenLabs `voice_id`. No voice cloning, consent fabrication or invented voice ID is implemented.
4. Review Sofía's proposed portrait in `characters/sofia/references/v1/`. An ADMIN creates a media
   profile referencing the current persisted visual configuration. Its rate card must come from
   the actual provider account and include a dated reference. No default rates are seeded.
   Speaking profiles accept verified PNG/JPEG bytes; a WebP reference must be versioned as a
   supported image before any paid execution. File extensions do not determine the upload MIME type.
5. An ADMIN creates an explicit, expiring per-run and per-day spend policy. Reservations use this
   configured rate card; they are not a provider billing guarantee. Set provider account spending
   limits as well. Unknown actual costs remain `null`; they never become zero.
6. Set `MEDIA_LIVE_ENABLED=true` and restart the backend **only when paid execution is authorized**.
   Keep `AI_MOCK_MODE=true` and the broader publishing/auto-reply/external-creator flags disabled.

`GET /v1/media-dependencies` reports availability booleans only, never keys. Until configured,
the internal console shows the missing dependency instead of a simulated finished video.

## Configuration API

All requests require `Authorization: Bearer <internal-token>` and `X-Tenant-ID`. Use local
`.local/credentials.json` to select the correct internal role without copying secrets into logs.

An ADMIN posts to `/v1/influencers/{influencer_id}/media-profiles`:

```json
{
  "visual_config_version_id": "<current visual configuration UUID>",
  "payload": {
    "schema_version": 1,
    "voice_id": "<your chosen provider voice ID>",
    "tts_model": "eleven_multilingual_v2",
    "presenter_provider": "heygen",
    "tts_usd_per_1000_characters": "<verified decimal upper-bound rate>",
    "avatar_usd_per_second": "<verified decimal upper-bound rate>",
    "price_reference": "<account price reference, without secrets>",
    "price_checked_at": "<UTC timestamp>"
  }
}
```

An ADMIN posts `/v1/media-spend-policy` with `schema_version:1`, `per_run_usd` and `per_day_usd`
as positive decimal strings, `expires_at` as a future UTC timestamp, and `enabled:true`.
There is no implicit budget. New policies and profiles are immutable versions.

## Local test flow

1. Open the internal console at `http://127.0.0.1:3000`. Select a verified, current, human-approved
   workflow. A fixture or stale/blocked source cannot qualify.
2. Open **Speaking video**. Select the configured profile and one to ten approved text excerpts
   in speaking order. Saving a video request makes no provider calls; mandatory disclosure is
   appended by the database. The operator cannot submit arbitrary new narration.
3. Inspect the saved script and budget. Execute/resume when ready. Speech, uploads and video
   submission each persist a job/SkillRun before the network call. Calls run outside database
   transactions. A pending avatar is polled manually, respecting saved cooldowns.
4. Resume when the provider is ready. The compositor downloads only the verified provider CDN,
   reuses the exact speech track, burns aligned captions and disclosure, then validates the MP4.
   Output is H.264, 1080×1920, 24 fps, AAC 48 kHz, at most 300 seconds and 200 MB.
   Brief full-frame cutaways show exact approved FACT text with its evidence IDs. Their timing
   follows the original speech alignment; creative text is not promoted into factual cards.
   Text that cannot fit is skipped intact. The private captions file pins the script and card
   timeline to the reviewed manifest. These are editorial fact cards, not AI-generated B-roll.
5. Load the exact preview as an APPROVER. Separately check identity, pronunciation, lip sync,
   captions and disclosure. Structural media QA does not evaluate those perceptual qualities.
6. Approve or reject the exact manifest. Approved download rechecks source freshness, parent
   approval, current revisions, persisted media approval and the actual stored bytes.

No paid generation was used in the automated tests. A synthetic encode proves format and
compositing only; a real provider sample and human review remain required for live acceptance.

## Failure and recovery

Media state progresses through `CREATED`, `SPEECH_READY`, `IMAGE_READY`, `ASSETS_READY`,
`AVATAR_PENDING`, `AVATAR_READY`, `AWAITING_APPROVAL`, then `APPROVED` or `REJECTED`.
Policy violations, execution failures and uncertain submissions stay distinct as `BLOCKED`,
`FAILED` and `UNKNOWN_OUTCOME`.

An advisory lock serializes executors. Every job reserves spend and commits its attempt before
the external call. Successful local receipts allow recovery after a database checkpoint gap.
A process interruption without a receipt on a submission enters `UNKNOWN_OUTCOME`; no blind
paid replay is offered. Reconciliation against provider history is a manual administrative
operation and is not yet automated. Do not create replacement runs until that outcome is known.

Retryable failures persist bounded backoff and provider `Retry-After`. Non-poll stages allow
three attempts; polls allow ninety and at least two seconds between successful polls. Failed
and uncertain reservations are retained. Charges are not assumed refunded. The actual amount
remains unknown until separately reconciled with billing; the API does not invent charges.

Private files live below `.local/media/<tenant UUID>/<media UUID>/`. They are not a public media
host. Back up database and private files together. Host local storage and synchronous execution
are development foundations; production needs durable storage, workers and reconciliation.

## Provider choice and documented contracts

ElevenLabs provides timestamped speech: [API contract](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps)
and [model guide](https://elevenlabs.io/docs/overview/models). The supported profile choices are
`eleven_multilingual_v2` and `eleven_v3`; quality must be auditioned with the chosen voice.

HeyGen's [image-to-video API](https://developers.heygen.com/image-to-video) accepts a photo and
audio asset. This implementation uses Avatar IV via v3, with
[asset upload](https://developers.heygen.com/reference/upload-asset),
[video creation](https://developers.heygen.com/reference/create-video) and
[status polling](https://developers.heygen.com/reference/get-video). Generation submission has
no assumed idempotency support. User-provided arbitrary media URLs are never fetched.

Higgsfield adapters support typed estimates/submission/polling for optional future cutaways,
using [official API documentation](https://docs.higgsfield.ai/docs) and
[billing/retention rules](https://docs.higgsfield.ai/docs/concepts/billing-and-retention).
`HF_API_KEY_ID` and `HF_API_KEY_SECRET` are optional. **The runtime speaking workflow does not
dispatch Higgsfield cutaways yet.** Its adapter tests are not a live quality demonstration.
No provider subscription or credit purchase was made by this implementation.
