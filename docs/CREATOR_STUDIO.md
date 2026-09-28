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

Open `http://127.0.0.1:3000`, select **Open workspace**, and enter the tenant ID and a human
access key from `.local/credentials.json`. ADMIN can exercise creation and review locally;
OPERATOR and APPROVER retain their separate permissions. Access stays in page memory.

Choose **Create influencer** to pick a category, name, audience, language, tone and mission.
The new card is backed by PostgreSQL, including immutable initial configuration. Generic
creators begin with abstract identity artwork; the app does not fabricate a generated portrait.
Existing configured character references, including Sofía's, remain private authenticated images.

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
- `GET /v1/studio/influencers/{id}` and `/portrait`: protected detail and validated private image.
- `GET /v1/studio/overview`: actual workspace counts and the latest 50 workflows with source titles.
- `GET /v1/studio/workflow-runs/{id}/readiness`: read-only current-revision checklist.

Migration `0018` adds immutable creator receipts and a guarded atomic SQL transition. Migration
`0019` validates v2 rendering geometry without weakening v1 constraints. All new business data
uses tenant ownership, forced RLS and restricted runtime access. The original workflow states,
evidence relations, exact approvals, skill/cost logging and social guards are unchanged.

## Deliberate limits

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

The implementation report records actual final test/build/browser results separately from this
usage guide. Hosted CI for baseline `484ce5f` passed; later changes need their own CI result.
