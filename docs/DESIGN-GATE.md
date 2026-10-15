# The design gate (cycle 92)

Owner rule, 2026-10-07: "Visual design needs to be top tier. Better generation is preferred over lower
cost; prioritise quality while optimising both."

The design gate is a pipeline stage after `render_pages` (and before `review_notify`). For every rendered
page it takes screenshots, runs hard checks that need no model, asks a vision judge for a score, and keeps
the best of several layouts of the same copy. It never rewrites copy: only the look, the first screen
(and, when cycle 91 lands, the block set) change.

Code: `harness/design_gate.py` (the gate), `harness/capture.py` (screenshots and the DOM probe, shared
with the Library thumbnails), `harness/fonts.py` (local brand fonts), `harness/design_rubric.md` (the
tenant-neutral rubric) and `tenants/<t>/design-rubric.md` (the brand rules and its reference pages).

## How it judges

1. **Screenshots** with the headless Chromium the Library already drives (`$HARNESS_CHROME`, else
   `chromium` / `google-chrome` / `chromium-browser` on PATH, else the macOS app): phone 390 px wide, full
   page, capped at 6000 px tall; desktop 1280x900 (the first screen); desktop 1280 wide full page. The page
   is framed in an iframe of exactly that size. A staged copy of the page gets the tenant's local brand
   fonts (see "Fonts" below), every image made eager, and a wait for `document.fonts.ready`.
2. **Hard checks** (no model), from a DOM probe of the staged page and the phone shot:

   | Check | Fails when | Warns when |
   |---|---|---|
   | `overflow_390` | the page is wider than the 390 px viewport (offenders listed) | |
   | `first_screen_cta` (cycle 97) | on a landing page or a cycle 94 composition (`.adv-landing`, `.hr-root`), the primary CTA's top edge (the first visible `hr-cta` / `ld-btn` / `op-btn` / `adv-cta` link outside a fixed or sticky bar) is at 844 px or lower, or there is none. An article first screen (listicle face/story/display) passes with a note | |
   | `contrast_hero` | first-screen text over a photo is under WCAG AA (4.5, or 3.0 for large text), measured on the screenshot pixels behind the words | |
   | `empty_images` | an `<img>` did not load or has no src, or a `*placeholder*` element shows | |
   | `text_cut` | a text block is clipped (`overflow: hidden/clip`, line clamp or ellipsis with hidden content) | |
   | `tap_targets` | a button (`<button>` or a `btn`/`cta` class) is under 44 px | another link is under 44 px (inline links in a sentence are exempt) |
   | `page_weight` | index.html + assets over 3 MB | over 1.5 MB |
   | `fonts` | | a font family the text uses is declared as a web font but no face of it loaded |

   A variant with a failing hard check is never chosen while a variant without one exists, and never passes.
   Cycle 97: a variant that fails `first_screen_cta` is not sent to the judge at all (it can never be
   chosen, so the call is saved); its record says "not judged: ...". When every variant fails it, the
   revise round tries other first screens, and a page with none left STOPs (enforce) or warns.

   **Viewport-height units in shots (cycle 97).** The full-page shots frame the page in an iframe as
   tall as the page, so a `100svh` band used to be measured against about 4,700 px: the owner's first
   graded page (caps_over_photo, score 36, owner 3/10) showed three screens of photo before the
   headline in its phone shot, which a real 844 px phone never shows. The staged capture copy now
   turns every `vh`/`svh`/`dvh`/`lvh` length into px of the device's own screen height (844 phone,
   900 desktop; `capture.pin_viewport_height`). The page itself is never changed.
