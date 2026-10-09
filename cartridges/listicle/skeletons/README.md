# Listicle winner skeletons (cycle 73)

Ten item maps of proven listicles (cycle 98 added `named-cause`). The writer adapts one per listicle run,
the same way it adapts the tenant's winner exemplar: it keeps the item order,
each item's role and the section hints, and writes the copy in this run's
style, headline template, brand voice and verified claims. A skeleton is never
a claims source and never reaches the gates -- every page still passes the
claims gate, proof lines, quote fidelity and the banned words unchanged. The
one exception is the item count (cycle 100): a skeleton's optional
`item_range` replaces the cartridge's 5-7 for its pages.

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
| `named-cause` | reasons, mistakes | editorial | |

`named-cause` (cycle 98, from the Nourial, Jevi, Swarva and Citrus Root
listicles): the hook names the reader's moment, the lede names ONE cause in
plain words, item 1 states the cause as one mechanism sentence (its proof is
a verified claim or the ad speaker's own quoted words), items 2-4 are signs
(the sign, why the cause produces it, what the product changes), item 5 is
why the usual fixes did not hold, and the closing block is the product as the
fix with the fixed price, financing and warranty lines. Its headline
templates are nc1 "N Signs \<cause\> Is \<harm\>" and nc2 "N Reasons Why
\<common_solution\> Isn't Enough" (docs/HEADLINES.md). Cycle 100 (owner
decision 2026-10-09): 3, 4 or 5 items, never more (its `item_range`, read
by the item gate; every other skeleton keeps the cartridge's 5-7).
`item_plans` hold the 4-item map (cause, two signs, past fixes) and the
3-item map (cause, one sign, a second sign merged with why the usual fixes
did not hold); `--items N` picks one.

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
