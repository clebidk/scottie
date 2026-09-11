# Fixture sweep, 2026-09-11 (Cycle 24, final Friday sweep)

Run after Cycle 23's two fixes (run-id collision, `pipeline.make_run_id`/`make_run_dir`;
CSS layering, `harness/structure.css` always loaded before a tenant's `brand/base.css`) were
both committed and installed on the server (`72a8f64`). All seven fixtures run for real
(`.venv/bin/harness run`), foreground, one at a time, real Claude calls, 600s timeout each,
default three cartridges (`article`, `longform`, `product-page`) plus two dedicated
`--cartridges listicle` runs (hidden-costs-v2, product-features-v2). `harness review` and
`harness shopify-body` ran on every PASS. `ad_overclaim_policy` is `"warn"` for this entire
sweep (unchanged tenant default, not touched this cycle).

## Summary table

Reports each fixture's **final** (PASS) run. Cost is that run's own `estimated_cost_usd` log
line (real per-model pricing, including cache).

| # | Fixture | Cartridges | Final run id | Result | Attempts/repairs (per cartridge) | Word counts | Cost |
|---|---|---|---|---|---|---|---|
| 1 | hidden-costs-v2.mov | article, longform, product-page | `20260911-001517-hidden-costs-v2-7tnk` | **PASS** (1st attempt) | article 1/0, longform 1/0, product-page 1/0 | article 1162, longform 1015, product-page 358 | $0.2355 |
| 2 | product-features-v2.mov | article, longform, product-page | `20260911-001800-product-features-v2-3i3w` | **PASS** (1st attempt) | article 2/1, longform 1/0, product-page 1/0 | article 1305, longform 878, product-page 283 | $0.2513 |
| 3 | price-comparison-v2.mov | article, longform, product-page | `20260911-002507-price-comparison-v2-cur2` | **PASS** (2nd real attempt) | article 1/0, product-page 1/0, longform 1/1 | article 1272, longform 940, product-page 265 | $0.2658 |
| 4 | still-lessthan300-4x5.png | article, longform, product-page | `20260911-002807-still-lessthan300-4x5-wngk` | **PASS** (1st attempt) | article 1/1, longform 1/1, product-page 1/0 | article 1154, longform 808, product-page 262 | $0.2381 |
| 5 | still-levelup-4x5.png | article, longform, product-page | `20260911-003029-still-levelup-4x5-fg3w` | **PASS** (1st attempt) | article 2/0, product-page 2/1, longform 1/0 | article 1322, product-page 300, longform 935 | $0.3211 |
| 6 | still-infraredglow-4x5.png | article, longform, product-page | `20260911-003614-still-infraredglow-4x5-nvz3` | **PASS** (2nd real attempt) | product-page 1/0, longform 1/1, article 1/1 | product-page 310, longform 911, article 1246 | $0.2249 |
| 7 | still-unforgettable-4x5.png | article, longform, product-page | `20260911-003834-still-unforgettable-4x5-ffl3` | **PASS** (1st attempt) | product-page 1/1, article 1/0, longform 1/1 | product-page 313, article 1260, longform 895 | $0.1932 |
| 8 | hidden-costs-v2.mov | listicle only | `20260911-004038-hidden-costs-v2-widd` | **PASS** (1st attempt, 3 writer sub-attempts) | listicle 3/3 (attempt1 1 fail/1 fix, attempt2 1 fail/1 fix, attempt3 0 fail/1 fix) | listicle 698 | $0.1220 |
| 9 | product-features-v2.mov | listicle only | `20260911-004216-product-features-v2-imuh` | **PASS** (1st attempt, 3 writer sub-attempts) | listicle 3/4 (attempt1 1 fail/1 fix, attempt2 1 fail/2 fix, attempt3 0 fail/1 fix) | listicle 680 | $0.1093 |

**Seven of seven fixtures PASS** (all default-cartridge runs), plus both listicle runs PASS,
matching Cycle 19's clean-sweep result.

**Total real spend, this sweep: $1.9612** across the nine final PASS attempts above. Two
real attempts STOPped at the claims gate before their fixture's final PASS (fixtures 3 and 6,
below) — **neither printed or logged a cost line**: both stayed at `state.json`'s `"generated"`
state (the claims gate runs before `render_pages`/cost accounting in this pipeline order), wrote
`unmatched_claims.json` and no `REVIEW.md`/`page_json`, so their real-dollar cost (non-zero —
real model calls were made) is not recoverable from the run directory. This differs from Cycle
19's sweep doc, which itemized a cost for its two STOPped retries; that data point is not
reproduced here because this cycle's STOP attempts genuinely have no cost artifact on disk —
flagging the gap rather than estimating a number.

## What STOPped before converging (fixtures 3, 6)