3. **Vision judge**: one call per variant through the harness's Anthropic client (`client.messages.create`,
   so tests use the FakeClient). Images: the phone page in tiles of at most 2400 px (Opus 5.5 reads up to
   2576 px on the long edge without downscaling), then the desktop first screen, as JPEG. The system prompt
   is the rubric; the user turn names the page type, the layout, the measured hard checks and, when a brand
   font did not load in the screenshot browser, a note telling the judge to score typography by scale and
   hierarchy, not by the face. Thinking stays on (Opus 5.5 cannot disable it); `output_config.effort` is
   the tenant's `design_gate.effort`. The answer is JSON:
   `{score_0_100, subscores: {hierarchy, typography, imagery, whitespace_rhythm, brand_fit, mobile_craft,
   distinctiveness}, top_issues: [{what, where, fix_as_layout_choice}], verdict: ship|revise|reject}`.
   A refusal, an API error, a cut-off or unreadable answer makes that variant "not judged" (logged), never a
   crash.

### Cycle 110 hard checks

- `chrome_window` -- for the pre-sell first screen (`peak_first`), probed at 375x812, 390x844 and 360x780: the
  eyebrow, headline, subhead, proof row, price line and CTA pill each end above `h - 212` once the store's
  116px of header chrome sit on top (the visible window on load, measured 2026-10-15), and the photo's top
  edge shows. Any other first screen passes with a note.
- `cta_spacing` -- in-flow CTAs (`ld-btn`, `ld-link`, `hr-cta`, ...) no more than 1.5 phone screens (1266px)
  apart, from the top of the page to the footer. Fails on a pre-sell look (look.json "sell"), warns elsewhere.
- `dead_clicks` -- nothing with a pointer cursor or role=button that is not a link or button. Fail on a
  pre-sell look, warn elsewhere.
- `load_budget` (harness/perf.py) -- HTML <= 60 KB, the hero image <= 60 KB WebP with a reserved box, no
  eager image below the hero, no external script; and on a pre-sell look the page's own LCP <= 2.5 s and CLS
  <= 0.1 in headless Chromium over the DevTools protocol with Slow 4G (1.6 Mbps, 150 ms RTT) and a 4x CPU
  slowdown at 390x844, the page served from 127.0.0.1. The numbers show in REVIEW.md's hard-check line and
  on the generation page; state.json `landing.quality` keeps lcp_ms, cls and first_screen_ok.
- The judge's context for a landing page asks whether the product or the ad's person is recognizable in the
  390px crop and, when the ad names an audience, whether a people photo matches it.

Real-browser tests: a snap-packaged Chromium (/snap/bin/chromium, first on the prod PATH) has its own
private /tmp and cannot read pytest's tmp_path, so tests/conftest.py points `real_browser` tests at a non-snap
browser (google-chrome) through HARNESS_CHROME when one is installed. Production renders live under $HOME and
are unaffected.

## The rubric

`harness/design_rubric.md` (engine, tenant-neutral) + `tenants/<t>/design-rubric.md` (tenant). A tenant
with no file of its own gets a brand section built from tenant.yaml `brand.palette`, `brand.fonts` and
`brand.radius`. Headline criteria:

- The bar is the brand's own approved reference pages, not an average web page; "tidy and correct" is a 60.
- Seven dimensions, 0-100: hierarchy, typography, imagery, whitespace rhythm, brand fit, mobile craft,
  distinctiveness. 90+ ships beside the references; 80-89 strong with small fixes; 70-79 competent but
  generic; under 60 has real problems.
- Top tier = a confident hero composition that owns the first phone screen, deliberate white space,
  full-bleed photography used with intent, a clear typographic scale, one accent, no clutter, no generic
  card grids, 16 px phone gutters.
- PEAK (`tenants/peak-saunas/design-rubric.md`): white ground, basalt dark band and buttons, ONE red accent
  (#702B34); the type system is the four cycle 95 brand families -- Montserrat (headings), Epika (the serif:
  pull quotes, review quotes, founder line), Peak Grotesk Wide and Acid Grotesk (display) -- with DM Sans body;
  boxless by default, 4 px radii, pill CTAs, one CTA phrase; the ten owner-approved pack pages (Ridge,
  HexClad, Bobbie, Caraway, Venus et Fleur, Eight Sleep, Oura, Function Health, Rivian, Plunge), one line
  each on what they get right.
