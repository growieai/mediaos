# Social carousel design

`social-editorial-v2` is the default for newly seeded visual configurations and
new creators. It is a 1080 × 1350 (4:5) carousel template for the existing
Instagram image delivery path. It does not claim compatibility with every
social network or create vertical video.

The cover uses a large headline, a prominent cropped character reference and a
contrasting text area. Interior slides alternate the paper and forest palette;
the last slide emphasizes the existing call to action. Sequence markers, lime
and coral accents are decoration, not metrics or verification badges. A creator
without an owned portrait gets an abstract identity tile. No person is invented
or borrowed to fill a missing reference.

New configurations use 72 px headlines, 48 px body text, 32 px CTAs and 26 px AI
disclosures. The body block is vertically centered within its measured region.
Every source character, evidence ID, caption and disclosure remains exact. The
renderer never shrinks type, rewrites claims or truncates text to make it fit.
Long content requires a new content revision. Failed visual QA produces no PNGs.

Migration 0019 retains the old `editorial-v1` geometry and adds exact fixed
regions for v2. The database rejects changed, off-canvas or incorrect-template
text bounds and preserves the existing content/visual approval checks. The
renderer engine identifier remains `pillow-editorial-v1`; the immutable visual
configuration's explicit template version and hash identify the design.

Run migration and seed, then create a **new render** using the newest visual
configuration. Previous render files, configurations and approvals remain
immutable. The old visual approval does not authorize the new artwork; review
and approve the exact new PNGs before export or separate social dispatch.

Design quality and a prepublication readiness assessment cannot predict
virality. Actual reach, shares and saves must come from attributed observations
or verified connected-account metrics. No decorative score is proof of future
performance.
