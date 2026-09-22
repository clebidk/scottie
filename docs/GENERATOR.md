# Listicle generator

## Generate a listicle in under 10 minutes

On prod (`ssh prod`, `cd ~/advertorial`):

```
.venv/bin/harness run <drive-link-or-file> --cartridges listicle
```

`<drive-link-or-file>` is either a Google Drive link/id or a local path under `tenants/peak-saunas/fixtures/`
(video, still image, or text). This runs the full pipeline -- ingest, ground, claims gate,
write, render -- for the listicle cartridge only. It takes 1-3 minutes of real work (one
to a few real Claude calls) plus however long ingest takes for a video transcript.

A run either **PASSes** (writes `tenants/peak-saunas/out/<run-id>/listicle/index.html` and `page.json`) or
**STOPs** with exit code 2 (an ad claim couldn't be matched to `tenants/peak-saunas/claims/verified.json`;
`unmatched_claims.json` is written instead of a partial page) or exit code 3 (a budget cap
-- wall clock / tokens / Claude calls -- was hit). A STOP or a budget exit is never a
partial page; there is nothing to review in either case except the log.

Next, review the run:

```
.venv/bin/harness review tenants/peak-saunas/out/<run-id>
```

Writes `tenants/peak-saunas/out/<run-id>/listicle-review.html` -- a self-contained file with every image
inlined as a `data:` URI, safe to send as one attachment. As of fix cycle 15 every asset
is downscaled to a 1600px long edge before this step ever runs, so the review file stays
well under 12 MB. Pull it to your Mac with `make review RUN=<run-id>`.

Then, if the page is going to Shopify:

```
.venv/bin/harness shopify-body tenants/peak-saunas/out/<run-id>/listicle
```

Writes `shopify-body.html` (the page body only -- no `<html>`, `<head>`, `<header>`,
`<footer>`, `<nav>`) and `shopify-body.assets.json` (every image referenced, with an
intended Shopify Files CDN filename) next to that run's `index.html`. This only reads and
writes local files; it makes no Shopify API call. See "The publish gate" below before
this goes anywhere near a live page.

## The listicle cartridge (v0.2, cycle 41)

Rebuilt against `tenants/peak-saunas/docs/REFERENCE-LANDERS-2026-09-18.md`: the DTC
listicle shape, with Sun Home's evidence-labelled honesty and HubSpot/ClickUp's
above-fold stack (headline -> hero -> one CTA -> trust line).

**Five styles.** Every run writes in exactly one, and the style fixes the headline
formula and what a numbered item is:

| style | headline formula |
| --- | --- |
| `reasons` | N Reasons \<audience\> Are Choosing \<category\> |
| `mistakes` | N Mistakes \<audience\> Make When Buying \<category\> |
| `questions` | N Questions \<audience\> Should Ask Before Buying \<category\> |
| `myths` | N \<category\> Myths \<audience\> Still Hear, and What the Evidence Says |
| `tested` | We Checked N \<category\> Claims \<audience\> Keep Hearing. Here Is What Held Up |

N is the item count in every style (cycle 49: `tested` no longer leads with a number
of weeks -- its old headline/dek asserted a first-person test that never happened).
Every formula now names an `<audience>` so two ads never produce the identical
headline; `listicle:headline_slots` rejects an empty slot or one that is just a
generic word ("people", "buyers", "shoppers", "customers", "everyone"). `tested`
reports a claims check against verified specs and published facts, never a physical
test -- `listicle:tested_no_fake_test` rejects first-person testing/usage phrases
("we tested", "our test", "hands-on", ...) anywhere on the page, in any style.

```
.venv/bin/harness run <input> --cartridges listicle --style myths
```

