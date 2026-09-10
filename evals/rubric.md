# Page Score Rubric

Four axes, each scored 1-5. Score the rendered page as a whole, not just
the copy in isolation.

## Angle Match
Does the page carry through the ad's actual angle, not a different one?
- **1** — Page argues a different angle than the ad, or contradicts the hook.
- **3** — Same general theme, but emphasis, tone, or CTA has drifted from the ad.
- **5** — Direct continuation of the ad's hook, promise, and CTA.

## Brand Fit
Does the page look and read like it belongs on the tenant's site?
- **1** — Wrong voice or visual register; a mismatch obvious at a glance.
- **3** — On-brand overall, with a few word or layout choices a designer would flag.
- **5** — Indistinguishable in voice and layout from the tenant's other published pages.

## Claim Safety
Does every claim on the page hold up against verified sources?
- **1** — Contains a claim that is false, unverifiable, or the gate should have caught.
- **3** — All claims are true but at least one is stated more strongly than the source supports.
- **5** — Every claim traces cleanly to a verified source, no overstatement.

## Would Publish
The holistic call: ship this page as-is?
- **1** — Would not publish; needs a rewrite or a new run.
- **3** — Publishable after one specific, nameable fix.
- **5** — Would publish as-is, no changes needed.

## How to record
Run:

    harness score <run-dir> --angle N --brand N --claims N --publish N [--by NAME] [--note "..."]

This appends one JSON line to `tenants/<tenant>/evals/scores.jsonl`. Each
line is one reviewer's scores for one run. Do not edit or delete a past
line — to re-score, run the command again and let both lines stand.

## Threshold
A page is shippable when the mean "would publish" score across all
recorded reviews for that run is >= 4. A low score on any other axis is a
reason to look closer, but the publish bar itself is set by this axis
alone.
