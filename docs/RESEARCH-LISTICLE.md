# Listicle cartridge research (2026-09-10)

Scope: analysis only, feeding the listicle type into the advertorial harness (`docs/SPEC.md` style
6, "Listicle pre-sell"). Covers the live shipped listicle, the 2025-2026 pre-sell landscape, how the
existing cartridge system already is a type registry, and what changes in the plan as a result.
`docs/research-advertorial-systems.md` does not exist in this clone (`README.md`'s READ FIRST list
names it, but the working tree has no such file) -- not cited below.

## 0A. peaksaunas.com and the live listicle teardown

**Live page**, fetched 2026-09-10 with a real UA: `https://peaksaunas.com/pages/5-reasons-to-love-peak-saunas`
(HTTP 200, 393KB). Anatomy, read straight off the fetched HTML:

- **Section order**: hero (eyebrow trust line + H1 + subhead + CTA + product image) -> 5 numbered
  reason items, alternating left/right image placement -> closing CTA block. 7 `<section>`-level
  blocks total, matching `reference/peak-listicle-lp/README.md`'s "all 7 sections" claim.
- **Item count**: exactly 5 (`01` Plug & Play, `02` Medical-Grade Red Light Therapy, `03` Fits Your
  Space, `04` Smart App Controls, `05` Free Shipping). Each item is one `<h2>` (2-4 words) plus one
  paragraph, no sub-bullets.
- **Paragraph length**: 3-4 sentences per item, roughly 45-70 words (e.g. item 5's is 62 words; item
  2's is 34 words) -- short, plain, no citations.
  Item 3 is factually off-spec: it states the Mini is "only 31in wide," but `docs/knowledge-map.md`
  records the Mini's footprint as unsourced -- the shipped page states a number that isn't in
  `claims/verified.json` today.
- **Image**: one per item plus one hero image, all `object-fit: cover`/`contain` inside a
  fixed-height flex container (50-75vh), no `width`/`height` attributes on any `<img>` tag -- CLS
  risk is contained by the fixed-height wrapper, not eliminated by explicit intrinsic dimensions.
- **CTA count**: 2. Hero CTA text "Shop Peak Saunas", closing CTA text "Get Your Peak Sauna" -- two
  *different* phrases, both `href="/collections/all"`. This directly violates every existing
  cartridge's one-CTA-phrase rule (`cartridges/article/cartridge.md` rule 2: "Exactly 1 CTA";
  `cartridges/longform/cartridge.md` and `product-page/cartridge.md`: "Return top-level `cta_text`
  ... exactly once ... reuses them verbatim"). The listicle DNA is good; its CTA discipline is not.
- **Proof placement**: a single unsourced trust line above the H1, "Trusted by 5,000+ Happy
  Customers" -- no per-item proof, no citations, no sources list. Per `docs/knowledge-map.md` section
  3, no customer-count figure is cleared for reuse (conflicting 4,000+/10,000+/template-default
  figures); "5,000+" matches none of them and would fail the harness's claims gate today.
- **Disclosure/byline**: none. No "Advertisement" label, no author/verifier byline block, no
  disclosure paragraph anywhere in the fetched HTML (confirmed by grepping for
  advertis/disclosure/sponsored/byline -- the only hit was an unrelated ad-pixel config string). This
  is the single biggest gap against `docs/SPEC.md` section 7, which requires an "Advertisement"
  label near the headline and a disclosure paragraph at the bottom on every harness-produced page.
- **`:has()` full-bleed rules**: confirmed surviving on the live page, byte-identical to
  `reference/peak-listicle-lp/shopify-body.html`'s top three rules (`.section:has(.pk-lp) .container`,
  `.page__title{display:none}`, `.page__content{margin:0...}`), and the Aurora
  `.container.container--small` wrapper they neutralize is still present in the live markup at the
  page-title block. The trap `reference/peak-listicle-lp/README.md` documents is real and still live.

**Historical reference**: `https://landing-page-builder-mbmcgarry.replit.app/listicle/` returned
HTTP 200 but only a 1.5KB React/Vite shell (`<div id="root"></div>` plus asset script tags) -- the
app is up, but its content is client-rendered and not recoverable via `curl`. Treated as unavailable
for teardown purposes; the shipped `shopify-body.html`/`index.html` in this repo is the only
inspectable copy of this design.

