# Listicle winner skeletons (cycle 73)

Nine item maps of proven listicles. The writer adapts one per listicle run,
the same way it adapts the tenant's winner exemplar: it keeps the item order,
each item's role and the section hints, and writes the copy in this run's
style, headline template, brand voice and verified claims. A skeleton is never
a claims source and never reaches the gates -- every page still passes the
claims gate, proof lines, quote fidelity and the banned words unchanged.

Each skeleton fits inside the cartridge's fixed eight-section Structure
(`../cartridge.md`). Its `sections` hints say what to do in the hero, the
items, audience_fit, the FAQ and the closing block; they never add a section.

| id | styles | look | default for |
| --- | --- | --- | --- |
| `hormozi-value-stack` | reasons | lander | |
| `hormozi-mistakes` | mistakes | editorial | mistakes |
| `native-article` | reasons, mistakes | editorial | |
| `classic-n-reasons` | reasons | cards | reasons |
| `hidden-costs` | mistakes | editorial | |
| `switcher-reasons` | reasons | cards | |
| `myth-bust` | myths, tested | pillars | myths, tested |
| `buyers-checklist` | questions | scorecard | questions |
| `day-in-the-life` | reasons | pillars | |

## How a run picks one

`harness run ... --cartridges listicle --skeleton <id>` pins one. Without
`--style`, the run takes the skeleton's first style; with `--style`, the
skeleton must fit it. A pinned skeleton also sets the look (unless `--look`
names one) and narrows the seeded headline pick to its `headline_templates`
(unless `--headline-template` names one; an ineligible list falls back to
every eligible template).

Without `--skeleton`, the run picks from the skeletons that fit its style:
each skeleton tag found in the ad brief (angle, hook, promise, tone, features,
objections) scores 1, each tenant `angle_fit` word scores 2, the best score
wins (ties go to library order), and no match falls back to
`index.json`'s `default_by_style`. An auto-picked skeleton shapes the items
only; the look and headline pick stay as they were.

## Tenant hints

Tenant-specific fills live with the tenant, never here:
`tenants/<tenant>/listicle-library/skeletons.yaml` maps a skeleton id to
`angle_fit` (auto-pick words) and `item_hints` (sent to the writer as
`tenant_item_hints`, angles only).

## Files

- `index.json` -- the library order and `default_by_style`.
- `skeleton.schema.json` -- the shape every skeleton passes
  (`harness/skeletons.py` `validate`).
- `NN-<id>.json` -- the skeletons.

The 17 pre-sell headline swipes the source branch carried are the h01-h17
templates in `../headlines.yaml` (each keeps its branch id as an `alias`,
except h05/h14/h16, rewritten for the guardrails); see docs/HEADLINES.md.
