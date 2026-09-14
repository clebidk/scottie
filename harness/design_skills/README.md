# Design skills (a separate harness component)

This directory vendors [elayadesign/ai-design-skills](https://github.com/elayadesign/ai-design-skills)
as a first-class harness component. It is **not** a cartridge and **not** a
block. Blocks stay layout; cartridges stay page types; this component is the
design-skill pack a writer, a reviewer, or an implementing agent consults,
plus the deterministic checks we could take from it without breaking the
harness's own rules.

```
harness/design_skills/          this component
harness/blocks/                 layout (composed by cartridges)
cartridges/<name>/              page types
tenants/<name>/                 company data
```

## What is here

| Path | What it is |
|---|---|
| `landing-page-design/SKILL.md` | Upstream skill, MIT, byte-identical. Do not edit. |
| `LICENSE` | The upstream MIT license (attribution required). |
| `SOURCE.json` | Repo URL, vendored commit, date. |
| `rules.json` | Every A/B rule extracted, with a take / adapt / decline decision. |
| `adapter.py` | Loads the decisions; `harness design-skills list` / `explain`. |
| `gate.py` | Deterministic checks for the *taken* measurable rules. |

## How it is used

1. **As itself.** `harness design-skills list|explain|check` inspects the
   pack without running a generation. An implementing agent reads
   `rules.json` before applying anything from the raw skill.
2. **As improvement.** `gate.py`'s hard checks are wired into
   `repair.check_page_gates` (so the writer repair loop can fix them). Soft
   checks land in REVIEW.md next to the existing advisory warnings. Two
   new blocks (`tagline-reveal`, `risk-reversal`) carry the skill's
   layout patterns the block gate already allows.

## What this component will never do

The raw skill's visual system (Geist/Manrope, Tailwind type scale, hex
dark-mode palette, 680 px hero cap, IntersectionObserver word-reveals,
"no hyphens in copy") **declines**. Those fight the block gate (CSS
variables only, no scripts, no fixed width over 390 px), the tenant's
brand tokens, and locked-topic copy. The adapter is the source of truth
for what applies here; the skill file is the source we adapted from.
