# Test reported metrics

This is the manual data foundation for M7. Instagram is disconnected, so no platform metrics are
fetched or independently verified. The application cannot infer that a reported post actually exists.

1. Run migration 0005, seed and build/restart the API and console.
2. Sign in as OPERATOR at http://127.0.0.1:3000/ and open a workflow with a historically approved
   content revision and visual render. Select that render in its history.
3. Under **Reported metrics and descriptive comparisons**, register an observation set. Choose
   FIXTURE for synthetic testing, or MANUAL for a self-reported actual post reference. Describe
   the origin of the data. The marker is permanent; neither mode is platform-verified.
4. Add a cumulative observation with a UTC timestamp, retained evidence text and metric definitions.
   Enter zero only when zero was actually reported. Leave unavailable counts blank for Unknown.
5. Add a later observation with exactly the same definitions. Choose the earlier and later rows,
   then save a descriptive comparison.
6. Inspect the baseline/current counts and deltas, exact snapshot IDs/hashes and limitations.
   A negative delta is retained as a reported decrease; corrections and definition issues may explain
   it. No cause, campaign winner or editorial change is inferred.

Historical approval establishes content lineage. It does not authorize another export or post after
content/source revisions change. Metrics registration does not modify the workflow or its approvals.
Every imported observation is immutable. Corrections need a new observation with appropriate evidence.
Different definitions or non-increasing observation times are rejected for comparison.

For a repeatable end-to-end simulation from the repository root:

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py metrics-acceptance
```

To exercise the running console proxy from `backend` on Windows:

```powershell
$env:ACCEPTANCE_API_URL='http://127.0.0.1:3000'
$env:ACCEPTANCE_VIA_CONSOLE='true'
.venv/Scripts/python.exe -m app.metrics.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
Remove-Item Env:ACCEPTANCE_VIA_CONSOLE
```

Use `.venv/bin/python` on Linux/WSL. The harness adds development records without resetting the
database. It uses simulated approval identities and explicitly FIXTURE metric values; it does not
pretend a carousel was posted. `.local/metrics-acceptance-report.json` contains the relevant IDs.
Normal pytest resets only the configured `mediaos_test` database.

The report operation is one bounded database transaction. Its report, successful deterministic
SkillRun, zero-cost CostEvent and audit event commit together. A database failure rolls them back;
retry the identical request key safely after recovery. Rejected input does not become a fake
successful report, and no network or model work is retried.

Live M7 still requires a real post, authorized insights access, verified metric definitions and a
connector preserving the raw platform response. MANUAL and FIXTURE records will not be relabelled
as verified when a connector is added later.
