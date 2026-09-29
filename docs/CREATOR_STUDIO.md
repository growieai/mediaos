# Creator Studio

This extension implements the requested category-led platform interface on top of the existing
tenant, evidence, workflow and approval foundation. It replaces the main engineering console.
The five workspace sections are Overview, Influencers, Content Studio, Channels and Insights;
creation and story review stay in dialogs rather than separate page flows.

## Try it locally

Use the standalone local environment, never an existing Growie production service.

```powershell
# Set ENABLE_EXTERNAL_CREATORS=true in the ignored .env, keeping AI_MOCK_MODE=true.
backend/.venv/Scripts/python.exe scripts/dev.py migrate
backend/.venv/Scripts/python.exe scripts/dev.py seed
cd apps/console
npm.cmd test
npm.cmd run build
cd ../..
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

Open `http://127.0.0.1:3000`, select **Sign in**, and use your administrator-provisioned
email/password account. See [user sign-in](USER_LOGIN.md). A valid session survives refresh;
signing out revokes it. Advanced access still accepts the tenant ID and a human access key
from `.local/credentials.json` for internal testing. ADMIN can exercise creation and review
locally; OPERATOR and APPROVER retain their separate permissions.

Choose **Create new** to pick a category, name, audience, language, tone and mission.
The new card is backed by PostgreSQL, including immutable initial configuration. Generic
creators begin with abstract identity artwork; the app does not fabricate a generated portrait.
Existing configured character references, including Sofía's, remain private authenticated images.

**Suggest with AI** offers three starting points for a name and audience after you choose a
category. **Draft mission with AI** offers three editable editorial missions based on the current
category, audience, language and tone. Choose **Use this starting point** or **Use this mission**
to insert a suggestion, then edit it before the final **Create new** action. Changing inputs or
steps clears suggestions so an old response cannot overwrite new work. Suggestions never save
a creator or publish content themselves.

The helper currently supports **mock previews only**, clearly labelled template-based drafts
with no AI model call or charge. It works without provider credentials. Live onboarding drafting
is not yet implemented; selecting real text mode does not silently enable it. The existing paid
content-model adapter is separate. The helper endpoint requires the same tenant membership,
OPERATOR/ADMIN permission and creator feature flag as creation. No migration is needed.

Choose **Create content** to provide a real source and exact evidence excerpts. An authorized
reviewer must attest the source before generation. Test evidence stays marked and cannot pass
publication QA. Content generation preserves exact evidence and returns a saved workflow.
The manual form requires an explicit claim type and source relationship; see
[MANUAL_SOURCE_CLASSIFICATION.md](MANUAL_SOURCE_CLASSIFICATION.md) for its evergreen-claim scope
and the structured evidence path required for funding, eligibility and dates.

Open a story to inspect its content, caption, disclosure, source and readiness suggestions.
**Design** generates the exact 1080×1350 carousel with the latest visual configuration.
Review every saved image. Content approval and visual approval are separate; downloading
the approved ZIP needs both. Old previews remain historical after a configuration/revision
change; their approvals never transfer to a new render.

For blocked, failed or revision-required stories, **Create replacement story** opens a fresh
source submission for the same creator. Use corrected evidence or shorter exact excerpts when
the design overflows. The replacement receives new QA and human approval; the original story
and its audit history remain saved. The Design tab also exposes this recovery action. Inline
editing of an existing carousel is not part of this screen; the existing typed revision API
remains available to internal tooling and still requires fresh QA and approval.

**Discover opportunities** is available only for creators whose mission has an editorial
policy. Its source selector exposes only enabled connectors, the request is bounded to one
page/five records, and repeated input resumes the same ingestion. Audience selection and
editorial checks precede draft creation. Newly created categories do not automatically enable
Spain intelligence or a crawler.

## Channels and posting

Instagram is the current connected publishing destination. Channels shows actual saved
connections, lets an ADMIN choose an influencer and start OAuth when dependencies are configured,
and never requests a social password. Connection setup alone does not publish.

