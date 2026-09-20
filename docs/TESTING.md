# Test M0–M2 locally

The internal console is at **http://127.0.0.1:3000**. This checkout uses a separate native PostgreSQL instance and requires no AI API key. No publishing or image generation is available.

## Sign in and review

1. Open `.local/credentials.json` on this machine. Copy `tenant_id` and `tokens.OPERATOR` into the console, then choose **Connect / refresh**. Do not commit or share that file.
2. Open workflow `b740e4b4-0162-44ed-8c1d-b8db6a35ded5`. It contains the live BDNS opportunity `ES:BDNS:929780`. A fresh content revision has QA PASS and is **AWAITING_APPROVAL** for your review; earlier simulated approval remains in its immutable history.
3. Inspect `source_snapshots`, `research_pack_versions`, `content_briefs`, `content_asset_versions`, `qa_reports`, and **Audit trail**. The asset is structured carousel text; graphics are deferred.
4. An OPERATOR must not see an enabled approval action. Replace the token with `tokens.APPROVER`, reconnect, and reopen the workflow.
5. Review the exact current revisions, enter a comment, then choose **Approve exact revisions** or **Reject**. Approval must produce APPROVED and an immutable record; rejection must require revision. Nothing is published.
6. Open blocked workflow `41a74282-f2b4-4c04-bce0-9f457154d279`. It contains an unsupported-claim control from the local acceptance simulation. Approval must remain unavailable. The API also rejects a direct approval attempt.

The current handoff IDs are also in `.local/testing-handoff.json`. Live evidence expires under the mission policy. If you test after expiry, refresh official ingestion and generate current research; stale approval rejection is expected behavior.

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

## Dependencies and remaining limits

- **Ready locally:** Python 3.12, Node 24, standalone PostgreSQL, seeded internal credentials and the built console.
- **Docker Desktop:** optional for this native setup; required to run containers on this Windows machine. Hosted CI also verifies containers on Linux.
- **Cámara:** automated access remains disabled until the source owner permits it. Its recorded parser tests are available; BDNS and BOE are enabled.
- **AI/model provider:** no key is needed for current deterministic extraction, scoring and drafting. Semantic model extraction and creative model drafting are not implemented in this slice.
- **Long legal material:** the BOE parser retains documents exceeding its 100-fact bound as raw snapshots with an extraction failure rather than truncating legal conditions.
- M3 rendering, images, video, social publishing, external signup/creator UI and marketplace remain outside scope.
