# Claude Code brief: write the playbook into the harness — and keep the design-skills pack as its own component

Two sources. Treat them as **separate components** that also improve the
existing engine. Neither one rewrites the claims gate.

1. **Amin (@eCom_Amin), "how to cook killer advertorials with fable 5.1
   (for ecom google ads)"** — X article 2097352396834033664, posted
   2026-09-13 via https://x.com/ecom_amin/status/2099200132717928853.
   Playbook for *what the page says and when the brand appears*.
2. **[elayadesign/ai-design-skills](https://github.com/elayadesign/ai-design-skills)**
   (`landing-page-design` skill, MIT, vendored commit
   `1c1e97cb9878e236552c772092dda7adcdddbcb2`). Pack for *how a landing
   page is structured and how it looks*. Already landed in this repo as
   `harness/design_skills/` — do not re-vendor it, extend it.

The X account attached to this environment is not enrolled for API
reads, so the article body could not be fetched here. The playbook
mapping below uses the article's own public preview plus what this
harness already does. If you can open the article, fill any remaining
`[CONFIRM IN ARTICLE]` rows before changing a cartridge rule.

---

## Objective

Two jobs, in this order:

**A. Keep `harness/design_skills/` a separate component.** It is not a
cartridge and not a block. The raw `SKILL.md` is evidence. `rules.json`
+ `adapter.py` decide take / adapt / decline. `gate.py` holds the taken
measurable checks. `harness design-skills list|explain|check` is the
CLI. New upstream skills land as new folders under
`harness/design_skills/<skill-id>/` the same way.

**B. Use both sources to improve the harness**, with the harness's own
discipline: every claim still traces to a verified source; the gate, not
the model, owns quality; blocks are layout, never copy.

The article's headline result, from its own preview: "the highest-converting
page we've ever shipped for an ecom account had no pricing, no buy button,
and no brand name anywhere in the first 900 words — it pulled 4-13x ROAS on
keywords the product page [could not win]." The article cartridge already
delays the brand to the close and allows exactly one soft CTA — but
nothing *measures* the warm-up window. That is the kind of rule this
harness turns into a deterministic gate.

The skill's headline rule: **one offer → one audience → one primary
action**, plus a non-negotiable visual system. Most of that visual
system **declines** here (it fights the block gate and the tenant's
brand tokens). The structure half adapts onto existing cartridges. The
copy tells we could measure (no filler, no leftover AI cliches, no dead
`#` links) are already hard gates.

---

## Repo map for the implementing agent

- Engine: `harness/` (company-agnostic). The claims gate is
  `harness/claims.py` — do not rewrite it; add checks in its established
  pattern (a `find_*_violations(page_json)` function, no-op when the
  section is absent, wired into `gate_page_json`).
- Deterministic non-claim checks: `harness/pagechecks.py` (image
  allowlist, internal links, HTML validity, JSON-LD, block picks,
  `find_design_skill_violations`).
- Repair loop: `harness/repair.py` (`fix` = deterministic pre-repair,
  `reask` = model repair, `exception` = ClaimsGateFailure STOP).
- Cartridges: `cartridges/<name>/{cartridge.md, schema.json,
  template.html, rubric.md}` — tenant-neutral, `{{ tenant.* }}`
  placeholders.
- Layout blocks: `harness/blocks/` (registry.json + `<name>/block.html` +
  optional `block.css` + `screenshot.svg`; the block gate is
  `harness/blocks/gate.py`). Count stays in the plan's 20–30 window.
- **Design-skills pack (separate component):**
  `harness/design_skills/` — README.md is the contract. `rules.json` is
  the decision table. Never apply the raw SKILL.md visual tokens.
- Tenant data: `tenants/peak-saunas/` (never modify
  `claims/verified.json`, `vocab.yaml`, `authors.yaml`; additions go to
  `claims/pending.json` with a source).
- Baseline parity: `python -m evals.fake_run <fixture> --tenant
  peak-saunas` byte-compares against `evals/baseline/`; the suite runs
  it as a test. New hard gates must pass the canned pages.
- Runbook: `docs/PROMPTING.md`. Baseline doc: `docs/KIMI-BASELINE.md`.

---

## Hard constraints (neither source overrides these)

- Every claim on a page traces to `claims/verified.json` through the
  gate. If a playbook or skill calls for a claim the store doesn't have
  (a ROAS figure, a 30-day guarantee, a statistic), it goes to
  `claims/pending.json` with a source — never into copy.
- Locked topics stay locked: warranty and financing are fixed sentences;
  price comes from live Shopify data; review stats from live Judge.me.
  Absolute bans (EMF, competitor names, hype words) apply to any new or
  changed cartridge. **Money-back / 30-day guarantee / white-glove
  delivery / $/mo widgets are prohibited outputs** for the first tenant
  and for any tenant with the same lock.
- Blocks are layout, never copy. Blocks cannot contain `<script>` or
  inline handlers. Colors are CSS variables. No fixed width over 390 px.
- The tenant's brand tokens own type and color. The engine never
  hard-codes Geist, Manrope, Inter, or a hex palette.
- One commit per logical change; full suite (`pytest -q`) and
  `ruff check` green at every commit; behavior-preserving work proves
  itself against `evals/baseline/`.

---

## Component A — design-skills pack (already landed; extend, don't re-do)

```
harness/design_skills/
  README.md                         contract
  SOURCE.json                       repo, commit, MIT
  LICENSE                           upstream MIT (attribution required)
  landing-page-design/SKILL.md      upstream, byte-identical — do not edit
  rules.json                        every A/B rule × take|adapt|decline
  adapter.py                        list / explain
  gate.py                           taken measurable checks
  __init__.py                       LANDING_CARTRIDGES, loaders
```

CLI: `harness design-skills list`, `explain [--action take|adapt|decline]`,
`check <page.json> [--cartridge NAME]` (exit 2 on a hard fail).

Cursor rule (adapter, not the raw skill):
`.cursor/rules/landing-page-design.md`.

### What is already taken (do not duplicate)

| Skill rule | Harness home |
|---|---|
| B8 filler (Lorem Ipsum, John Doe, Acme Corp, SmartFlow) | `design_skills.gate.find_filler_copy_violations` → `pagechecks.find_design_skill_violations` → `repair.check_page_gates` |
| B8 leftover cliches (seamless, next gen, delve, tapestry, "in the world of") | `find_ai_cliche_violations` (elevate / unleash / game-changer stay tenant vocab so the synonym repair still owns them) |
| B9 dead `#` / `javascript:` urls | `find_dead_link_violations` (same-page `#faq` is fine) |
| A4 generic CTA (Learn more / Submit / Click here) | soft warning `find_generic_cta_warnings` |
| B11 tagline-reveal | `tagline-reveal` block + optional `page.tagline.lines` on longform / product-page / comparison; soft warning when absent. **Never on article.** |
| A4 risk-reversal | `risk-reversal` block; comparison already had the field; copy is a verified claim |
| B4 no single-sided card border | `feature-grid` now uses a full border |
| A4 one primary CTA | already `claims.find_second_cta_violation` + `repair.find_cta_violation` |

### What is declined (do not implement)

B1 fonts / no-hyphens / Tailwind scale; B2 spacing table; B3 nested
radius as a gate; B4 dark-mode hex; B5 hero text gradient and 680 px
cap; B6 icon libraries; B7 JS word-reveals inside a block; B8 round-fake
number heuristic; B9 app-UI state sets; A6 section-by-section writer;
A7 silent noindex; A4 money-back framing.

### How to add the next upstream skill

1. Vendor `SKILL.md` + license under `harness/design_skills/<skill-id>/`.
2. Append rules to `rules.json` with a take/adapt/decline reason each.
3. Put new measurable takes in `gate.py` with a discrimination test.
4. Do not fold the skill into `harness/blocks/` or a cartridge.

---

## Component B — Amin advertorial playbook (still to implement)

The article body was not retrieved in this environment. Implement from
the preview first; confirm numbers against the article when you can open
it.

### Mapping (preview → harness)

| Playbook (from the preview) | What the harness already does | What to add | Home |
|---|---|---|---|
| No pricing, no buy button, no brand name in the first N words (preview says 900) | Article delays the brand to the close and allows exactly one soft CTA. Nothing measures the window. | **Warm-up window gate.** Walk prose in reading order; for the first N words assert: no tenant name / display name, no `$` figure, no CTA link text/url rendered. Cartridge-scoped (article, maybe listicle). `[CONFIRM IN ARTICLE]` the exact N and which page types. | `harness/pagechecks.py` `find_warmup_window_violations`; wire into `repair.check_page_gates`; discrimination tests |
| Editorial structure that earns the click before the offer | Article: open → body → alternatives → how-it-works → turn → close, 1,000–1,600 words, one soft CTA | Map the article's section formulas onto `cartridges/article/cartridge.md` + `schema.json` descriptions (prompt wording). Do not invent sections the article does not prescribe. | `cartridges/article/` |
| A page that can win keywords the PDP cannot | Article + listicle already exist for cold/education traffic; product-page is the PDP-shaped cartridge | Do not collapse article into a product page. The warm-up gate is what makes the difference measurable. | gate, not a new cartridge |
| Layout patterns the article repeats | `harness/blocks/` already has heroes, proof rows, FAQ, CTAs | Any *repeated layout* the article shows becomes a block (layout only). Confirm against the article before adding. | `harness/blocks/` |
| A ROAS / conversion claim in the write-up itself | Claims gate | A 4–13x ROAS figure is the article author's result, not a tenant claim. Never print it on a generated page. | `claims/pending.json` only if the tenant later sources its own figure |

### Candidate: the warm-up window gate

Draft shape (implement this once N is confirmed):

- `pagechecks.find_warmup_window_violations(page, tenant, *, word_limit, cartridge_name)`
- Reading order = `textutil.walk_page` over prose keys, same as the
  word-count path in `repair.count_words`.
- Fail if, inside the first `word_limit` words, any of: the tenant
  `name` / `display_name` / `short_name`; a `$` amount
  (`textutil.DOLLAR_AMOUNT_RE`); a CTA `text`/`url` field that would
  render.
- No-op on product-page (price above the fold is required) and on
  comparison (the eyebrow names both products).
- Discrimination tests: known-good article page passes; a planted
  brand-in-paragraph-one fails; a planted `$8,250` in the open fails.

If the article's N is not 900, use the article's number. Do not guess.

### Candidate: article cartridge wording

Only after you have the article text. Treat new formulas as prompt
wording, reviewed as such. Do not change the 1,000–1,600 word range or
the one-CTA rule unless the article's measurable claim requires it *and*
the suite's canned article page is updated in the same commit.

---

## Done criteria

Standing:

- Full suite green and ruff clean at every commit.
- Every new rule that is measurable has a deterministic gate with a
  discrimination test (known-good passes, planted bad fails).
- Every new block passes the block gate; registry stays in 20–30.
- Baseline parity for behavior-preserving commits.
- No real model runs without a Caleb-issued capped key; the daily spend
  cap (`budget.daily_usd`) bounds any real sweep.
- `harness design-skills` stays a working entry point; `rules.json`
  stays the adapter.

This brief's remaining work (the playbook half):

- Warm-up window gate, scoped, tested, N confirmed from the article.
- Article cartridge wording updates only where the article is specific.
- No design-skill declined rule is "fixed" back in.

Already done (do not redo):

- Design-skills component vendored and wired.
- Filler / leftover-cliche / dead-link hard gates.
- `tagline-reveal` and `risk-reversal` blocks.
- feature-grid full border.
- Landing-style cartridge docs + optional `page.tagline`.

---

## Stop conditions

Per `docs/PROMPTING.md`: stop and ask when a gate, cartridge rule, or
tenant file needs changing beyond this brief's scope; when the suite
can't go green without weakening a check; when parity fails on a
behavior-preserving change; when anything needs a real key or prod
access; when a skill rule the adapter declined looks tempting anyway.

---

## Suggested commit order for the remaining playbook work

1. `find_warmup_window_violations` + tests (no cartridge wording yet).
2. Wire it into `check_page_gates` for `article` only; prove canned
   article still passes; update baseline only if page.json actually
   changes (it shouldn't).
3. Article `cartridge.md` / `schema.json` wording from the article
   text, one commit, reviewed as prompt change.
4. Any new block the article's layout actually repeats, one commit,
   block-gate green.
