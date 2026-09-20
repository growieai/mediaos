# Test the internal delivery rehearsal

This is the dry-run portion of the original Milestone 6. It proves the internal handoff of an
approved carousel. It does not connect Instagram, verify platform media requirements or post content.

1. Run migrations and seed, then start the API and console using LOCAL_DEVELOPMENT.md.
2. Open http://127.0.0.1:3000/ and sign in with the local operator identity.
3. Open a sourced workflow and create a passing render. Review the text and every slide.
4. Sign in with the approver identity and approve the exact content, then the exact render.
5. Sign back in as operator. Under the current render, select the enabled dry-run target and click
   **Run / recover delivery preflight**.
6. Inspect the saved `DRY_RUN_COMPLETE` receipt. It contains exact caption/media/package hashes,
   `network_performed=false`, `post_id=null` and `published_at=null`.
7. Repeating the same request recovers that receipt. An explicit new request performs a fresh check.
   Changing content requires fresh QA, content approval, rendering and visual approval first.

Use tokens only from the ignored `.local/credentials.json`; do not paste them into task messages
or commit them. OPERATOR cannot approve content. The server rechecks permissions and revisions
regardless of which buttons the browser enables.

From the repository root:

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py delivery-acceptance
```

For the running console proxy on the prepared Windows machine, from `backend`:

```powershell
$env:ACCEPTANCE_API_URL='http://127.0.0.1:3000'
$env:ACCEPTANCE_VIA_CONSOLE='true'
.venv/Scripts/python.exe -m app.delivery.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
Remove-Item Env:ACCEPTANCE_VIA_CONSOLE
```

The harness uses real internal repository policy evidence, separate seeded identities and simulated
approval requests. It writes `.local/delivery-acceptance-report.json`, checks idempotency, missing
approvals, cross-tenant denial and stale revisions, and leaves a fresh render awaiting your review.
It does not reset the development database. Normal pytest rebuilds only `mediaos_test`.

Local transient interruptions can retry up to three times with persisted backoff. Corrupt files,
invalid output and policy blocks require intervention rather than repeated retries. Historical
receipts stay available after revisions change, but cannot authorize any future delivery.

Live Instagram delivery still needs account connection, verified platform permissions/requirements,
secure token storage, HTTPS media hosting and safe reconciliation of uncertain publish outcomes.
Metrics need real posts and insights access; community needs approved inbound events and send
permissions; conversion needs a chosen consent-aware destination. These are not completed by a
successful dry run. Reels remain gated on a stable carousel and measured metrics loop.