- **`price-comparison-v2.mov`**, 1st real attempt (`20260911-002126-price-comparison-v2-3c6r`):
  STOPped at `page_json:article` — 1 unmatched claim, the trigger word "rated" in buyer-education
  prose about red-light panel specs ("what they are actually rated to do"), no claim_id and no
  safe generic synonym found. Same trigger-word-with-no-claim_id gate pattern documented in prior
  sweeps (`SWEEP-2026-09-11.md`'s fixture 5/6 notes). Re-run converged cleanly
  (`20260911-002507-price-comparison-v2-cur2`) — `longform` needed 1 repair, `article` and
  `product-page` converged on the first try.
- **`still-infraredglow-4x5.png`**, 1st real attempt (`20260911-003401-still-infraredglow-4x5-o4hk`):
  STOPped at `page_json:article` — 2 unmatched items: the trigger word "medical" in buyer-education
  prose about verifying "medical-grade" claims against spec sheets, and a numeral-bearing sentence
  about a "15 amp" standard household circuit (incidental numeral outside the gate's allowed
  pass). 2nd real attempt converged cleanly (`20260911-003614-still-infraredglow-4x5-nvz3`) —
  `longform` and `article` each needed 1 repair, `product-page` converged on the first try.

Both root causes match the pre-existing writer/gate interaction pattern already on record
(a trigger word used in generic buyer-education prose with no safe synonym, or an incidental
numeral outside the claims gate's allowed pass) — not a regression from Cycle 23's run-id or
CSS-layering fixes. Not patched under this cycle's time pressure; same follow-up bucket as
before.

## Byline check (23 pages)

`article`, `longform`, and `listicle` cartridges render the full byline line, verbatim (stripped
of markup, whitespace-normalized):

> Written by Austin Laudenslager · Peak Saunas Editorial Team · Reviewed by Caleb Niednagel,
> Technology Lead

Confirmed present and correct on all 16 `article`/`longform` pages and both `listicle` pages.

**`product-page` never renders a byline at all** — `cartridges/product-page/template.html` has
no byline block (confirmed: zero matches for "byline" in the template source; only CSS rules for
`.adv-byline`/`.byline` exist, unused). This is a cartridge-template fact, not a per-run defect —
all 7 `product-page` pages this sweep are consistent with each other, just missing the byline
entirely. Not a Cycle 23 regression (the byline decision was Cycle 19, unrelated to this cycle's
fixes); flagging since the task asked for a byline check on every page and this cartridge fails
it by design.

## Financing sentence check (23 pages)

21 of 23 pages carry the exact fixed sentence, verbatim: **"Financing is available through Bread
Pay at checkout."** (`claims/config.json`'s `financing_lender: "Bread Pay"`).

**2 pages do not** — both `article` cartridge, both from runs where the writer left
`page.financing_line` unset in `page.json` (confirmed: `None` in both files), so
`cartridges/article/template.html`'s conditional `{% if page.financing_line %}` block never
renders the dedicated `.adv-financing` paragraph. In both cases the writer instead paraphrased
financing into body prose, not verbatim to the fixed sentence:
- `20260911-001800-product-features-v2-3i3w/article`: "...priced at $5,450, **with financing
  available through Bread Pay at checkout**, so the decision becomes..." (missing "is",
  embedded as a subordinate clause, not the exact sentence).
- `20260911-002507-price-comparison-v2-cur2/article`: "...priced at $5,450, **and financing is
  available through Bread Pay at checkout**. See the models..." (closer, but still not the exact
  standalone sentence — no capital "F", joined with "and").

Not an EMF/lender/claims-gate violation (Bread Pay is the configured lender either way, and
nothing here is unverified) but a real gap against the "financing line is exactly the fixed
sentence" expectation this sweep was asked to check — a writer-determinism gap in the `article`
cartridge specifically, not present in `longform`/`product-page`/`listicle` this cycle.

## Banned-term check (visible text, 23 pages)

**Zero** hits on every page, checked directly with `harness.claims.find_forbidden_visible_text`
against each rendered `index.html`'s own visible text (not a raw grep) — confirmed via a one-off
script run through `.venv/bin/python` on the server, no tenant `.env` read or printed.

## Static mobile checks (23 pages)

Confirms Cycle 23's CSS-layering and run-id fixes are holding under this cycle's real runs:

1. **`structure.css` loads before the tenant's `brand/base.css`.** Confirmed on all 23 pages —
   the first inlined `<style>` block contains the harness's structural classes (`.adv-hero`,
   `.adv-cta`, etc.); the tenant's own stylesheet block always follows it.
2. **Every asset `<img>` has `width`/`height` attributes.** Confirmed on all 23 pages — 80 real
   `<img>` tags total (script/style/comment-only text excluded from the count), zero missing
   both attributes. (An unrelated false-positive from a naive tag regex — a code comment in the
   inlined `<style>` block literally contains the text `<img>` while documenting this same
   Cycle 23 fix — was caught and excluded; the actual rendered `<img>` elements are clean.)
3. **`.adv-sticky-cta` has CSS.** Confirmed on all 7 `longform` pages (the only cartridge that
   renders it) — the class is both used in the body and has a matching rule in the inlined
   `structure.css` block.

No regressions found against Cycle 23's fixes.

## Policy / tree state

`claims/config.json`'s `ad_overclaim_policy` remains `"warn"`, untouched this cycle.
`notifications.slack` remains `false` in `tenant.yaml`, untouched this cycle — no Slack message
was sent or attempted. No Shopify call was made at any point (`shopify-body` only renders a
static HTML export locally; `SHOPIFY_STORE`/`SHOPIFY_TOKEN` remain unset). `git status` on the
server is clean of this sweep's own artifacts after the review/shopify-body writes (all land
under gitignored `tenants/peak-saunas/out/`, never tracked) except this sweep's own new docs
files, committed separately. Server test suite: see `docs/FIXLOG.md`'s Cycle 24 entry for the
exact count.