**Token conflict.** `brand/tokens.json` (built 2026-09-09 from the live *theme*, not from any
advertorial page) says the site's accent is `#16C47F` green on a white background with black text
(`color.brand.primary_accent`, `color.text.primary`) and headings in Poppins/DM Sans. The shipped
listicle's own `:root`-scoped tokens, unchanged since its 2026-08-12 dark launch, are primary
`hsl(221 44% 18%)` (a dark navy) and accent `hsl(156 80% 43%)` (a *different*, darker green), on the
same Poppins/DM Sans pairing (`reference/peak-listicle-lp/README.md`). Both are real, both are
currently live on peaksaunas.com, and they visually conflict: the listicle's navy headline color and
darker accent green do not match the rest of the site's black-on-white, brighter-green theme.

**Recommendation: `brand/tokens.json` wins for a new listicle cartridge.** Three reasons. First,
`tokens.json` was extracted 2026-09-09 directly from the live theme CSS and Judge.me widget vars
(`brand/NOTES.md`), the same source of truth the harness's other three cartridges already build on --
a second, older token set for one type only would make the four generated page styles visibly
inconsistent with each other, worse than one static page differing from the theme. Second, the
shipped listicle predates `tokens.json` by four weeks and was built to replicate an external Replit
reference, not to match Peak's theme -- its palette is an artifact of that copy job, not a brand
decision. Third, `brand/NOTES.md` already resolves the closest real precedent (article/blog content
blocks) in `tokens.json`'s favor: it chose the site's advertorial `pkx-` system's tokens (`#E9EAEC`
borders, 8px radius) over the theme chrome's own defaults where the two disagreed, because that
system is "a closer match for generated landing pages" -- the same logic favors the live green theme
over an older, one-off palette. Keep the listicle's non-color DNA (numbered items, alternating image
sides, the `:has()` full-bleed technique); drop the `hsl(221 44% 18%)`/`hsl(156 80% 43%)` color pair.

## 0B. Listicle/pre-sell landscape, 2025-2026

Every claim below is cited to a fetched source with the date fetched (2026-09-10). **Unsourced
benchmarks are hypotheses, not evidence** -- several marketing blogs assert specific lift numbers with
no named methodology or sample size; treat every percentage below as directional, not a target.

Pigeon Digital's awareness-stage framework: listicles suit buyers who are "problem-aware or
product-aware" and want something "fast and scannable," while advertorials suit "colder, further-out
buyers" who are "problem-unaware or solution-skeptical" -- the advertorial "earns its length by
building the case before it ever asks for the sale." One case there claims conversions "jumped around
40% basically overnight" after switching cold traffic from a product page to an advertorial -- a
single unnamed case study, not a benchmark.

Landra's own examples post claims a named case saw advertorial pre-sell pages "cut CAC by 46%" versus
product-detail pages for cold Meta/TikTok traffic, and cites "pre-sell landing pages outperform
product detail pages by 2-3x for cold traffic" as a general claim -- again attributed to unnamed
case studies, no methodology given. Landra's dedicated advertorial-vs-listicle piece is the most
epistemically honest source found: it states plainly that "there is no public controlled A/B test
that pits the two formats against matched traffic and declares a winner," and frames its own guidance
as "directional evidence, not a randomized trial" built on Nielsen Norman Group scanning research and
Eugene Schwartz's awareness levels rather than quantified data. Its qualitative split: advertorials
win when "products are higher-priced or mechanically complex" and categories have "high skepticism,"
while listicles win when "readers already feel the problem" and "traffic is warm or high-intent."

The FTC's native-advertising guide (no dated version banner, current as fetched) sets the compliance
floor regardless of format: a native ad must be disclosed "clearly and prominently," acceptable labels
are "Ad," "Advertisement," "Paid Advertisement," or "Sponsored Advertising Content" (vague terms like
"Promoted" or "Sponsored by" are explicitly called out as insufficient), and on a click-through page
the disclosure must sit "as close as possible to the headline," before the reader receives the pitch.
The live shipped listicle satisfies none of this -- it carries no label and no disclosure at all,
which is a real compliance gap independent of Peak's own internal brand rules.

Landerlab's 2026 benchmark roundup (published 2026-03-30, updated 2026-04-24) puts general ecommerce
landing pages at "2-5% Average" conversion, with top performers reaching "10-15%" by removing
friction; it does not break out listicle-specific numbers, and no fetched source gives a high-ticket
($5K+) benchmark -- treat a five-figure sauna purchase's target rate as an open hypothesis, closer to
the low end of that 2-5% range than to any low-ticket-ecommerce figure, not a sourced number.

