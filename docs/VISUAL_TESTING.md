# Test the rendered carousel and visual approval

This is the M3 visual extension to the persisted M0–M2 workflow. Use the internal one-screen console at **http://127.0.0.1:3000**. The current renderer needs no AI API key: it uses pinned fonts, a versioned template and the imported proposed character reference.

Content QA, human content approval, visual QA and human visual approval are separate gates. A passing render alone cannot be downloaded as an approved carousel. The server checks the exact current content, research, QA, visual configuration, render and both approval records again at export.

## Prepare a repeatable example

Run migrations and seed using the existing local development commands. Then, from `backend` on Windows:

```powershell
.venv/Scripts/python.exe -m app.rendering.acceptance
```

On Linux/WSL use `.venv/bin/python` instead. To exercise the already running local HTTP API:

```powershell
$env:ACCEPTANCE_API_URL='http://127.0.0.1:8000'
.venv/Scripts/python.exe -m app.rendering.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
```

Only loopback HTTP URLs are accepted. The harness does not reset the database and does not contact government sources or a publishing platform. It uses the real statement **“An OPERATOR cannot approve content.”** from `docs/SECURITY.md` as internal source evidence. It is an internal policy example, not a real business opportunity.

The harness creates persisted workflows, records separate OPERATOR/APPROVER requests, verifies both approval gates and the exported PNG hashes, then revises the content. Its approvals are **simulations**, not your own editorial sign-off. A new current content revision and render are left for your review.

Read `.local/visual-acceptance-report.json`. Confirm `status` is `PASS`, then use the workflow and render IDs under `user_review`. The report also identifies the unsupported-claim blocked workflow and historical acceptance approvals. It contains no bearer tokens.

The historical checked ZIP and first slide are saved as `.local/visual-acceptance/approved-carousel.zip` and `.local/visual-acceptance/slide-01.png`. They describe the earlier simulated approval. Because the harness subsequently changes the content, that old render is no longer exportable from the application; review the fresh render in the console.

## Review in one screen

To exercise the console's authenticated JSON/PNG/ZIP proxy as well, set
`ACCEPTANCE_API_URL=http://127.0.0.1:3000` and `ACCEPTANCE_VIA_CONSOLE=true` before running
the same harness. Its report will identify `CONSOLE_PROXY_HTTP`. This verifies the proxy contract;
the steps below are still needed to review the images and appearance personally.

1. Open `.local/credentials.json`, enter `tenant_id` and `tokens.OPERATOR` in the console, and choose **Connect / refresh**. Keep credentials local.
2. Open the report's `user_review.workflow_run_id`. Inspect the source, evidence, research, content and content QA. The workflow should be **AWAITING_APPROVAL**.
3. In **Carousel visual review**, select the current render. Inspect every slide, the caption and the exact AI disclosure. **Visual QA: PASS** means layout checks passed; the human decision should still be pending.
4. The OPERATOR cannot approve content or visuals. Sign in with `tokens.APPROVER`, reconnect and reopen the same workflow.
5. Review the sourced content, then choose **Approve exact revisions**. This records the content approval and changes the workflow to **APPROVED**.
6. Review the slides again, including the character's appearance. Tick **I reviewed every slide, the caption, the AI disclosure and the character appearance**, add an optional comment, then choose **Approve exact render**.
7. Choose **Download approved carousel ZIP**. The archive includes numbered 1080 × 1350 PNGs, `caption.txt` and the exact manifest. Download does not publish anything.

To reject a visual instead, choose **Reject this render** before approving it. That decision is immutable. An OPERATOR must prepare a new render request after the required changes; a rejected render cannot later be converted into an approval.

For another current workflow, an OPERATOR can select the latest visual configuration and choose **Create / recover this render request**. Repeating that request reuses its saved render. **Prepare new render request** creates a new request key; it does not silently change or reapprove an existing render.

## Expected negative results

- Visual approval before content approval is denied. OPERATOR approval requests are denied even if a button or request is forged.
- Unsupported factual content receives content QA **BLOCKED**, and cannot be rendered or approved. Use `blocked_workflow_run_id` from the report to inspect this case.
- Text overflow produces **REVISION_REQUIRED** rather than smaller hidden text or a truncated factual statement. Read the manifest findings and revise the content.
- A changed content, research or QA revision invalidates the old render's approval/export eligibility. A newer visual configuration or a newer render also requires review of the current version.
- Missing or changed PNG bytes fail integrity checks. An approved manifest cannot authorize arbitrary replacement images.
- Another tenant's context cannot retrieve or export the render. The harness checks unauthorized tenant-context substitution; PostgreSQL integration tests exercise separate tenants and RLS.

Historical previews may remain visible. Approval and export recheck the current policy and source freshness. Live-source material that has expired must be refreshed through the evidence workflow before use.

## Character reference and operational limits

Sofía's `characters/sofia/references/v1` portrait and reference sheet are **proposed**, not a finalized identity. The imported design assets were created with the built-in image-generation tool. Their metadata records that the tool did not report model, token or monetary cost details; unknown cost is not recorded as zero. A visual approval reviews the exact pinned image used in that render and does not silently declare the canonical character identity finalized.

The application does not yet call a runtime image-generation API. Per-render execution uses the deterministic renderer and records zero model tokens/cost. Keep the local deterministic/mock configuration for reproducible testing unless a separate provider integration has been configured and verified.

Rendered files currently live in private local storage under `.local/renders` (or the configured asset storage path). Production object storage, replication, retention and recovery remain operational follow-ups. The database validates provenance, immutable manifests and approval lineage; the server validates the actual PNG files. It does not claim that PostgreSQL independently inspects image pixels.

The current slice exports approved files for review. Instagram connectivity, public publishing, video, replies and customer-facing creator features remain outside this visual test flow. Cámara access permission is unrelated to rendering and is not required for this test.
