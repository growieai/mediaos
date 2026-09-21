# Sofía video direction and local preview

The selected target is **Sofía speaking directly to camera, with captions and cutaways**.
The first local deliverable is a playable 22-second vertical motion study. It shows close-up framing,
typography, three illustrated cutaways and proposed Spanish script captions. The face is a still
portrait with camera movement. There is no generated speech, lip movement or music. It is a concept
preview, not a final talking-avatar video or completed Milestone 10.

## Watch or reproduce

From the repository root, with the existing backend environment and local `ffmpeg` and `ffprobe`
available on PATH:

```bash
python scripts/dev.py video-preview
```

The command prints a new private `.local/video-previews/<UUID>` directory. Open `video.mp4` in a
video player. It also contains `poster.png`, `contact-sheet.png`, the exact `spec.json` and a strict
`manifest.json`. Encoding uses local Pillow and FFmpeg; no database, network or AI credentials are
needed. FFmpeg 8.1.1 was available on the prepared Windows host. It is an optional local preview
dependency, not installed into the API container. Output is 1080×1920 H.264/yuv420p at 24 fps.

The final 22-second concept is designed as follows:

| Time | Picture | Proposed speech / caption |
| --- | --- | --- |
| 0–4s | Sofía close-up, gentle camera move, large hook | ¿Una ayuda para tu negocio? Antes de ilusionarnos, tres preguntas. |
| 4–8s | Animated document/source illustration | Primero: ¿quién la publica? Vamos a la fuente oficial. |
| 8–12s | Animated calendar illustration | Segundo: ¿sigue abierta? Comprueba la fecha antes de preparar papeles. |
| 12–17s | Requirements illustration | Y tercero: ¿encaja con tu negocio? Lee los requisitos y revisa las condiciones. |
| 17–22s | Return to Sofía and closing line | Fuente, plazo y requisitos. Guarda estas tres preguntas para la próxima ayuda. |

Timing is an art-direction proposal. A real voice recording will determine the final edit and caption
timings. No actual subsidy, amount, deadline or individual eligibility is asserted by this example.
The current generated character appearance remains proposed and needs the user's visual review.

## Boundary with the application

The generic renderer reads a strict ConceptSpec; Sofía's example is data in
`characters/sofia/video_concept.json`. It rejects unsupported text, overflow, unsafe asset paths,
oversized/corrupt inputs and unbounded timelines. Every frame has fixed visible concept/silent/AI
labels as well as the configured disclosure. Preview flags cannot be changed to publishable=true.

The CLI creates a new output directory, streams raw frames into a bounded encoder process, validates
the resulting stream with ffprobe, checks input hashes again and writes the completion manifest last.
Failures may leave a partial directory without a manifest; they do not yield a completed preview.
The manifest pins the specification, portrait/font hashes, output hash, frame count, duration,
Pillow/renderer/encoder versions, proposed identity and silent/nonpublishable mode. Zero cost refers
to this local rendering run, not to the historical cost of producing imported design assets.

No workflow/approval record is created. No migration, tenant query, API route, delivery adapter or
console export path changes. Preview files are excluded from Git and stay outside `.local/renders`.
The runtime video/publishing flags remain off, and these preview files cannot be submitted as an
approved carousel package. There is no background process or public media hosting.

## What is needed for a real speaking video

The next single step is a short speaking-avatar proof using an approved portrait and a chosen
Spanish voice, followed by human review of identity, pronunciation, lip sync and expression.

One candidate is HeyGen's Photo Avatar API: its official guide supports a photo, script and voice ID,
with vertical video output. This is a documented capability, not an integration tested by this repo.
See [Photo Avatar](https://developers.heygen.com/photo-avatar) and
[voice selection](https://developers.heygen.com/docs/voices/search-voices), checked 2026-09-21.

A real call requires the selected provider's API access/credits, a voice choice and explicit
authorization to leave mock mode for paid video generation. If HeyGen is chosen, its documented
credential is `HEYGEN_API_KEY`; put it in the ignored local `.env`, never in chat or committed code.
Adding a key alone does not enable an adapter here. See its [API key guide](https://developers.heygen.com/docs/api-key).
No provider account was created, key inspected, asset uploaded or paid call made for this preview.
Instagram connection can happen separately after a video exists.

Production video work still needs persisted tenant-scoped render requests and attempts, typed provider
responses, unknown-outcome reconciliation, exact sourced scripts, audio/caption alignment, immutable
media revisions, guarded audiovisual QA/approval, durable storage and cost logging. The existing
carousel and community safeguards cannot be replaced by this standalone design preview.

## Verification

Verification on 2026-09-21:

- Full suite: 472 passed in 265.55s, including the 408 inherited tests and 64 initial preview cases.
- After the review fix: all 65 preview tests passed, including a new missing-label-glyph regression.
- Ruff check/format passed for 86 files; mypy passed for 65 source files.
- Independent review found one issue: fixed disclosure labels bypassed glyph/width preflight.
  All fixed labels now receive the same preflight; the follow-up review has no material findings.
- Actual encoding and ffprobe checks passed: 528 frames, 22 seconds, 1080×1920, H.264/yuv420p,
  24 fps, no audio stream. FFmpeg decoded the entire final MP4 without errors.
- Representative portrait/cutaway/closing frames and the contact sheet were visually inspected.
- No database migrations or frontend changes were needed. Hosted CI has not run for this branch.

The final local output is `.local/video-previews/a8990957-73a3-41bf-ac38-09b9217bda89/video.mp4`.
Its 2,340,399-byte file matches manifest SHA-256
`38904922dcc1859178c4a57108e616c5d11bdacfee592607d21badc27f04a10f`.
The preceding render produced identical MP4 bytes; the final rerender also exercised the corrected
label preflight. The regression suite reports one upstream Starlette/AnyIO deprecation warning.

Pure tests cover schema restrictions, duration bounds, asset containment, image/text rejection,
frame determinism, permanent labels, output stream checks and encoder lifecycle failures. No unit
test requires FFmpeg, a provider or Internet access; actual media encoding is a separate local check.