**When article vs. listicle vs. simplified product page wins for Peak**, synthesizing the above against
Peak's own five configured styles (`brand/NOTES.md`'s style descriptions, echoed in `docs/SPEC.md`
section 5): the article cartridge is already positioned correctly for "cold Meta" per its own
`cartridge.md` ("Audience temperature: cold. Reader does not know Peak"), which matches the sourced
consensus that a $5-8K purchase in a "high skepticism" category (infrared saunas invite exactly the
kind of health-claim skepticism the FTC and Peak's own guardrails are both cautious about) needs
narrative before pitch when traffic is cold. The simplified product-page cartridge is correctly scoped
to "retargeting and Shopping traffic that already knows the product" (`product-page/cartridge.md`) --
warm-to-hot, matching the sourced guidance that a stripped product page suits readers who already feel
the problem. A listicle sits between them: sourced guidance says it should not be the cold-traffic
workhorse the article cartridge already is, but a fast, parallel-reasons format for a reader who is
already problem-aware and needs a nudge, not a reframe -- closer to warm retargeting or a second-touch
after an article/video, not first-touch cold Meta. This matches `brand/NOTES.md`'s own framing of the
listicle as a later style ("Later: 6. Listicle pre-sell") to feed a quiz or CTA, not a style-1
replacement.

**Consult vs. buy CTA for high ticket**: none of the fetched sources directly address a consult-first
CTA, but the qualitative pattern (advertorial for skeptical/complex/high-price, listicle for
already-convinced/warm) argues that a listicle's single CTA should be lower-commitment than "Buy" for
a $5-8K purchase reached via a scannable, low-depth format -- "See the models" or "Shop the Peak
[Model]" (both already in `cartridges/longform/schema.json`'s `allowed_cta_texts` pattern) fits better
than a bare "Buy Now," which the shipped listicle's own "Get Your Peak Sauna" already avoids.

## 0C. Multi-type harness bones

The cartridge system is already the type registry the harness needs -- no new engine required. Per
`README.md` and `docs/SPEC.md` section 5, every type is `cartridges/<name>/{cartridge.md, schema.json,
template.html, rubric.md, exemplars/}`, discovered by `adv/cli.py`'s `discover_cartridges()` and
loaded generically by `write.py`'s `load_cartridge_prompt`/`validate_schema`/`load_exemplars` and
`render.py`'s `render_page` -- none of that code branches on a hardcoded cartridge name. Adding
`listicle` means adding a fifth directory in that same shape; it does not touch the pipeline.

A `listicle` schema needs, at minimum: a `hook` (the eyebrow/trust-line equivalent -- must resolve to
a verified claim id or be dropped, unlike the shipped page's unsourced "5,000+" line); `items`, an
array of 5-7 objects each with `title` (2-4 words, matching the shipped DNA), `body` (40-70 words,
matching the observed 45-70 word range), `image_key` (an asset-library id, not a raw URL, per every
existing cartridge's "referenced by asset_id only" rule), and `claim_ids` (at least one per item --
the shipped page's zero-citation items would fail this); a `proof_block` (a citable stat or review
figure, replacing the unsourced trust line); and a `closing_cta` using the same top-level
`cta_text`/`cta_url` pattern every other cartridge already uses, enforced to appear identically in
both the hero and the close -- closing the shipped page's two-different-CTA-phrases gap directly.

**Image key / aspect / CLS practice**: follow the existing asset-library convention (id-only
reference, renderer derives `alt`), but unlike the shipped static page, set explicit `width`/`height`
(or `aspect-ratio`) on every image tag in `template.html` -- the shipped page's CLS mitigation (a
fixed-height flex wrapper) works for a static, one-off artifact but is fragile against a real
generator whose images vary in native aspect ratio per run; explicit intrinsic dimensions are the
more robust fix and cost nothing at render time.

## 0D. Drive audit pointer

Asset-sourcing detail for a listicle's image-per-item requirement is being audited concurrently in
`docs/DRIVE-AUDIT-LISTICLE.md` (a separate in-flight task) -- not duplicated here.

## 0E. Synthesis

**Keep / finish / add / kill.** Keep article, product-page, and longform as-is (all three are
Friday-scoped in `docs/SPEC.md` and already match sourced awareness-stage guidance for their intended
traffic). Add listicle now, built from the shipped page's structural DNA (numbered alternating items,
`:has()` full-bleed technique) but with `brand/tokens.json`'s color tokens, real citations per item,
one CTA phrase, and a disclosure/byline block. Quiz and comparison stay later-phase, per
`brand/NOTES.md`'s own "(Week 2: interactive, stateful)" / "(Week 2: needs sourced competitor spec
table)" notes -- nothing in this research changes that sequencing.

