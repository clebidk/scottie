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

With `design_gate.mode: enforce` (PEAK's setting since cycle 92) a run can also STOP with exit
code 2 at the **design gate**: the page is rendered, but the best of its layout variants scored under
`design_gate.min_score`. See `<run>/<cartridge>/design/scores.json`, REVIEW.md's "Design" section and
[DESIGN-GATE.md](DESIGN-GATE.md) (how the gate judges, the rubric, the knobs, the cost).

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

**One look, three first screens (cycle 79).** The owner approved one boxless design on
2026-10-05 (`~/asset-inbox/design-approved-2026-10-05/`), so the five cycle 51 looks
(editorial, cards, pillars, scorecard, lander) are retired into one, `open`
(`cartridges/listicle/looks/open/template.html`): white page, a centred headline with ONE
accent phrase in brand red, one pill CTA (Basalt, hover red), a one-line byline, numbered
items (red numeral, heading, body, a proof line with a 3px red left bar, a 4px-radius photo,
alternating sides on desktop), "Made for you if / Not for you if" as plain check lists, a
value stack ("What comes with the PEAK <Model>" -- verified feature lines, the price, the
financing line, the CTA), the FAQ as plain Q and A on hairlines, and ONE dark closing band. A
page.json, flag or A/B/C entry that names a retired look renders in `open`
(`listicle.LOOK_ALIASES`).

The first screen is its own dimension (`harness/first_screen.py`), stamped on page.json and
state.json as `hero_style`:

| hero style | what the first phone screen shows |
| --- | --- |
| `face` | headline, one-line byline, a 4:5 still of the ad's creator (`harness/ad_frames.py`) with her verbatim quote as the caption ("In the ad she made for PEAK"), an open-loop line, a scroll cue |
| `story` | eyebrow, headline, byline, then the lede: "In the ad, she says, "..."" next to a round crop of the still, the open-loop paragraph -- and item 01 starts on screen 1 |
| `display` | headline in Peak Grotesk Wide upper case (or Acid Grotesk, `first_screen.display_font`), dek, the product cut-out, a stats strip (big value, red bar, small label; verified claims only), a scroll cue |

```
.venv/bin/harness run <input> --cartridges listicle --hero-style story
.venv/bin/harness rerender tenants/peak-saunas/out/<run-id> --page listicle --hero-style display
```

Without `--hero-style` the run seeds one from the run seed (tenant pin:
`cartridges.listicle.hero_styles`). `face` needs a usable ad still; with none it falls back
to `story` when the ad has a quotable speaker, else `display`, and the log says so. The writer
fills one set of first-screen fields whatever the style -- `eyebrow`, `headline` (6-13
words, from the ad's hook, never the answer), `accent_phrase` (a substring of the
headline), `dek`, `lede` (opens the loop, no number), `scroll_cue`, `hero_quote_id` (an
`ad_quotes` id) and `speaker_pronoun` -- so switching the style is a re-render, no model call.

**Hero compositions (cycle 94).** The first screen is now a shared, pluggable part:
`harness/heroes/` holds one module + one partial per composition, and every look includes
`heroes/_pick.html`. Besides face, story and display (listicle) and ad_first (the landing
page's own first screen), the listicle and landing looks render `caps_over_photo`,
`dark_statement` and `split`; the landing looks also `letters_pill` and `buybox`.
`--hero-style`, `harness rerender --hero-style`, the A/B/C arm (`listicle:<style>:<look>:<hero>`,
`landing:<look>:<hero>`) and the Library's "Generate with this" take any of them; a
composition whose image is missing falls back, logged, and page.json/state.json record what
rendered. Catalog, image needs, selection and how to add one: `docs/HEROES.md`.

**Page structure.** Header (the first screen above; the product hero is the model's cut-out,
`harness/cutouts.py`) -> 5-7 numbered items (named-cause skeleton: 3-5), each with an H2, a short
one-idea body, one image and a closing proof line, with a micro-CTA after items 2 and 4 -> a
pull-quote band after item 3 -> "who this is for / who it is not for" -> the model
picker -> a 5-7 question FAQ -> the closing block (3-bullet recap, CTA, warranty
sentence, financing sentence, HSA/FSA line) -> disclosure and Sources -> a sticky bottom
CTA bar. No page-level word band and no item word range (cycle 72): the writer adapts
the tenant's winner exemplar -- its component map, density and voice -- and never pads
toward article length; the gate only fails an empty item body.

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
`listicle:item_words:<i>` (empty body only), `listicle:item_image:<i>`, `listicle:item_proof:<i>`,
`listicle:hero`, `listicle:audience_fit[:<field>]`, `listicle:faq_count`,
`listicle:faq_claims:<i>`, `listicle:recap`, `listicle:urgency:<phrase>`,
`listicle:headline_slots`, `listicle:tested_no_fake_test`,
`listicle:renderer_owned:<key>`.

**Copy-quality gates** (cycle 74, `harness/listicle_quality.py`, same wiring):
`listicle:item_repeats:<i>` (heading, body and proof are three different lines),
`listicle:proof_restates_body:<i>` (at least 80% of the proof's content words already in
the body), `listicle:repeat_number:<n>:<k>` (a number in more than 3 fields across items,
audience fit and FAQ), `listicle:repeat_sentence:<path>`, `brand_spelling:<path>` (fixed
deterministically to the tenant's display name), `listicle:meta_reference:<path>` (no
"the ad"/"the ad speaker" outside the quote frame; no "she"/"he" in the headline or dek),
`listicle:myth_item:<i>` (myths style), `listicle:message_match` (headline + dek share a
word with the ad's hook/angle/promise/objections).

**First-screen gates** (cycle 79, `harness/first_screen.py`, same wiring):
`listicle:first_screen:eyebrow|accent|lede|scroll_cue|hero_quote|pronoun` (the fields above;
the accent phrase must be a substring of the headline; the lede carries no number, spec or
trigger word; `hero_quote_id` must name an `ad_quotes` entry when the ad has one),
`listicle:quote_verbatim:<field>` (quoted words in the headline, dek, lede or eyebrow are a
contiguous run of the transcript), `listicle:most_people:<path>` (an assertion about what
"most people/most buyers ..." do, with no claim_id on that node -- a question passes), and
`listicle:headline_words` (an open-loop template's 6-13 word band). Gate change the owner
accepted: "she"/"he" in the headline or dek passes `listicle:meta_reference` when the ad is
first person and the page carries the speaker's verbatim quote (`hero_quote_id` set). Every
earlier gate is unchanged.

Review fixes (cycle 79, from the smoke runs): the speaker's pronoun comes from evidence only
(`ad_brief.speaker_pronoun`, set before the writer by `first_screen.prepare_speaker` from the
ad-still check when unambiguous, or `first_screen.speaker_pronouns` in tenant.yaml); otherwise
every quote frame is "In the ad, the creator says," (`listicle:speaker_pronoun:<path>`). The
page never narrates the speaker outside her verbatim quotes (`listicle:speaker_narration:<path>`:
no she/he/her/his outside a quote, and no lede sentence that tells a past without "you"); the
she/he allowance above now covers the headline only. `display_headline` (3-8 words, at most 48
characters) is what the display style sets in the wide face; a longer line is set in the heading
face. No source notes in the copy (`listicle:source_parenthetical:<path>`, also stripped at
render). Each ad quote at most once on the page, the hero quote included
(`listicle:quote_repeat:<id>`).

**Winner skeletons** (cycle 73; list in `cartridges/listicle/skeletons/README.md`). The
item map the writer adapts: `harness run ... --cartridges listicle --skeleton <id>`, else a
pick by the ad's angle. Cycle 98 added `named-cause` (styles reasons and mistakes): the
hook names the reader's moment, the lede names ONE cause, item 1 states it as one plain
mechanism sentence (proof: a verified claim or the ad speaker's own quoted words), items 2-4
are signs that point back to it, item 5 is why the usual fixes did not hold, and the
closing block is the product as the fix. A pinned run takes the headline template nc1 "N
Signs \<cause\> Is \<harm\>" (reasons) or nc2 "N Reasons Why \<common_solution\> Isn't
Enough" (mistakes); both refuse fear and medical wording in the items, and the `cause` slot
refuses health and body-chemistry words (docs/HEADLINES.md). For PEAK the cause is one its
verified claims carry: the outlet requirement, the 360-degree heater placement, or a routine
that depends on leaving the house (that one only as a question or the ad's own words).
Cycle 100 (owner decision 2026-10-09): a named-cause page has 3, 4 or 5 items, never more than
5; every other skeleton keeps 5-7. The range is the skeleton's `item_range` (absent: the
cartridge's 5-7), read by the item gate (`listicle:item_count`) through `skeletons.item_range`;
the headline's N must still equal the item count. `item_plans` hold the 3- and 4-item maps: 4 =
cause, two signs, past fixes; 3 = cause, one sign, and a second sign merged with why the usual
fixes did not hold. `--items N` (with `--skeleton`) picks the count; a count outside the
skeleton's range, or one it has no item map for, stops the run before any model call. Without
`--items` the run writes the skeleton's `item_count` (5). The writer's hard constraints name the
count. There is no item or page word cap on the listicle (cycle 72), so none was scaled.

**Repairs** (cycle 74). The writer gets `ad_quotes` (the ad speaker's own sentences,
pre-filtered so each passes the claims gate) and `allowed_numbers` (every verified number
with its claim ids). A listicle repair is a patch: the writer returns `{"edits": [{"path",
"value"}]}` for the flagged fields only (`harness/pagepatch.py`), on the writer model;
a full rewrite only when a failure has no field path. Before any repair the deterministic
pass cites a line from the verified claim that states its numbers, snaps an unfaithful
attributed line to the verbatim ad quote it paraphrased, and fixes the brand spelling.

**Best of two drafts** (cycle 76, `harness/drafts.py`, `harness/jev.py`). With
`tenant.yaml` `jev: {enabled: true, drafts: 2}` (PEAK's setting) every listicle run
writes two drafts at once, and every gate above runs on each. Draft 2 is a different
attempt, not a re-sample: another headline template (a seeded pick from the templates
the style and the evidence allow) and the next winner skeleton for the ad (the
questions, myths and tested styles have one skeleton, so there only the headline
differs); the style, look, facts pack, claims, ad quotes and assets are the same. A
pinned `--headline-template` or `--skeleton` stays pinned in both drafts.
- Real time with `jev.second_draft_below` (PEAK 0.70): draft 1 is written alone; draft 2
  only when draft 1 fails a gate on attempt 1 or Jev scores it below 0.70 (no Jev:
  draft 1 ships, draft 2 is not written). A batch always writes both. When the style
  allows no other headline template or skeleton (questions, myths, tested), draft 2
  gets the "objection-first" angle instead.
- A draft that fails a gate never ships.
- Both pass on attempt 1: TypeSafe's Jev model scores them with the rubric in
  `cartridges/listicle/jev-rubric.yaml` (tenant override: `tenants/<t>/jev-rubric.yaml`);
  composite = mean of hook, specificity, proof, objections, offer, flow, voice,
  message_match, each 0-1. Draft 2 ships only when its composite beats draft 1's by at
  least `jev.min_margin` (0.02); else draft 1 ships. "overall" is recorded, not used.
- One passes: it ships, no Jev call.
- None passes: draft 1 takes the repair loop exactly as a one-draft run; draft 2 is
  dropped. Repairs only happen when no draft passed on attempt 1.
- No `TYPESAFE_API_KEY` in the tenant `.env`, an HTTP error, a timeout, or less than 30 s
  of wall clock left: the first passing draft ships and the reason is logged. Jev
  never fails or STOPs a run.

The run log tags each write with its draft (`write.listicle[draft 2]`) and adds
`drafts:` and `jev:` lines (scores, which draft shipped and why, Jev tokens -- billed by
TypeSafe, not in `estimated_cost_usd`). state.json's `jev` entry holds the same record,
passing drafts are kept as `<run>/drafts/listicle-draft-<n>.json`, and the listicle
site's generation page shows the scores as a table. `HARNESS_JEV_DRAFTS=1` runs one
draft. The daily spend reservation is multiplied by the draft count.

**Cost.** Both drafts share one prompt cache: everything up to the last cache breakpoint
(system prompt, the shared hard constraints, the facts pack, ad brief, ad quotes,
allowed numbers) is byte-identical for every draft and every repair; each draft's own
headline-template and skeleton lines ("Hard constraints for this page") come after it.
Draft 2 starts when draft 1's response starts streaming (its cache entry exists then),
so it reads that entry. The writer's copy of the facts pack leaves out asset URLs, claim
source URLs and product image URLs. With `--batch`, and for every A/B/C build when
`tenant.yaml` `batch_api.non_interactive` is true (PEAK), all drafts' first writes go in
one Message Batch at half price; the batch wait is not counted in the 300 s wall clock,
and a batch still open after `batch_api.timeout_s` (1800 s) is cancelled and the drafts
are written in real time. The review site's regenerate/feedback stays real time.

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
listicle --style <s>` renders any style with no API key; `--skeleton named-cause` renders
the canned named-cause page (cycle 98); add `--items 3` or `--items 4` for the 3- and 4-item
pages (cycle 100).

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

## The landing cartridge (opt-in, cycle 82)

`--cartridges landing` builds a short, ad-first page for a high-price product, designed for
a 390px phone first (PEAK: about 74% of traffic is mobile, 22 s average visit, 13% scroll
depth). The first two thumb-scrolls answer what it is, what it costs and what the ad's
claims mean; then one next step. Order: first screen (eyebrow, headline with one red accent
span, subhead, price + financing sentence, the CTA pill, the tenant's secondary text link,
a small trust line, then the ad's still or the product cut-out) -> checked claims ("The ad
said" + the verified fact, 4-6, each with claim ids) -> one real-room photo + a 4-cell spec
strip -> rating and an optional verbatim review quote -> 3-5 FAQs (FAQPage JSON-LD) -> the
Basalt closing band -> a sticky price + CTA bar once the first screen has scrolled out.
250-450 model-written words.

The model never types a number the page shows. `harness/landing.py` `render_context`
builds the price (the `price-<model>` claim), the fixed financing and warranty sentences,
the trust line (rating, free shipping, warranty term), the spec strip (facts_pack.specs in
`SPEC_PRIORITY` order: capacity, max temperature, infrared, material, electrical,
dimensions) and the secondary link from tenant.yaml `cartridges.landing.secondary_cta`:
`{text, url}` or `{text, sms, body}` (an `sms:<E.164>?&body=` link that opens the phone's
Messages app; `{product_name}` in the body is the product's full name). That link is a
text link (`ld-text`), not a button, and not in `abtest.CTA_CLASSES`. `find_landing_violations`
(wired in `repair.check_page_gates`) checks the checked claims, word limits, FAQ, ONE CTA
(any other link, url, button or phone field in page.json fails), the room photo (asset
kind lifestyle/installation only, else omit), the quote (verbatim in reviews_summary), the
power wording, numbers in claim-free lines and renderer-owned keys; the product-benefit
minimum (3) is `claims.MIN_BENEFIT_CLAIMS`.

**The longform `open` look (cycle 82).** The longform cartridge now has looks, like the
product page: `classic` (the template before cycle 82, byte for byte) and `open` (the cycle
79 boxless grammar, prefix `lo-`, the ad still or the cut-out first, the stats strip from
verified claims, alternating step rows, a hairline spec table, one Basalt closing band).
The harness default is `classic`; peak-saunas pins `cartridges.longform.looks: [open]`.
`harness rerender --look classic` switches back with no model call.

**The landing `proof` look (cycle 85).** The landing cartridge has two looks: `open` (the
cycle 82 page, the default) and `proof`, which applies six ideas from a Shopify product-page
post the owner shared, 2026-10-07 (reported 1.2% -> 4.7% CVR), in the harness's rules: the
verified rating + count is the first row of the first screen and a verbatim review quote sits
under the subhead; trust facts (free shipping, the warranty term, a short verified returns
clause) sit directly under the pill, with the financing sentence verbatim just above it; 4-6
✓ benefit rows follow the first screen, then the room photo; a 3-4 cell icon row (inline line
icons shipped in the look) comes before the closing band; the rest is the open page. The
writer adds two optional fields only for this look -- `benefits` and `icon_row`
(`landing.ICONS` keys) -- each with claim ids, gated only when present, and inside the same
250-450 words. Select it with `harness run --cartridges landing --look proof`, a tenant pin
(`cartridges.landing.looks`), or the A/B/C library entries `landing:open` / `landing:proof`;
the variant record keeps the look. `--look proof` reaches the writer, unlike a rerender.

**Block-built looks (cycle 91).** The landing cartridge also has `cinematic` (pack page 08: a
full-bleed first screen with the stats inside it, a photo essay, a feature grid, a dark statement
band) and `buybox` (pack page 06: a trust bar, the cycle 94 `buybox` first screen, then the
product with labelled hotspots). Their first screens are cycle 94 hero compositions. Both are composed from the block catalog (`harness/blocks/catalog/`): sections the harness
owns from end to end, declared in a `look.json` next to the look's template. A declared block
renders from facts_pack; only its writer fields (`page.json` `blocks.<id>`) reach the prompt; only
its gates run. See docs/BLOCKS.md for the catalog, how a look declares blocks, and how to write a
block. `--look cinematic|buybox`, A/B/C `landing:cinematic` / `landing:buybox`.

**Offer-led pages (cycle 109).** Owner, 2026-10-14: "if the ad is about a sale, the landing page
100% needs to have that as primary focus". `harness ingest` adds an `offer` block to the ad brief
when the ad's own words (the transcript and the copy around the media) state a discount, a code, a
deadline or a sale name: `amounts`, `codes`, `deadline`, `name` and `sentence` (the ad's sentence
that states the offer, never the "last chance" one). The read is deterministic (harness/offer.py),
not a model call. The ground stage matches it to a verified claim of category `promo` whose text
holds every amount and code the ad states (and the same deadline day) and whose own deadline has
not passed; the match is `offer.claim_id`, the claim joins `facts_pack.verified_claims` and
`facts_pack.offer` is set. Then the landing page is offer-led, all from the claim text:
- eyebrow = the sale name (the claim's text before the colon);
- the headline is the writer's, about the offer in words (gate `landing:offer_headline`);
- under the price: the product's own amount and code in one line ("$300 off with code INDOOR300";
  indoor/outdoor picked from the product's slug, title and URL; a product that matches neither or
  both gets the claim's whole offer clause), then the claim's deadline as one sentence ("Prices go
  up on October 16, 2026.");
- under the first CTA: "Offer applied at checkout with the code." (only when the claim says "at
  checkout");
- the sticky bar: "$8,250 · Code INDOOR300";
- the first checked claim: the ad's sentence as "the ad said", the claim text as the fact. The
  renderer adds it (page.json is unchanged); the writer may not write an offer item
  (`landing:offer_in_checked`).

The markup is in the shared pieces: `heroes/_parts.html` (`offer_lines`, `offer_note`, used by
every composition through `price` and `act`) and `heroes/ad_first.html` (the open and proof first
screen, and every imported look with no look.json). Each look's sticky bar and the cinematic price
band print `ld.offer`. With no offer every page renders byte for byte as before (the cycle 94 and
91 hash pins hold). The listicle gets a lighter touch: the offer line and the deadline under the
buy box's price, and "<sale name>: <line> Offer applied at checkout..." in the closing band.

Cycle 109b: the writer is told exactly what the renderer adds (the eyebrow, the offer line, the
deadline sentence, the CTA note, the bar, the first checked claim) and what it must not write: 3-5
checked claims about the ad's other claims (the gate accepts 3-6), no offer item or placeholder,
nothing about discounts, sales, codes or deadlines outside the hero, no FAQ question about them. A
slip is fixed in place before the gate, never sent for repair (`offer.tidy_page`, every attempt,
landing and listicle): an item that cites the promo claim or states the offer and an item with no
ad_said or fact are dropped, a FAQ entry whose question is about the offer is dropped, a sentence that
denies the offer is removed (an answer or fact left empty drops its entry). Each fix is logged
"fix: ...". "Sales call", "sales team", "no sales pressure" are not offer words.

No countdown, no "last chance", no invented scarcity. The urgency gate
(`listicle.find_urgency_violations`, every cartridge that uses it) now also refuses a deadline
sentence ("prices go up ...", "sale ends ...", "until October 16"), except the verified claim's own
deadline sentence, word for word, on an offer-led page.

**Unverified offer.** When the ad states an offer and no promo claim matches, the facts pack gets
`offer_unverified`, the writer is told to say nothing about discounts, sales, codes or deadlines
(neither that one exists nor that there is none), page.json records `offer_unverified: true`, the
checked claims may not carry the offer, and no line may deny it ("no discount applied", "full
price", "not on sale": `landing:offer_denial`, `listicle:offer_denial`). The A/B/C pipeline holds
such a test off Meta (docs/ABTEST.md "Held offers"). Add a promo claim with
`harness claims add "<text>" --category promo --source "<where>"` or the Ads page form.

## Tenant looks (cycle 87, "Import a page")

A look can also belong to a TENANT: `tenants/<t>/looks/<cartridge>/<slug>/`
with `template.html`, `library.json`, `observation.json` and `review.html`.
`harness/look_import.py` makes one from any landing page (the site's
**Import a page**, or `harness look import --tenant <t> --cartridge landing
(--file F | --url U | --html -) --name <slug> [--title T] [--dry-run]`;
see docs/LISTICLE-SITE.md for the steps). `--dry-run` prints the build
observation and makes no model call. Landing and listicle looks can be
imported.

Resolution (`harness/looks.py`): a tenant look resolves AFTER the
cartridge's own looks. An explicit look (`--look`, page.json's `look`, an
A/B/C entry) that is not one of the cartridge's own is accepted when it is
one of this tenant's imported looks for that cartridge
(`looks.imported_looks`); a tenant look is never a default and never a
tenant pin. `render_page` loads it through a Jinja prefix loader on
`tenants/<t>/looks/<cartridge>/`, after the cartridge folder, so a built-in
look of the same name always wins (the import also refuses such a name).
The A/B/C library takes `landing:<slug>` / `listicle:<style>:<slug>`;
`harness run --look <slug>` and `harness rerender --look <slug>` take it
too. A tenant look is runtime data (`tenants/*/looks/` is gitignored, like
`abtests/`).

The look contract (enumerated from `landing.render_context` and
`render_page`'s template context; `look_import.CONTRACT_NAMES`, checked by
`tests/test_look_import_cycle87.py`). A landing look may read `page`,
`product`, `assets`, `cartridge_data`, `disclosure_label`, `byline_html`,
`render_image_slot`, `image_fit`, `look` and `tenant_name` -- not
`facts_pack`, `ad_brief` or `tenant`. `cartridge_data` keys: `eyebrow`,
`headline_html`, `headline_fits`, `price`, `financing`, `warranty`,
`trust`, `specs`, `rating`, `review_quote`, `secondary`, `quote`, `made_for`, `frame`,
`room_kinds` (the proof look's extra keys are not given to an imported
look). `page.*` must exist in `cartridges/<c>/schema.json`. A listicle look
also reads `first_screen` and `about_author_html`. The rules the model is
given and the validator enforces are in `harness/look_import_rules.md`.

The reference render uses the tenant's demo page,
`tenants/<t>/look-demo/<cartridge>/{page,facts_pack,ad_brief}.json`, made
once from an offline run (`python -m evals.fake_run
tenants/peak-saunas/fixtures/founder-warranty-demo.txt --tenant peak-saunas
--cartridges landing`; the listicle one with `--cartridges listicle --seed
42`). A tenant with no demo page for a cartridge cannot import a look for
it (the error says how to make one).

### Import fidelity (cycle 90)

`harness/look_design.py` adds the design level to the import: per section a
DOM skeleton and a style summary from the page's own CSS, the distinctive
patterns (`look_design.PATTERNS`), a design brief, the page's sanitised CSS
and the closest hand-made exemplar for the prompt, and the fidelity score of
the rendered candidate (below `FIDELITY_THRESHOLD` = 0.7 a repair round names
the missing patterns; `look_import.MAX_CALLS` = 3). A look marks each
pattern it realises with `data-look-pattern="<name>"`; the score reads those
markers from the demo render. `harness look import ... --exemplar <slug>`
names the exemplar; `--dry-run` prints the observation (with `brief`) and
the exemplar it would use. Details: docs/LISTICLE-SITE.md "Import fidelity".

## Add a new cartridge type in under 10 minutes

Copy an existing cartridge folder -- `product-page` or `longform` are the most complete
references -- to `cartridges/<new-name>/`:

```
cp -r cartridges/product-page cartridges/<new-name>
```

Edit the five pieces:

- **`cartridge.md`** -- voice and structure rules in prose: word range ("N-M words," the
  exact phrase `write.parse_word_range` looks for; optional -- listicle has none, so any
  other length there is phrased "8 to 14 words" and can never parse as one), section order, CTA rules pointing at
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
(`harness/write.py`'s `load_exemplars`). The writer treats them as the adaptation source
for voice and structure, never a claims source (cycle 72).

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

These registry blocks are layout variants of one slot. The cycle 91 **block catalog**
(`harness/blocks/catalog/`, snake_case ids, docs/BLOCKS.md) is a different tier: whole sections
with their own data, writer fields, gates, markup and CSS, which a look declares in `look.json`
and includes as `{% include "blocks/<id>.html" %}`.

## The comparison cartridge (v1.0.0, cycle 56; opt-in)

`harness run --tenant <t> --cartridges comparison <ad>` builds a
claims-safe comparison lander on two axes, both from verified facts only:

- **Model vs model (renderer-owned).** `ground.facts_for(...,
  include_comparison=True)` adds `facts_pack.comparison`: the ad's product
  (featured column) plus the two models `_model_options` already picks
  (closest in price, active only), each with its URL, first storefront image
  and one cell per row. Rows: capacity, footprint, indoor/outdoor, infrared
  wavelengths, red light, controls/app, power, 1-2 writer-picked extras
  (`max_temperature`, `cabin_material`, `heaters`, `audio`), price, warranty.
  Every cell is a fragment of ONE verified claim's own text with that id
  (claims namespaced `spec-/gbrain-/pdp-<model>-<suffix>`), the price cell is
  the first dollar figure of the price claim (never a compare-at), the
  warranty cell is vocab's fixed spec value; anything else is a dash, and an
  all-dash row is dropped. The writer never writes a cell.
- **Vs the alternatives (writer-owned).** 2-3 ids from
  `cartridges/comparison/schema.json` `alternatives` (studio membership,
  traditional sauna, far-infrared-only cabin, portable blanket). Summary,
  similarities and `theirs` carry no digit, `$`, `%` or trigger word; the
  tenant's side (`ours`) is `{text, claim_ids}` and claims-gated.

The writer picks `axis: models|alternatives`, which fixes the headline
formula, in sentence case since cycle 60 (`<A> vs <B> vs <C>: which <category> fits <audience>` or
`<category> vs <alternative>: what <audience> should compare`). Gates live in
`harness/comparison.py` (`find_comparison_violations`, stable
`comparison:*` keys: axis, headline_formula, headline_slots,
alternatives_count/allowlist, alternative_digits, alternative_claims,
best_for, who_for, faq_count/faq_claims, numbers_mean, recap, headline_case,
dek_length, best_for_length, images, renderer_owned; cycle 60 removed
extra_rows -- the table is eight fixed rows); competitor and retired names stay the existing
forbidden-vocab gate's job. `render_page` adds the table's claim ids to
Sources (each model's facts fall back to that model's own page) and runs
`find_table_violations` as a post-render backstop. The approved-competitor
store (`claims/competitors/`, `_comparison_targets`) no longer feeds this
page. Rubric: `cartridges/comparison/rubric.md`.

## The quiz cartridge (opt-in, cycle 57)

`cartridges/quiz/` is a self-assessment lander: 5-7 questions, short
"good to know" lines between them, and a result that names ONE of the
tenant's active models with its verified price, capacity, product image, a
"why this matches you" list built from the reader's own answers, and a CTA
to that model's page. Run it with `harness run --cartridges quiz <ad>`; it is
not in the no-flag random-3 default.

**The recommendation is data, not copy.** The tenant keeps a scoring rubric
in `tenants/<t>/quiz/rubric.yaml` (template: `tenants/_template/quiz/`):
questions (`id`, `prompt`, 2-4 `options` each with a `label` and
`scores: {<model_slug>: <int>}`), `interstitials` (`after: <question id>`,
`topic`), and `tiebreak`. A model slug is the product name lowercased with
hyphens -- the namespace its claim ids use. The writer rephrases the prompts,
writes the interstitial lines and the FAQ, and echoes the option labels
verbatim; it never touches a score.

**Who builds what.** Renderer-owned, from `facts_pack.quiz` (built by
`ground.py` when quiz is selected): one result card per active model with a
price claim, the trust line, the fixed financing sentence, the HSA/FSA line
(only when verified) and the fixed warranty sentence. The card claims join
the Sources list. The page's one inline script (no `src`, no network) shows
one question at a time with a progress bar and Back, adds up the chosen
options' `data-qz-scores`, and reveals the winning card; the same rule in
Python is `harness/quiz.py`'s `pick_model`. Without a script the page reads
as one list, and the result shows the featured (ad-inferred) model's card
with a note.

**Gates.**

| when | key | what |
| --- | --- | --- |
| before any writer call (`pipeline.ground`, STOP exit 2) | `quiz:rubric:*` | rubric loads; 5-7 questions, 2-4 options; every option scores an active model above zero; no retired/unknown slug; every active model wins some combination; no combination of answers leaves every model at zero (all combinations are enumerated) |
| repair loop | `quiz:headline_formula`, `quiz:headline_slots`, `quiz:headline_case`, `quiz:dek_length` | "Which <category> is right for <audience>?" in sentence case, nothing after it (cycle 61); no brand/model in the category; no generic audience; dek at most 20 words |
| repair loop | `quiz:question_count`, `quiz:question_id:<i>`, `quiz:question_prompt:<i>`, `quiz:options:<qid>` | one question per rubric question, same ids, prompts end in "?", labels verbatim in order |
| repair loop | `quiz:interstitial_count`, `quiz:interstitial_after:<i>`, `quiz:interstitial_claims:<i>`, `quiz:interstitial_length:<i>` | one line per rubric slot; a digit or trigger word needs claim ids; at most 25 words |
| repair loop | `quiz:faq_count`, `quiz:faq_claims:<i>`, `quiz:urgency:*`, `quiz:discount`, `quiz:renderer_owned:*`, `quiz:hero`, `quiz:cta_url` | 3-5 FAQs with cited facts; no urgency/discount; no writer-built card/price/trust line; a hero; cta_url is the featured model's url |
| post-render backstop | `quiz:script`, `quiz:script_external`, `quiz:script_network`, `quiz:result_cards`, `quiz:rendered_questions` | one inline quiz script, no `src`, no network token; cards exactly the active models; rendered question count |

`quiz:options:*`, `quiz:question_id:*`, `quiz:interstitial_after:*` and
`quiz:cta_url` have exactly one right answer (the rubric's, the product's)
and are fixed deterministically, never with a repair call. Simplicity: the
fold holds the renderer's "Start the quiz" anchor only (options are
`<button>`s), and the headline band is 7-14 words. A page written to the
cycle-57 formula renders its H1 in the current form (`quiz.display_headline`),
so `harness rerender` updates it with no writer call.

**Peak's rubric** is generated, not hand-written:
`tenants/peak-saunas/quiz/build_rubric.py` reads `claims/verified.json` and
`claims/products.json` and writes `rubric.yaml` with a comment above every
option naming the claim ids its scores came from. Re-run it after a claims
change -- it overwrites hand-tuned weights, so re-apply them after.
`tests/test_quiz.py` checks the script's output is valid and has the
committed file's questions and labels; weights may differ.

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
