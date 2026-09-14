# Landing-page design (adapter, not the raw skill)

This repo vendors [elayadesign/ai-design-skills](https://github.com/elayadesign/ai-design-skills)
`landing-page-design` at `harness/design_skills/`. **Do not apply the raw
SKILL.md visual tokens.** The adapter
(`harness/design_skills/rules.json` + `adapter.py`) is the source of
truth: every rule is take, adapt, or decline.

When building, editing, or reviewing a page in this repo:

1. Read `harness/design_skills/README.md` and `harness design-skills explain`.
2. Follow taken/adapted rules only.
3. Never override the claims gate, the block gate, or a tenant's locked topics.

**Declined (do not do these here):** Geist/Manrope/Inter font rules; "no
hyphens in copy"; Tailwind type scale; the spacing token table; nested
radius as a gate; dark-mode hex palette; hero text gradients; 680 px
fixed hero width; icon libraries; IntersectionObserver per-word reveals
inside a block; money-back / 30-day guarantee copy a tenant does not
offer; silently `noindex`ing a page.

**Taken / adapted:** one CTA; no Learn-more/Submit; filler/placeholder
copy is a hard gate; leftover AI cliches (seamless, next gen, delve,
tapestry, "in the world of") are a hard gate; dead `#` links are a hard
gate; optional `tagline-reveal` and `risk-reversal` blocks on landing-style
cartridges; feature-grid uses a full border (not a single-sided one).

The article cartridge stays an editorial warm-up. Do not add a
tagline-reveal there.
