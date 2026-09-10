---
name: gate-repair
purpose: Re-run page-level gate checks against page.json and repair failures before falling back to the model.
inputs:
  - page.json
  - facts_pack.json
  - gate check results
outputs:
  - page.json (repaired)
  - gate check results
model: claude-sonnet-5
---

## What it does
Re-runs every page-level gate check against page.json. First applies
deterministic, no-model-call text fixes: hype-word synonyms, incidental
numerals written as words, and the fixed warranty sentence. If failures
remain, makes up to 2 real repair calls, each with a "REVISION REQUIRED"
block listing every failure seen so far, including failures from prior
attempts.

## Rules
- Deterministic pass runs first, always, model: none (deterministic) for
  that pass specifically. Only escalate to a model call if it doesn't clear
  all failures.
- Hard cap of 2 model-backed repair calls per run; a third failure is not
  attempted — the run stops and reports the remaining failures.
- Never relaxes or skips a gate check to make it pass; only edits page.json
  content.
- Each repair call sees the full accumulated failure list, not just the
  newest failure, so it can't fix one check by breaking a previously-passed
  one.

## Done when
Either all gate checks pass, or 2 repair calls have been used and the
remaining failures are reported for a human, without a partial or
silently-degraded page.json being treated as final.
