---
name: design-audit
purpose: Review a rendered page against the tenant's brand tokens and cartridge rubric, and report a fix list.
inputs:
  - rendered index.html
  - brand tokens
  - cartridge rubric
outputs:
  - fix_list.md
model: claude-sonnet-5
---

## What it does
Loads the rendered page alongside the tenant's brand tokens (color, type,
spacing) and the cartridge's design rubric, compares them, and writes a
list of concrete mismatches and fixes.

## Rules
- Read-only against the rendered page; makes no edits to index.html,
  page.json, or any source file itself.
- Every item in fix_list.md names the specific element and the specific
  token or rubric rule it violates.
- Does not evaluate claim accuracy or copy content; that is the claims
  gate's and writer's territory, not this role's.
- Flags missing required render-stage elements (byline, dates,
  "Advertisement" label, disclosure, Sources list, JSON-LD) if any are
  absent, but does not add them.
- Consult `harness/design_skills/` (the vendored landing-page skill plus
  its adapter) for visual/structure notes. Apply only rules the adapter
  marked take or adapt. Do not flag Geist/Inter, hyphenated copy, a
  missing 680 px cap, or a missing JS word-reveal -- those are declined.
  Nested radius (B3) is a manual review note, not a gate.

## Done when
fix_list.md exists and every mismatch found against the brand tokens or
rubric is listed with enough detail for someone else to act on it.
