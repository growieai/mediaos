# Test internal comment reply review

This is the internal M8 foundation. No social account, webhook, sending client or paid model is
connected. A reviewed reply is a saved draft, not a sent message or permission to send one.

Run migration 0006 and seed, then build/restart the API and console. Seed versions the optional
community policy in the character configuration. New workflows pin the new configuration. Old
workflows keep their original immutable configuration and return HUMAN_REVIEW if it has no policy;
they are not silently upgraded.

## Manual review

1. At http://127.0.0.1:3000/, sign in using the local tenant and OPERATOR credential. Keep the
   credential file private. Open a new, sourced workflow created after the updated seed.
2. Obtain the normal human content approval for its exact current revisions. Community work does
   not replace that approval or the original content/evidence QA.
3. Under **Comment reply review**, open **Submit a comment for internal review**. Choose FIXTURE
   for synthetic testing. Use MANUAL only for an actual operator-supplied comment, clearly naming
   its origin. An internal request is not an externally verified platform event. Use an opaque
   participant reference instead of personal contact data.
4. Enter the exact comment and UTC capture time, then save it. The original text and provenance
   are immutable. Comments are never treated as evidence about business eligibility or consent.
5. Select source excerpts already claimed by the current carousel for a source request. Choose
   **Create / recover reply draft**. Repeating the unchanged request recovers the same review.
6. Inspect classification, reply text, fact IDs, mandatory AI disclosure, QA and attempt history.
   Sofía's initial configured phrases include `Gracias` and `¿Fuente?`. Recognition is exact apart
   from leading/trailing ASCII spaces. Other comments, including appended instructions, go to
   HUMAN_REVIEW. Acknowledgements require no facts; a source request without facts fails closed.
7. A FIXTURE must be BLOCKED from approval. For an eligible actual MANUAL comment, an APPROVER can
   check the exact text and evidence, acknowledge review, and choose **Approve exact draft (no
   sending)** or **Reject reply draft**. OPERATOR cannot approve. Approval ends at REVIEWED_DRAFT.
8. **Prepare new review request** allows an explicit new revision. The previous decision is
   historical. New parent content/research/QA or a newer reply review makes old approval attempts
   stale. Refresh evidence through the source workflow when it expires or conflicts.

The console never offers Send. No message reaches a social platform or another person.

## Verification

From the repository root with the standalone Media OS database running:

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py community-acceptance
```

The acceptance harness exercises persisted internal requests and simulated separate approver
identities. Its explicit fixture controls remain blocked. It does not claim real social comments,
external consent or actual human sign-off. Read `.local/community-acceptance-report.json` for the
current workflow/reply IDs and the checks actually completed. Tests reset only `mediaos_test`;
acceptance adds internal development records without resetting user data.

To exercise the running console proxy from `backend` on Windows:

```powershell
$env:ACCEPTANCE_API_URL='http://127.0.0.1:3000'
$env:ACCEPTANCE_VIA_CONSOLE='true'
.venv/Scripts/python.exe -m app.community.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
Remove-Item Env:ACCEPTANCE_VIA_CONSOLE
```

Linux/WSL uses `.venv/bin/python`. A connected account, verified inbound events, platform
permissions and an explicit dispatch policy are separate requirements for a future live community
integration. M8 does not implement conversion requests, business audits, DMs, automatic replies,
video or customer-facing creator features.