**Non-negotiable Peak layout rules for the listicle type**: one CTA phrase, repeated verbatim (not
the shipped page's two-phrase pattern); an "Advertisement" label and disclosure paragraph (the shipped
page has neither, and the FTC source makes this a compliance floor, not a style choice); every proof
statement traceable to `claims/verified.json` (killing the "5,000+" and "31in wide" style unsourced
numbers); no EMF-led framing or named competitor, per `docs/guardrails.md`, same as every other
cartridge.

**Copy rules**: 5-7 items (the shipped page's 5 is the floor, not a ceiling -- schema should allow up
to 7 per the task brief); item title 2-4 words matching the shipped DNA; one proof element per item
(a claim_id-backed fact, where the shipped page had none); CTA verbs modeled on "See the models" /
"Shop the [Model]" rather than a bare "Buy," per the consult-vs-buy synthesis above; one CTA text used
identically at hero and close.

**Bone priority order**: (1) schema.json + cartridge.md for `listicle`, reusing the existing engine
unchanged; (2) template.html built from `reference/peak-listicle-lp/shopify-body.html`'s CSS/markup
DNA but re-themed to `brand/tokens.json`; (3) claims-gate wiring so the writer must cite `hook` and
every item, closing the shipped page's biggest gap; (4) exemplars, once at least one real generated
listicle passes gate.

**What this research changed in the plan** (concrete overrides of prior assumptions):

1. **The shipped listicle's own color tokens are not the source of truth for the new cartridge** --
   before this teardown, the shipped page (being the only real listicle artifact) was the obvious
   template to copy wholesale, including its `hsl(221 44% 18%)`/`hsl(156 80% 43%)` palette; the
   token conflict found in 0A means `brand/tokens.json`'s live-theme green/white/black wins instead,
   so the template needs a re-theme pass, not a straight lift.
2. **Listicle is not a cold-traffic replacement for article** -- the landscape research in 0B (Pigeon
   Digital and Landra both put listicles on the warm/problem-aware side of the awareness spectrum)
   confirms `brand/NOTES.md`'s "Later" placement was correct, but also means the listicle's CTA and
   proof design should target a warmer reader than the article cartridge does, not a copy of the
   article's cold-traffic voice rules.
3. **The listicle needs a claims-gate integration from day one, not a lighter-weight version** -- the
   shipped page's total absence of citations, plus its two live factual gaps (the "5,000+" customer
   count and the Mini's "31in" footprint, both unsourced per `docs/knowledge-map.md`), was easy to
   assume was acceptable for a fast, scannable format; it is not -- every other cartridge in this repo
   enforces claim_ids per section, and the FTC/guardrails findings in 0A/0B make citation-free proof
   claims a real compliance risk, not just a style inconsistency.
4. **CTA count target is 1, not 2** -- the shipped page's two differently-worded CTAs looked like
   acceptable "nav + close" duplication (the pattern `docs/design-notes-batch50.md` documents as fine
   for other cartridges when the wording is identical); here the wording actually differs, which every
   existing cartridge's rules explicitly forbid, so the listicle schema must enforce single-text reuse
   from the start rather than inheriting the shipped page's pattern as-is.

## Sources

- `https://peaksaunas.com/pages/5-reasons-to-love-peak-saunas` -- fetched 2026-09-10. Live shipped
  listicle: 5 items, 2 differently-worded CTAs, one unsourced trust line, zero disclosure/byline,
  `:has()` full-bleed rules confirmed intact.
- `https://landing-page-builder-mbmcgarry.replit.app/listicle/` -- fetched 2026-09-10. HTTP 200 but a
  1.5KB client-rendered React shell; no content recoverable via `curl`, treated as unavailable.
- `https://www.getlandra.com/blog/advertorial-examples` -- fetched 2026-09-10. Cites unnamed
  case-study lift numbers (CAC -46%, 2-3x cold-traffic outperformance) with no stated methodology.
- `https://www.pigeondigital.com/insight/advertorial-vs-listicle-landing-page-by-awareness` -- fetched
  2026-09-10. Awareness-stage framework: listicle for warm/problem-aware, advertorial for cold/
  problem-unaware.
- `https://www.ftc.gov/business-guidance/resources/native-advertising-guide-businesses` -- fetched
  2026-09-10. Disclosure must be "clearly and prominently" placed, close to the headline; the shipped
  listicle currently has none.
- `https://www.getlandra.com/blog/advertorial-vs-listicle-which-converts-better` -- fetched 2026-09-10.
  Explicitly states no controlled A/B test exists comparing the two formats; qualitative guidance only.
- `https://landerlab.io/blog/landing-page-conversion-rate` -- fetched 2026-09-10 (published
  2026-03-30, updated 2026-04-24). General ecommerce landing pages average 2-5% conversion; no
  listicle-specific breakout given.