Without `--style`, the style is picked deterministically from the run seed
(`harness/listicle.py`'s `resolve_style`), so a batch of runs with different seeds
rotates through all five. `tenant.yaml` may pin a subset with
`cartridges.listicle.styles: [myths, tested]`; an explicit `--style` still wins over
the pin, because that is an operator's deliberate choice.

**Five looks (cycle 51).** The style picks the copy; the LOOK picks the template that
lays it out. Each look is `cartridges/listicle/looks/<look>/template.html` with its own
`<style>` block scoped under `.adv-listicle.look-<name>`; `cartridges/listicle/template.html`
is a dispatcher that `{% extends %}` the resolved one, so `harness/render.py` stays generic.

| look | what it is |
| --- | --- |
| `editorial` | publisher article: one 680px column, serif headline and 19px serif body, sponsored eyebrow, author row under the H1, numbered subheads with inline images, pull-quote proof callouts, link CTAs plus one button mid-page and one at the end, no sticky bar |
| `cards` | the v0.3 DTC look, unchanged: two-column hero, alternating image/text cards on soft bands, big numerals, micro-CTAs, sticky bar |
| `pillars` | image-led: full-width 3:2 bands, uppercase pillar label over the H2, narrow copy, badge proof lines, headline reversed out over the hero image, `<details>` FAQ, sticky proof bar |
| `scorecard` | evidence look: trust row of verified facts, per-item "claim vs what the facts say" panels with evidence label chips, a summary table before the FAQ, no sticky bar |
| `lander` | product lander: wide two-column hero with a dual CTA, items as a 2-up panel grid with 4:3 thumbs, check/cross fit columns, three model cards, `<details>` FAQ, dark closing band, sticky bar on phones only |

```
.venv/bin/harness run <input> --cartridges listicle --look scorecard
.venv/bin/harness rerender tenants/peak-saunas/out/<run-id> --page listicle --look pillars
```

`harness rerender --look` switches a live page's layout with no model call and without
touching a word of copy. Without a flag the look comes from page.json's own `look`, else
`tenant.yaml`'s `cartridges.listicle.look_by_style`, else the default pairing
(`reasons`->`cards`, `mistakes`->`editorial`, `questions`->`scorecard`, `myths`->`pillars`,
`tested`->`lander`). `tenant.yaml` may pin the allowed set with
`cartridges.listicle.looks: [...]`. The resolved look is written to page.json and to
state.json's `listicle` entry next to the style.

Every look renders every section the schema provides, keeps the Advertisement label,
byline, disclosure and Sources, holds a CTA above the fold in markup order, uses only
`--pk-*` tokens, calls `render_image_slot` for every image, and survives `harness
shopify-body` and the review inliner. `tests/test_listicle_looks.py` asserts all of that
plus the property the looks exist for: no two of them render the same set of section
classes.

**Page structure.** Header (Advertisement label, H1, one-line dek, hero image, the
primary CTA, a trust line, byline) -> 5-7 numbered items, each with an H2, a 60-150 word
body, one image and a closing proof line, with a micro-CTA after items 2 and 4 -> a
pull-quote band after item 3 -> "who this is for / who it is not for" -> the model
picker -> a 5-7 question FAQ -> the closing block (3-bullet recap, CTA, warranty
sentence, financing sentence, HSA/FSA line) -> disclosure and Sources -> a sticky bottom
CTA bar. 900-1,400 words.

**What the writer does not write.** The trust line, the pull-quote band, the model
picker, the closing HSA/FSA line and the sticky bar's rating line are built by the
renderer from `facts_pack` alone (`harness/listicle.py`'s `render_context`). Each one is
omitted entirely when this run verified nothing for it -- there is no page.json field to
invent one in, and `listicle:renderer_owned:*` rejects a page that adds one. The model
picker's rows come from `facts_pack.model_options`, which `harness/ground.py` builds
from the tenant's own active products (the run's product first, then the closest in
price, capped at three) with each row's price and fit backed by its own verified claim.

**One CTA, five places.** One `cta_text` and one `cta_url` render in the header, after
items 2 and 4, in the closing block and in the sticky bar. That is one offer repeated,
not five offers: `claims.find_second_cta_violation` still rejects a second `cta_url`
anywhere in page.json, and the CTA allowlist is unchanged. For the simplicity gate the
hero CTA is the one link allowed above the fold; the sticky bar is exempt by
construction, since it renders that same single url and only appears after the hero has
scrolled past (see `harness/simplicity.py`'s module docstring).

**Gates** (`harness/listicle.py`, wired into `repair.check_page_gates`, so the writer
repair loop can fix them). Each failure carries a stable key: `listicle:style`,
`listicle:headline_formula`, `listicle:item_count`, `listicle:item_numbering:<i>`,
`listicle:item_words:<i>`, `listicle:item_image:<i>`, `listicle:item_proof:<i>`,
`listicle:hero`, `listicle:audience_fit[:<field>]`, `listicle:faq_count`,
`listicle:faq_claims:<i>`, `listicle:recap`, `listicle:urgency:<phrase>`,
`listicle:headline_slots`, `listicle:tested_no_fake_test`,
`listicle:renderer_owned:<key>`.

**Design.** Each look ships its own scoped `<style>` block (this cartridge is the one
exempt from `tests/test_css_coverage.py`'s structure.css rule). Shared across all five:
17-18px body at 1.6 (19px serif in `editorial`), 36-44px H1, 24-28px H2, a 48-64px
section rhythm, a full-width mobile CTA at 52px min-height, and, where a look has one,
a 64px sticky bar with safe-area padding. No colour or font is hardcoded: every token resolves through the
tenant's own `--ps-*` brand tokens first and `harness/structure.css`'s `--adv-*`
defaults second, so `harness brand import` still drives the page. The sticky bar lives
inside the cartridge wrapper, never in `base.html`, so `harness shopify-body` carries it
into the storefront body.

**Offline.** `python -m evals.fake_run <fixture> --tenant peak-saunas --cartridges
listicle --style <s>` renders any style with no API key.

**Reading `REVIEW.md`.** Every run writes `tenants/peak-saunas/out/<run-id>/REVIEW.md`: the product picked
(with a `**WARNING:**` line if it was defaulted rather than named in the ad), the gate
history (attempts/repairs/failures per cartridge), word count, cost estimate, and the
claims actually used. Under `ad_overclaim_policy: "warn"`, it also carries a bold **AD
CLAIMS NOT REPEATED ON PAGE -- ad needs fixing** section: every ad claim that couldn't be
matched or that overclaimed a locked topic (warranty/reviews/financing/price), with the
verified fact when one exists. That's the "not repeated" list -- read it before sending a
page out, since it names exactly what the ad promised that the page deliberately left
out. A separate section, **Ad statements about alternatives (not repeated)**, lists
claims about a competing/comparison option; these are never checked against anything and
never stop a run under either policy, shown purely for visibility.

## Add a new cartridge type in under 10 minutes

Copy an existing cartridge folder -- `product-page` or `longform` are the most complete
references -- to `cartridges/<new-name>/`:

```
cp -r cartridges/product-page cartridges/<new-name>
```

Edit the five pieces:

- **`cartridge.md`** -- voice and structure rules in prose: word range ("N-M words," the
  exact phrase `write.parse_word_range` looks for), section order, CTA rules pointing at
  `schema.json`'s `allowed_cta_texts`.
- **`schema.json`** -- the page.json shape the writer must produce, plus a top-level
  `allowed_cta_texts` list (use `{short_name}`/`{model_name}` placeholders, resolved per
  run).
- **`template.html`** -- Jinja template rendering that shape to HTML. Keep the root
  element's class matching the pattern any other full-bleed cartridge uses if this type
  will ever go to Shopify (see `IMAGE-MAP.md` and
  `harness/page_body.py`'s `full_bleed_css()`, which reads the tenant's own
  `theme.full_bleed_css`, keyed to its `theme.root_class`). Any `adv-*` class the
  template uses must have a rule in `harness/structure.css` -- the harness's own
  structural/component/responsive stylesheet, always loaded first by
  `harness/templates/base.html`. A tenant's `brand/base.css`, if any, loads second as
  an *override* layer (tokens, fonts, colors) on top of it, not a replacement -- a
  tenant is never required to (and no shipped tenant does) redefine the `adv-*`
  classes at all. `tests/test_css_coverage.py` enforces this by grepping every
  cartridge template's classes against `structure.css`. A cartridge that ships its own
  fully self-contained `<style>` block (listicle is the precedent) is exempt for the
  classes that block itself defines.
- **`rubric.md`** -- a 10-point manual-review checklist (not executed in V1, but written
  for a human reviewer).
Exemplars are NOT part of a cartridge: they are one tenant's approved pages, and
live in `tenants/<tenant>/exemplars/<cartridge>/` -- up to 2 reference `.md`/`.txt`
files, trimmed to 700 words each before being sent to the writer
(`harness/write.py`'s `load_exemplars`).

No registration step exists: `harness/pipeline.py`'s `discover_cartridges()` finds any
`cartridges/<name>/` directory with a `cartridge.md` automatically -- `--cartridges
<new-name>` works the moment the folder exists. If the new type should join the no-flag
random-3 default, add its name to that tenant's own `tenant.yaml`
`default_cartridge_pool`; otherwise it stays opt-in, the same way listicle shipped.

**What the shared gates enforce automatically**, with zero cartridge-specific code: EMF
and forbidden-term scanning across the whole rendered page (body, alt text, meta, JSON-LD,
filenames, links); leaked claim-id detection in prose; the financing and warranty
fixed-sentence gates; first-person attribution rules; hype-word and incidental-numeral
substitution; the word-range and CTA-allowlist gates once `cartridge.md`/`schema.json`
state them. These all walk `page.json` generically by content, not by cartridge name, so
a new type inherits every one of them the moment it exists -- as cycle 14's listicle build
proved by adding zero lines to `harness/claims.py` or `harness/write.py`.

## Layout blocks (`harness/blocks/`)

kimi/long-run's block registry gives a cartridge named, swappable layout
variants for one slot -- e.g. a proof row rendered as a stat strip or as
cards -- without a copy change. Each block is a folder under
`harness/blocks/<name>/`: `block.html` (a Jinja partial that binds every
piece of content from a placeholder -- no literal copy, ever), an optional
`block.css` (required when the block's `registry.json` entry sets
`styling: "self"`), and `screenshot.svg` (a wireframe capture). `registry.json`
lists every block in the shadcn registry-item shape.

A cartridge opts a slot into blocks by adding it to `schema.json`'s
`block_slots`, e.g.:

```json
"block_slots": {
  "proof": {
    "description": "Layout block for the header proof-stat row.",
    "blocks": ["proof-stat-row", "proof-stat-row-cards"],
    "default": "proof-stat-row"
  }
}
```

The writer records its pick in `page.json`'s top-level `"blocks": {"<slot>":
"<block-id>"}` (omit a slot to render its default), and the template
includes it with `{% include page.blocks.get("<slot>", "<default>") ~
"/block.html" %}`.

**A block can never smuggle in copy or a company's word.**
`harness/blocks/gate.py` rejects, per block: any of the same literal copy
words the page-level gate rejects; any tenant word (the R40 scan --
`tests/test_tenant.py` -- also runs over `harness/blocks/` directly, not
just as part of `harness/`); a `<script>` tag; an inline event handler; an
`<img>` with no explicit size; a color that isn't a `var(--…)` token; and a
fixed width over 390px (a block must never break the mobile-first layout).
`tests/test_blocks.py` runs every registered block through this gate.

## The comparison cartridge (opt-in)

`cartridges/comparison/` is the sourced "<product> vs. <alternative>" page:
a spec table where every cell needs `claim_ids`, 2-4 "deep dive" sections on
dimensions the run's product wins, and a **required**
`alternative_strengths` section -- real, sourced strengths of the
alternative, so the page can never read as a one-sided attack ad. Competitor
entries only ever load from `tenants/<t>/claims/competitors/` when they
carry an `approved_by`; an unapproved entry is invisible to the writer. Like
every cartridge it is opt-in only -- it does not appear in the no-flag
random-3 default, the same way listicle doesn't -- so a tenant chooses it
explicitly with `--cartridges comparison`.

## Eval data (`harness dataset export`, `harness eval report`)

```
.venv/bin/harness dataset export --tenant peak-saunas [--out PATH]
.venv/bin/harness eval report --tenant peak-saunas
```

`dataset export` writes one JSONL record per (run, cartridge) to
`tenants/<t>/evals/dataset.jsonl` by default -- `ad_brief`, a `facts_pack`
summary, the cartridge/block content versions used, `page.json`, gate
history, deterministic check results, and the human score joined from
`evals/scores.jsonl` when one exists. It re-runs the deterministic checks
over the *stored* run artifacts rather than re-generating anything, so an
old run and a new run export the same shape. This is the record a future
fine-tune would be built from -- not before ~200 human-scored pages
(fix cycle correction 7). `eval report` aggregates `evals/scores.jsonl` by
cartridge, block, angle, and reviewer against the rubric's publish bar
(mean would-publish >= 4).

## Add a verified claim

```
.venv/bin/harness claims add "Peak ships free on every order." --category trust \
  --source https://peaksaunas.com/policies/shipping-policy [--approved-by Caleb]
.venv/bin/harness claims list
```

Appends to `tenants/peak-saunas/claims/verified.json` -- the only write path into it, and the only thing the
writer is ever allowed to cite. `tenants/peak-saunas/claims/pending.json` holds ad claims seen in creative
that need Caleb's sign-off before they can move to `verified.json`.

## Config keys (`tenants/peak-saunas/claims/config.json`)

- **`ad_overclaim_policy`** -- `"stop"` (default) or `"warn"`. `"stop"`: any unmatched or
  overclaimed ad claim stops the run. `"warn"`: every such claim is dropped from what the
  writer may use and listed in `REVIEW.md`'s "not repeated" section instead; the run
  continues.
- **`financing_lender`** -- `null` until a real lender is approved; while null, the
  writer's financing line is locked to "Financing is available at checkout." and any
  lender name or figure in an ad claim is an AD OVERCLAIM. Once set (fix cycle 21), the
  financing line is locked instead to "Financing is available through {lender} at
  checkout." (`vocab.allowed_financing_sentence`) -- still a single fixed sentence, never
  a freeform monthly figure or lender name; an ad claim naming the configured lender with
  no $ amount, "/mo", "per month", or APR matches it, but any of those figures is still an
  AD OVERCLAIM (no real lender quote exists anywhere in this codebase to check a specific
  figure against).
- **`allow_ai_renders`** -- `false` by default (fix cycle 15). Controls whether
  `ground.py` may select an `ai_generated: true` asset from
  `tenants/peak-saunas/brand/assets-listicle-pack.json` for Mini/Matterhorn. Real photos are always preferred;
  an AI composite used while this is `true` gets "Rendering:" prefixed to its alt text by
  the renderer.
- **`speaker_name`** -- `null` (anonymous "a customer") unless Caleb has a consented real
  name on file for the ad speaker's first-person story.
- **`show_compare_at_price`** -- whether a product's list/compare-at price is ever offered
  to the writer alongside its current price.

## Shopify traps

(From `tenants/peak-saunas/reference/peak-listicle-lp/README.md`, the live reference this cartridge's markup
was derived from -- the same traps apply to anything `harness shopify-body` produces.)

- The Aurora page template wraps `body_html` in `.container--small` with
  `.page__content{margin:3.2rem 0 0}`. Full-bleed comes from `:has()` rules at the top of
  `shopify-body.html` (`harness/page_body.py`'s `full_bleed_css()`, reading the tenant's own
  `theme.full_bleed_css`) that neutralise the
  container padding, the section spacing, the `.page__content` margin, and the duplicate
  `.page__title`. Removing them re-narrows the page.
- Editing a live page in Shopify admin's rich-text editor can strip the `<style>` block.
  Edit via the API only.
- The storefront serves page updates from several cache epochs. Verify with 8+ pulls, not
  one.

## The publish gate

Cycle 20 added `harness publish` (`harness/publishers/`; see `docs/PUBLISHING.md` for the
full state machine, approval, and packet-stamp flow). It refuses to run unless the run's
`state.json` shows that cartridge as `approved` **and** the run's `packet.json` is stamped
`ship` (`harness packet <run-dir> --stamp ship --by <email>`) -- both together stand in for
"Caleb's explicit, in-writing approval." Default publish is unpublished (a draft page);
only `--live` creates or updates a live page, and `SHOPIFY_STORE`/`SHOPIFY_TOKEN` are not
yet set for `peak-saunas`, so every live publish still fails closed today, with a one-line
message and no network call, until those are configured.

## Design skills

`harness design-skills list` shows the take/adapt/decline rules from the vendored skill; `explain <rule-id>` prints one; `check <run-dir>` runs the deterministic design checks (they also run inside the page gate on every write). Reference site analyses live in harness/design_skills/design-md/ and are evidence only.
