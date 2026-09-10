---
name: writer
purpose: Generate one landing-page cartridge's page.json from the facts pack.
inputs:
  - ad_brief.json
  - facts_pack.json
  - cartridge schema.json
outputs:
  - page.json
model: claude-sonnet-5
---

## What it does
One Claude call per cartridge. Takes the ad brief, the facts pack, and the
cartridge's schema.json, and writes page.json content that fits that
cartridge's structure and matches the verified facts.

## Rules
- page.json must validate against the cartridge's schema.json.
- Only uses claims present in facts_pack.verified_claims; never re-derives
  or extends claims from the ad brief directly.
- Never writes the byline, published/updated dates, the "Advertisement"
  label, the disclosure paragraph, the Sources list, or JSON-LD — those are
  render-stage output, not model output.
- One Claude call per cartridge; does not call itself in a loop or retry
  internally. Retries belong to gate-repair.
- Excludes EMF content entirely, matching the ban already enforced upstream.

## Done when
page.json exists, validates against the cartridge schema, and every claim
in it appears in facts_pack.verified_claims.
