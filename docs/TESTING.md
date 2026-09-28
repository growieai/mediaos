# Test the local Media OS loops

The current UI is [Creator Studio](CREATOR_STUDIO.md), replacing the JSON-first console.
Use **Open workspace**, **Influencers**, **Content studio** and the story's **Content / Design /
Video / Publish / Activity** tabs. Historical instructions below describe earlier handoffs.
The schema head is now `0019`; migrate, seed and rebuild after updating. Creator creation is
explicitly enabled with `ENABLE_EXTERNAL_CREATORS=true` in the local environment.

Baseline commit `484ce5f` has now passed [hosted CI](https://github.com/growieai/mediaos/actions/runs/36044067134).
That result does not substitute for validation of later Creator Studio changes.
Creator Studio commit `a21efc6` has also passed its own
[hosted verification](https://github.com/growieai/mediaos/actions/runs/36406451051): 1,403 backend
tests, 151 frontend tests, clean migrations, build and Docker API acceptance. See the
[implementation report](CREATOR_STUDIO_IMPLEMENTATION_REPORT.md) for local browser evidence and limits.

The internal console is at **http://127.0.0.1:3000**. This checkout uses a separate native PostgreSQL instance and requires no AI API key for its mock and deterministic loops. It supports sourced carousel drafts, deterministic PNG rendering, separate content/visual approval, checked ZIP export, delivery dry runs and reported metrics. Opt-in real text, speaking video, connected Instagram/insights/replies and signed conversion handoff are implemented, but live integration flags remain disabled and provider/account credentials are not configured.

On September 25, 2026, the preserved local database was migrated through `0017`, the frontend production build passed, and the content/render/delivery/metrics/community acceptance rehearsal passed through the running console proxy. This rehearsal uses simulated approvals and observations; it does not prove real voice/video generation, posting or delivery. See the [completion report](MILESTONE_COMPLETION_REPORT.md) for exact release and hosted CI results.

The M2-specific review below preserves its historical handoff IDs, not a promise that those artifacts remain fresh. For the later capabilities, follow
[visual testing](VISUAL_TESTING.md), [delivery preflight testing](DELIVERY_TESTING.md) and
[reported metrics testing](METRICS_TESTING.md). Their acceptance reports supply current local IDs.
The internal [comment reply review](COMMUNITY_TESTING.md) saves drafts and human decisions without sending messages. The separately configured [connected social integration](SOCIAL_INTEGRATION.md) adds account linking, exact publication/reply authorization and explicit dispatch; connecting or approving content alone never sends anything.

## Sign in and review

1. Open `.local/credentials.json` on this machine. Copy `tenant_id` and `tokens.OPERATOR` into the console, then choose **Connect / refresh**. Do not commit or share that file.
2. Open workflow `b740e4b4-0162-44ed-8c1d-b8db6a35ded5`. It contains the live BDNS opportunity `ES:BDNS:929780`. At the original M2 handoff, a fresh content revision had QA PASS and was **AWAITING_APPROVAL**; earlier simulated approval remains in its immutable history. Inspect the current state and evidence freshness before deciding.
3. Inspect `source_snapshots`, `research_pack_versions`, `content_briefs`, `content_asset_versions`, `qa_reports`, and **Audit trail**. M2 creates the structured carousel text. M3 can render that exact content into PNG graphics; its separate visual approval and export steps are covered in [VISUAL_TESTING.md](VISUAL_TESTING.md).
4. An OPERATOR must not see an enabled approval action. Replace the token with `tokens.APPROVER`, reconnect, and reopen the workflow.
5. Review the exact current revisions, enter a comment, then choose **Approve exact revisions** or **Reject**. Approval must produce APPROVED and an immutable record; rejection must require revision. Nothing is published.
6. Open blocked workflow `41a74282-f2b4-4c04-bce0-9f457154d279`. It contains an unsupported-claim control from the local acceptance simulation. Approval must remain unavailable. The API also rejects a direct approval attempt.

The historical M2 handoff IDs are also in `.local/testing-handoff.json`. Later acceptance reports under `.local` identify their own newly created runs. Live evidence expires under the mission policy. If you test after expiry, refresh official ingestion and generate current research; stale approval rejection is expected behavior.

## Speaking video and conversion review

Select a workflow and open **Speaking video**. With `tokens.ADMIN`, expand **Configure voice, verified prices and budget** to inspect or create versioned setup. Sofía's selected Sara Martin voice ID is `ODO4sbmD3pTjhgRVVRP6`; authenticated account access, current prices and an explicit spending limit still need verification. Keep credentials in the ignored server environment, never these forms. Saving a profile or policy does not generate media. An OPERATOR prepares a request from exact current approved content, then executes separately only when providers and the live flag are configured. An APPROVER reviews the resulting exact video. Follow [SPEAKING_VIDEO.md](SPEAKING_VIDEO.md) and [VOICE_SELECTION.md](VOICE_SELECTION.md). No actual speaking sample is available from the offline tests.

Open **Consent and manual handoff requests** to inspect a saved conversion request, then its **External handoff** view. The view shows business evidence, provisioned transport, exact intent and authorization/receipt history. Review and explicit dispatch are separate actions. See [CONVERSION_TESTING.md](CONVERSION_TESTING.md) for manual export and [CONVERSION_DELIVERY.md](CONVERSION_DELIVERY.md) for signed handoff setup. No real recipient is configured; no external delivery is claimed. Fixture consent/identities cannot be promoted into a live handoff.

## Discover another opportunity

Sign in as OPERATOR and expand **Official-source intelligence**. The source IDs and a bounded request are shown. Use the BDNS source ID, set `query` to `PYME`, and choose dates covering the recent period. Keep `max_pages: 1` and `page_size: 5` initially. Reusing the same idempotency key returns/resumes the same ingestion; use a new UUID for a new poll.

Choose **Discover / resume ingestion**, inspect **Evidence / versions**, then **Score audiences**. Select the persisted influencer, mission and audience before **Create sourced draft**. A missing deadline, unknown eligibility, conflict, expired evidence, or recently covered opportunity may result in HUMAN_REVIEW, IGNORE or WATCH rather than a draft. This is intentional; the workflow must not manufacture eligibility to produce content.

## Start and stop on this Windows machine

From the repository root, with the standalone PostgreSQL service running:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1 -Stop
```

The launcher starts hidden API/console processes bound only to 127.0.0.1, checks authenticated readiness, and writes process IDs/logs under `.local`. Repeated starts reuse its own running processes. Stop verifies each recorded process still belongs to this checkout before stopping its tree, and leaves PostgreSQL alone. After a PC restart, start your standalone PostgreSQL instance first. Build again after frontend changes.

For a fresh machine or Docker development, follow `LOCAL_DEVELOPMENT.md`: setup → up → migrate → seed → test → check → dev. Linux/WSL can also run the two foreground host commands in that guide. Do not reuse credentials from another database.

## Repeat verification

From `backend` on Windows:

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests migrations
.venv/Scripts/python.exe -m ruff format --check app tests migrations
.venv/Scripts/python.exe -m mypy app
$env:ACCEPTANCE_API_URL='http://127.0.0.1:8000'
.venv/Scripts/python.exe -m app.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
```

The optional URL makes acceptance use the actual running HTTP API, including Docker. Only loopback URLs are allowed so local credentials cannot accidentally be sent to a remote server. Without it, acceptance uses the in-process API. Tests rebuild only `mediaos_test`; your `mediaos_dev` review data is preserved.

Run `.venv/Scripts/python.exe -m app.intelligence.acceptance` separately for live government-source acceptance. It uses bounded public requests and controlled, explicitly marked negative fixtures in a separate verification tenant. Its approver calls are simulations; the prepared review above is for your own decision.

The later local acceptance commands are `python scripts/dev.py visual-acceptance`,
`python scripts/dev.py delivery-acceptance`, `python scripts/dev.py metrics-acceptance` and
`python scripts/dev.py community-acceptance`, run from
the repository root after migration and seed. The metrics harness includes the visual and dry-run
delivery checks, then imports explicitly synthetic observations and saves a descriptive comparison.
The community harness also verifies classification, sourced reply drafts and separate human-review simulation. These simulations neither publish a post or reply nor prove real audience performance. Normal tests use only the disposable database; do not run two PostgreSQL pytest sessions concurrently.

## Dependencies and remaining limits

- **Ready locally:** Python 3.12, Node 24, standalone PostgreSQL, seeded internal credentials and the built console.
- **Docker Desktop:** optional for this native setup; required to run containers on this Windows machine. Hosted Linux CI has demonstrated containers for earlier commits; consult the [completion report](MILESTONE_COMPLETION_REPORT.md) for the exact current release status.
- **Cámara:** automated access remains disabled until the source owner permits it. Its recorded parser tests are available; BDNS and BOE are enabled.
- **AI/model provider:** no key is needed for deterministic extraction, scoring, mock drafting or rendering. [Optional real creator selection](REAL_MODEL_EXECUTION.md) is wired into persisted workflows with strict typed selection, reservations and usage records. Mock mode remains selected; real calls require credentials, an explicit model and a current priced tenant policy.
- **Visuals:** deterministic PNG rendering, exact visual approval and ZIP export are available. Sofía's proposed reference images still need the user's appearance review. The runtime does not generate new images.
- **Delivery, metrics and community:** saved dry-run receipts and immutable MANUAL/SELF_REPORTED or FIXTURE observations are available. Connected publishing, platform observations and signed inbound comments/separately authorized replies are implemented. Instagram remains disconnected; a real post, insights observations, inbound comment and reply remain unverified. Unknown counters remain unknown.
- **Speaking video:** persisted ElevenLabs/HeyGen generation, caption/cutaway composition and separate audiovisual review are implemented. Provider credentials, voice access, verified rates/budget and the first real sample are pending. No video publishing is implemented.
- **Conversion:** exact consent review, manual export and separately authorized signed delivery/revocation are implemented. A real recipient and consent/handoff acceptance remain pending; local physical retention/purge is not implemented.
- **Long legal material:** the BOE parser retains documents exceeding its 100-fact bound as raw snapshots with an extraction failure rather than truncating legal conditions.
- Runtime image generation, optional Higgsfield cutaways, automatic social actions/DMs, public signup and marketplace remain deferred. Authenticated, feature-gated creator setup is now available in Creator Studio. Production deployment needs a chosen standalone host and its own acceptance; it is not established by the local rehearsal.
