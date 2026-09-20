# Build Plan — First Influencer

## Milestone 0 — repository can run
- Docker dependencies start.
- FastAPI `/v1/health` works.
- Console renders Sofía one-screen status.
- Unit tests pass.

## Milestone 1 — typed closed loop
- SourceInput
- ResearchPack
- ContentBrief
- CarouselDraft
- QAReport
- saved WorkflowRun

Acceptance:
- no publishable claim exists outside ResearchPack;
- QA can BLOCK;
- run is traceable end-to-end.

## Milestone 2 — real AI skills
Replace deterministic demo skill-by-skill, not all at once.

Order:
1. Creator skill with structured outputs.
2. Editor skill.
3. Research extractor.
4. QA reasoning.

Each replacement must retain deterministic validation around the model.

## Milestone 3 — real source ingestion
Start with 3-5 authoritative Spain sources, not the entire internet.
- fetch
- checksum/change detection
- store raw snapshot
- parse
- verify
- expire/recheck

## Milestone 4 — visual carousel render
- fixed templates
- Sofía reference lock
- headline/body constraints
- export 1080x1350
- artifact manifest

## Milestone 5 — approval console
One-screen modal flow:
- see what Sofía found
- inspect sources
- preview carousel
- approve/revise/block
- publish manually

## Milestone 6 — Instagram publishing
- OAuth/token storage
- dry-run
- publish one approved carousel
- record platform post id
- retry safely

## Milestone 7 — metrics loop
- fetch reach, saves, shares, comments, follows
- associate with content_id
- create learning object

## Milestone 8 — community loop
- classify comment
- retrieve verified context
- draft reply
- manual approval initially
- later auto-reply only for safe classes

## Milestone 9 — Growie conversion loop
- business identification
- business audit request
- Growie handoff
- attribution

## Milestone 10 — reels
Only after carousels + source QA + metrics are stable.