After content and design review, use the story's **Publish** tab to review the exact connection,
JPEG images and caption, authorize that package and explicitly dispatch. The existing database
approval and uncertain-outcome protections remain authoritative. Additional networks and video
publishing are not represented as connected capabilities.

The local instance still needs a configured Meta app, an eligible professional Instagram account,
encrypted token storage, a supported Graph API version and public HTTPS callback/media endpoints
before live connection/posting can be demonstrated. See [SOCIAL_INTEGRATION.md](SOCIAL_INTEGRATION.md).
No post is sent by a normal test or by this interface upgrade.

## Design and readiness

`social-editorial-v2` uses a prominent opening headline, a portrait/abstract identity treatment,
alternating evidence layouts, a distinct closing slide and progress indicators. It preserves exact
text and mandatory disclosure. Fonts, fixed geometry, overflow and contrast remain validated.
An overflow requires revision rather than silently shrinking text below policy.

The **Content readiness** score is a versioned editorial checklist with component reasons and
improvement suggestions. It is not a reach estimate or empirical viral probability. It cannot
override missing evidence, stale revisions, QA BLOCK or required approvals. Insights does not
invent engagement data; actual platform observations remain attached to their saved posts.

See [SOCIAL_DESIGN.md](SOCIAL_DESIGN.md) and [CONTENT_READINESS.md](CONTENT_READINESS.md) for exact rules.

## API and database boundaries

- `GET /v1/studio/categories`: typed catalog of eight content categories.
- `GET /v1/studio/influencers`: current tenant's saved influencer configurations and capability flags.
- `POST /v1/studio/influencers`: typed, role/feature-gated, tenant-idempotent creator setup.
- `POST /v1/studio/onboarding-drafts`: typed, bounded mock draft preview; no saved artifacts or provider calls.
- `POST /v1/studio/source-drafts`: tenant-scoped, OPERATOR/ADMIN mock writing preview from a story title and saved creator; no provider calls or saved artifacts.
- `GET /v1/studio/influencers/{id}` and `/portrait`: protected detail and validated private image.
- `GET /v1/studio/overview`: actual workspace counts and the latest 50 workflows with source titles.
- `GET /v1/studio/workflow-runs/{id}/readiness`: read-only current-revision checklist.

Migration `0018` adds immutable creator receipts and a guarded atomic SQL transition. Migration
`0019` validates v2 rendering geometry without weakening v1 constraints. All new business data
uses tenant ownership, forced RLS and restricted runtime access. The original workflow states,
evidence relations, exact approvals, skill/cost logging and social guards are unchanged.

## Deliberate limits

On **Source → Story**, enter a title, leave **Original source text** empty, then choose
**Generate draft → Use draft**. The editable starter uses the saved creator's language, audience
and mission. Mock mode uses local templates and makes no AI call. A preview never overwrites
existing text, chooses factual excerpts or attests evidence. Changed inputs/access invalidate
pending previews; a failed preview can be retried without losing writing.

Using the starter fills an explicit generated origin, publisher and internal source relationship.
It remains test material after edits. Saving requires the same exact-excerpt and claim-classification
steps; the resulting immutable source has `source_type=GENERATED` and `is_fixture=true`. It cannot
be verified or approved for publication. **Start again with real source** clears the draft, excerpts
and source details; a saved draft offers **Create replacement story** to start a separate evidence
record. Generated writing is not an alternative to factual research.

Migration `0020` enforces generated provenance at the database boundary. A generated type,
reserved `generated:` origin or `source_draft_policy` metadata requires internal fixture status.
The existing verification, QA and approval guards continue to reject fixtures. No text classifier
can identify arbitrary copied or relabelled generated text; reviewers must still establish source
authenticity. Live source drafting remains unimplemented and fails explicitly outside mock mode.