- Anti-patterns that cost points: empty space without purpose, a tiny hero image, stacked grey boxes,
  default-looking stats, orphan headings, cramped mobile gutters, more than one competing CTA, generic card
  grids, more than one accent / gradients / shadows.

## Variants and the revise round

- Round 1: the page as rendered first, then the other layouts the tenant allows, up to `variants`:
  look (tenant.yaml `cartridges.<c>.looks`, else all of the cartridge's looks: for the landing page open,
  proof, the cycle 91 block looks cinematic and buybox, and cycle 97's cinematic-light) x hero composition (cycle 94, harness/heroes/:
  each look's look.json `default_heroes`, narrowed by tenant.yaml `cartridges.<c>.hero_styles`) x block
  set (`design_gate.block_sets()`, `(None,)`: a look's cycle 91 blocks come with the look). An explicit
  `--look` / `--hero-style` (an A/B/C arm that names a look or a hero passes them) is never changed; a look
  that does not render a pinned hero is not tried. A layout that renders as one already tried (a composition
  whose image is missing falls back, e.g. face with no ad still) is skipped without a judge call.
- Best = the highest judge score among variants with no failing hard check. Pass = score >= `min_score`
  and verdict is not "reject".
- Below `min_score`: ONE revise round of up to `variants` untried layouts (the rest of the round-1 space,
  every other hero each look's look.json `heroes` declares, and the tenant's imported looks from cycle 87),
  ranked by the judge's top issues: an issue about the hero
  or first screen ranks a different first screen; one about layout / boxes / grid / structure ranks a
  different look; "more photos" / "tiny hero image" ranks layouts with more photography (face, caps over
  photo, letters + pill and split > display, dark statement, ad first, buy box > story; an imported look
  counts as photo-led), "fewer photos" the reverse. Still below: **enforce** STOPs
  the run (exit 2, after every page is scored); **warn** goes on. With nothing untried left, the revise round
  is skipped with a note.
- The best layout is re-rendered into `out/<run>/<cartridge>/` (exactly `harness rerender --look
  --hero-style`), stamped on page.json and recorded in state.json.

Everything is kept in `out/<run>/<cartridge>/design/`: `variant-<n>.png` (phone, full page),
`variant-<n>-desktop.png` (first screen), `variant-<n>-desktop-full.png`, and `scores.json` (every
variant's layout, hard checks, judge answer, cost; the rounds; the notes; the fonts that loaded).

## Where it shows

- Run log: one line per call (`design_gate.<cartridge>: model=claude-opus-5-5 input_tokens=...`), per page
  `design gate: N variants, M judge calls, $X; PASS|WARN|STOP score ...`, and a total line.
- REVIEW.md "## Design": result, chosen variant, score and subscores, top issues, hard checks, fonts
  (loaded / fallback), notes (budget stops, skipped rounds), a table of every variant, the shots inline.
- Review site: "Design <score>" on the generation card (next to Jev, which stays the copy score); on the
  generation page a Design card with the variants strip (each variant's phone shot and score; the shots are
  served behind the same login as every preview) and a "Design score" row in Details.
- Library: `harness library build --design-score` scores each version once (one paid call per version,
  outside the offline build); the score shows on the Library card.
- A/B/C: every variant page passes the gate (it is a stage of the run); the test record keeps
  `design_score` / `design_status` on the variant and on each attempt. A pinned arm that stays under the
  bar fails that attempt (exit 2), and the existing retry / substitute-arm rule applies.

## The knobs (tenant.yaml `design_gate:`)

| Key | Default | PEAK | Meaning |
|---|---|---|---|
| `mode` | `off` | `enforce` | `off`: the stage returns at once; every output is byte-identical to a run without the stage (tested). The review html's local fonts are a separate change that applies in every mode. `warn`: score and build variants, never stop. `enforce`: STOP under `min_score`. `HARNESS_DESIGN_GATE` in the environment overrides it for one run (the test suite sets `off`). |
| `model` | `claude-opus-5-5` | same | the judge; must have a price in harness/pricing.py |
| `effort` | `high` | same | the judge's thinking depth (low ... max) |
| `min_score` | 75 | 75 | the bar |
| `variants` | 3 | 3 | layouts per round (1-6) |
| `max_usd_per_page` | 1.00 | 1.00 | judge spend per page; reaching it stops further variants and rounds with a note in the log and REVIEW.md. It never lowers the bar: a page left under `min_score` (or never judged) because of the cap STOPs in enforce mode. |

No browser on the host, or a judge that cannot answer: the page is **UNSCORED** (said in the log, REVIEW.md
and on the site) and the run goes on.

## The cost

Every judge call goes through the run log's per-model pricing (`claude-opus-5-5`: $4 input / $20 output
per million tokens, added to harness/pricing.py) and so into the run's cost estimate and the spend ledger.
`harness run` reserves `max_usd_per_page` x pages on top of its usual reservation against the daily cap.
The time the gate takes is added to the run's wall-clock budget (like a batch wait); its own bounds are
`variants` and `max_usd_per_page`.

Expected per call on Opus 5.5 at effort high: about 2,200 rubric tokens + about 5,000 image tokens (three
390x2400 tiles and the 1280x900 fold) + a few hundred context tokens = about 7,700 input tokens ($0.031),
plus thinking and the JSON answer, roughly 1,500-5,000 output tokens ($0.03-$0.10). About $0.06-$0.13 per
call; best of 3 about $0.18-$0.40 per page; with a revise round (6 calls) about $0.36-$0.80, inside the
$1.00 cap. The output-token range is an estimate: measure it on the first real runs (`harness design
report` prints the average gate cost per page).

## Commands

- `harness design score <run-dir>/<cartridge> --tenant <t>`: score a rendered page (a run's page or a
  Library version folder) with one judge call, no re-render; writes `<page>/design/`.
- `harness design report --tenant <t>`: mean / min / max score by look and by first screen over every
  scored page (runs and Library), the average gate cost per page, the most common judge issues.
- `harness library build --design-score [--force]`: score Library versions after the build.
- `harness fonts fetch --tenant <t>`: download the body fonts (below).

## Fonts (cycle 92 merged with cycle 95: one module, harness/fonts.py)

Previews and screenshots must show the real brand faces on a host that has none installed (prod's
`fc-list` has none of them). The page's own @font-face rules point at `brand/fonts/<file>` (cycle 95's
licensed Epika, Montserrat and Acid Grotesk; a path that only resolves after publish) or at the theme CDN
(Peak Grotesk Wide), and the body face DM Sans comes from Google Fonts (a `<link>`, tenant.yaml
`brand.font_stylesheet`). Local copies:

- `tenants/<t>/brand/fonts/` (cycle 95) and `tenants/<t>/brand/fonts-display/` (Peak Grotesk Wide): every
  @font-face src whose file name is in one of these folders is rewritten to the local file
  (`fonts.localize_font_faces`; cycle 95's rule, now shared).
- `tenants/<t>/brand/fonts-body/`: the Google Fonts body faces, written by `harness fonts fetch --tenant <t>`
  from `brand.font_stylesheet` (else `brand.fonts.body`/`headings`), latin and latin-ext subsets as woff2,
  with `manifest.json` (family, weight, style, unicode range, file, source url) and `LICENCE.md` (SIL Open
  Font License 1.1). Their @font-face rules are injected at the top of `<head>`, before the page's own CSS,
  so the Google `<link>` still wins where the network works.
- The review html (`harness review`, the review site, the Library's review pages) gets data: urls (cycle 95:
  always self-contained). The capture path (design-gate screenshots of index.html, Library thumbnails of
  the review html) stages a copy with file:// urls, waits for `document.fonts.ready`, asks for one face of
  every declared family, and records per family whether a face loaded (scores.json, REVIEW.md, Library
  meta.json `fonts`). Measured on Chrome 154: with the CDN unreachable the local face renders.
- The storefront export (`shopify-body.html`) is unchanged: it keeps the publishable urls.