This is an authenticated workspace, not anonymous public signup or a billing/marketplace product.
Category selection persists editorial direction; it does not create an AI portrait or a custom
model training job. Provider calls remain behind their existing explicit credentials, reviewed
prices, spending policy and execution flag. Mock mode remains functional without any AI key.
New category-specific official discovery policies, broader format rendering, hosted organizational
login and production deployment remain separate work. Existing live account/provider and local
physical-erasure gaps are documented in the completion and operations reports.

## Validation

Backend tests cover typed creator configuration, feature and role checks, tenant isolation,
concurrent idempotency, existing sourced-workflow integration, authenticated image access,
readiness provenance and v2 render/approval guards. Frontend behavior tests cover source Unicode
spans, retries, duplicate clicks, tenant/token/revision changes, OAuth result scope, exact QA
approval binding and the saved-render review flow. Normal tests do not contact social providers.

The [implementation report](CREATOR_STUDIO_IMPLEMENTATION_REPORT.md) records actual test,
build, browser and migration results. [Hosted CI for `a21efc6`](https://github.com/growieai/mediaos/actions/runs/36406451051)
passed all 1,403 backend and 151 frontend tests, Docker startup/acceptance and the production build.

### Onboarding assistance follow-up — September 28

The **Create new** and draft-assistance follow-up passed 100 focused backend tests, all 184
frontend tests, full backend lint/format/mypy (111 application files), and the production frontend
build including TypeScript. Tests exercise duplicate clicks, cancellation, errors, stale selection,
tenant/token changes, mock-only disclosure, malformed draft rejection, no preview writes and final
explicit creation. Existing migrations through `0019` passed from an empty disposable database;
this follow-up adds no migration.

An actual browser rehearsal created **Sage** in the standalone local workspace: Education →
suggested profile → chosen audience/name → mission suggestions → selected mission → manual edit
→ **Create new**. A separate authenticated GET verified exactly one saved creator and the exact
edited mission, audience and versioned configuration. A 390-pixel mobile viewport had no horizontal
page or dialog overflow. No provider call or social post occurred. Private evidence is retained in
`.local/onboarding-acceptance-report.json` and the onboarding screenshots; credentials and evidence
files are not committed. A full page reload and fresh sign-in also retained Sage's edited profile.

[Hosted CI for `2d9ff57`](https://github.com/growieai/mediaos/actions/runs/36409564071) passed
**1,477 backend tests** (no skips, one existing upstream Starlette/AnyIO warning), **184 frontend
tests**, quality/type checks, production build, clean migrations, Docker build/startup and
persisted API acceptance. The earlier CI link describes the pre-onboarding baseline.

### Source drafting follow-up — September 29

Local validation passed 37 new source-draft unit tests, 93 focused PostgreSQL/studio/foundation
tests with clean migrations through `0020`, all 226 frontend tests, backend Ruff/format/mypy
(112 application files), and the production frontend build including TypeScript. Two independent
local reviews found no remaining material issues after fixing response-publisher validation and
a stale reset handler. Normal tests made no external provider calls.

A browser rehearsal generated a preview for Sage, explicitly inserted it, edited the original
text, selected an exact test excerpt and saved the source. An independent authenticated API read
confirmed the exact edited text and generated/internal/fixture provenance; an attempted source
verification returned 409. The saved view provided a fresh replacement-story action. At 390 × 844,
the page and dialog had no horizontal overflow. Migration `0020` was also applied to the standalone
dev database and the rebuilt console/API restarted successfully. Private proof is retained in
`.local/source-draft-acceptance-report.json` and `.local/source-draft-*.png`; no credentials or
private proof files are committed. A subsequent simulated API execution of that saved fixture
reached QA BLOCKED, an exact approval attempt returned 409, and its ten audit events were retrievable.

[Hosted CI for `748e455`](https://github.com/growieai/mediaos/actions/runs/36483787929) passed the
full backend/PostgreSQL and frontend suites, lint/type checks, clean migrations, persisted
acceptance, production build, container builds/startup and standalone Compose/ingress validation.
