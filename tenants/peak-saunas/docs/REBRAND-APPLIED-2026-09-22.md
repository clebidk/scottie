# PEAK rebrand — applied (2026-09-22, cycle 52)

The finalized PEAK design system is now what this tenant renders. This is the
record of what changed, what was checked and what is still open. The earlier
`BRAND-IMPORT-REBRAND-2026-09-14.md` was a dry-run survey of the agency
toolbox; where the two disagree, this file is current.

## The name

Display name is **PEAK** everywhere new copy or a label is produced:
`tenant.yaml`'s `name` and `tenant_short_name`, `source_label_prefix`,
`asset_alt_fallback`, `authors.yaml` (contributor is "PEAK Editorial Team";
the two human bios say PEAK), and `brand/byline.html`.

Unchanged on purpose: the domain, every email address, every product URL and
handle, every claim id, and the legal entity line (Peak Wellness USA LLC).
`claims/verified.json` was not touched — it is the evidence store, and its
claim texts are quotations.

Storefront product titles still begin with `Peak Saunas `. That prefix is now
declared in `tenant.yaml` as `product_display_strip_prefix` and removed
wherever a product name is **displayed** (image alt text, the Sources-list
label, product JSON-LD, the listicle model picker) and wherever the **writer**
is handed one (`facts_pack.product`, `model_options`, `comparison_targets`).
Slugs, URLs and claim ids keep it. `digit_exempt_terms` carries both forms,
because a verified claim's own text can still quote the raw catalog name.

## Tokens actually emitted (`brand/base.css` `:root`)

| Token | Value | Role | Contrast verified |
|---|---|---|---|
| `--ps-bg` | `#EFE3D2` Stone | page ground | — |
| `--ps-bg-muted` | `#C0C8C3` Fossil Dust | panels, cards, bands | — |
| `--ps-text` / `--ps-text-strong` | `#181918` Basalt | body and headings on light | **13.93** on Stone, **10.32** on Fossil Dust |
| `--ps-muted` | `#54524C` | secondary copy | **6.17** on Stone, **4.57** on Fossil Dust |
| `--ps-accent` | `#F27046` Solar Flare | the one accent CTA per view | — |
| `--ps-on-accent` | `#181918` Basalt | text on the accent | **6.03** on Solar Flare |
| `--ps-link` | `#181918` Basalt | links, underlined | 13.93 on Stone |
| `--ps-ink-dark` | `#181918` Basalt | dark bands | — |
| `--ps-on-dark` | `#EFE3D2` Stone | text and logo on dark | **13.93** on Basalt |
| `--ps-editorial` | `#702B33` Red | editorial colour, item numerals | **7.97** Stone on Red; Red on Stone 7.97 |
| `--ps-band` | `#483215` Cedar | thin accent bands | **9.52** Stone on Cedar |
| `--ps-border` | `#483215` Cedar | 1px rules and card outlines | 9.52 on Stone |
| `--ps-pill-text` / `--ps-eyebrow-color` | `#483215` Cedar | badges, eyebrows, no fill | **9.52** on Stone, 7.05 on Fossil Dust |
| `--ps-star-on` / `--ps-star-off` | `#181918` / `#C0C8C3` | stars and check marks | 13.93 on Stone |
| `--ps-red` | `#702B33` | error | — |
| `--ps-radius-card/-btn/-pill/-round` | `0px` | square corners everywhere | — |
| `--ps-sans` | Acid Grotesk stack | headlines, labels, body | — |
| `--ps-serif` | Epika stack | editorial moments | — |

`--ps-muted` was computed, not chosen: Basalt mixed 28% toward Stone, the
darkest mix that stays visibly secondary while passing AA 4.5 on **both**
light grounds.

Ratios that the palette forbids and that nothing on a page now uses: white on
Solar Flare (2.92), Basalt on Red (1.75), Solar Flare on Stone (2.35). The
last one is why every accent-coloured eyebrow, item numeral and FAQ marker
moved to Cedar, Red or Basalt.

## Fonts

- **Epika — licensed, self-hosted.** `brand/fonts/Epika-Regular.woff2` and
  `.otf`, from the toolbox `Fonts/Regular/Static` folder, declared with
  `@font-face` in `base.css`.
- **Acid Grotesk — license pending; fallback in use.** The toolbox has a
  trial build only, which must not be used. `--ps-sans` is
  `"Acid Grotesk", "Schibsted Grotesk", "Inter", Helvetica, Arial, sans-serif`,
  so pages render in Schibsted Grotesk or Inter until the licence is bought.
  Recorded in `brand/tokens.json` and `brand/NOTES.md`.

On the storefront: the export keeps only the document body, so `@font-face`
(a head rule) never reached a published page. `harness/page_body.py` now
carries those rules into the export, and `harness/publishers/shopify.py`
uploads each font file once as a generic `FILE`, caches
`{filename: cdn_url}` in `brand/fonts/cdn-manifest.json`, and rewrites the
exported `url(...)` to the CDN. `harness rerender` re-applies the cached URLs
without calling Shopify. A file Shopify refuses loses its own `src` entry and
is logged; a face with nothing uploaded is dropped whole, so the browser
walks on to the fallback stack instead of stopping at a family it cannot
load.

## Logo

`brand/logo.svg` (= `logo-basalt.svg`) and `brand/logo-on-dark.svg`
(= `logo-stone.svg`), plus PNG fallbacks, are the toolbox RGB files —
never redrawn, never set in a font. Drive ids are in `brand/NOTES.md`.
`find_tenant_logo` prefers `logo.svg`, so a page shows the Basalt wordmark on
its Stone ground, which is the rule.

Two things to know rather than discover:
- the toolbox SVGs fill with `#171817` and `#eee2d1`, a step off the palette
  hexes. They are the supplied artwork and are kept byte-exact.
- there was no legacy `logo.png` under `brand/` to rename. The old mark was
  referenced by storefront URL only (`tokens-legacy.json`'s `logo.url`).

## Headline case

`tenant.yaml`'s `brand.headline_case: upper`. `harness/render.py` puts one
class (`adv-case-upper`) on the page wrapper; `harness/structure.css` and
each listicle look carry the `text-transform: uppercase` rule (each look
restates it because the export keeps only the body). **The writer's copy is
never rewritten** — the gate, the claims scans and REVIEW.md all still see
the headline as written.

## What was verified, and how

Copies of the ten current listicle runs were re-rendered under
`/tmp/c52-runs` with `harness rerender` (no model call) and the review htmls
saved to `/tmp/c52-preview/<n>-<look>-review.html`. Each page's emitted CSS
was resolved token-by-token (`--pk-*` → `--ps-*` → `--adv-*`) rather than
eyeballed. All ten, across all five looks:

| Check | Result |
|---|---|
| no `#16C47F` or any legacy green in emitted CSS | pass (10/10) |
| CTA fill / text resolves to `#F27046` on `#181918` | pass (10/10) |
| page ground and look root band resolve to `#EFE3D2` | pass (10/10) |
| headlines uppercase (wrapper class + rule present) | pass (10/10) |
| Epika `@font-face` present | pass (10/10) |
| every `border-radius` resolves to 0 | pass (10/10) |
| Acid Grotesk fallback stack in use | pass (10/10) |

`evals/fake_run.py` for article, product-page and longform: all three render
with the new tokens (body ground Stone, body text Basalt, `.adv-cta` Solar
Flare on Basalt, radius 0, badge/eyebrow Cedar, Epika `@font-face` present,
uppercase wrapper). **No baseline recapture:** all three `page.json` files
are byte-identical to `evals/baseline/hidden-costs-v2-transcript/` — this
cycle changed presentation only.

The old name still appears in **writer copy** on the re-rendered pages, which
is expected until those runs are regenerated. Where it appears: FAQ and body
sentences about the phone app ("the Peak Saunas app"), shipping and returns
sentences quoted from the policy claims, and one audience-fit line. It does
**not** appear in any renderer-owned string — the disclosure, the byline, the
contributor line, the Sources labels and the review-source label all say
PEAK.

## Known, open, deliberate

1. **Acid Grotesk is not licensed.** Every headline is rendering in a
   fallback face. This is the one thing that makes the pages look
   not-quite-brand today.
2. **One accent per view is not yet true.** The design system reserves Solar
   Flare for a single primary CTA per view, but a listicle carries a hero
   CTA, micro-CTAs between items, a sticky bar and a closing CTA, and all of
   them render as the accent button. Verification for this cycle asked for
   "CTA = Solar Flare on Basalt", so that is what shipped. Demoting the
   secondary CTAs to the Basalt-fill primary style is a look-by-look design
   decision, not a token change.
3. **The listicle looks render no logo.** Only product-page and longform show
   `.adv-brand-logo`. The Basalt SVG is copied into every run's `assets/`
   folder regardless. This predates the rebrand and was not changed here.
4. **"PEAK" is a common English word.** The listicle headline-slot gate and
   the article warm-up report match the tenant name on word boundaries, so a
   headline containing "peak hours" or "off-peak" is now flagged as naming
   the brand. It errs toward blocking, never toward publishing, and a repair
   pass rewrites the slot — but expect the occasional false positive. The
   exemplar warm-up filter used a bare substring test, which would have
   matched "peak" inside "speakers"; that one was fixed to word boundaries.
5. **The live theme around the page is still legacy.** Only the generated
   page body is rebranded. The Aurora storefront theme's own chrome — header,
   footer, product cards, the green Judge.me stars, the DM Sans / Poppins
   faces and the white page ground outside `.pk-lp` — is unchanged, so a
   published page sits inside green-and-white furniture. `tokens-legacy.json`
   is the record of what that furniture uses.
6. **Ten live pages are already published with the old look.** They need a
   `harness rerender` + `harness publish --update` pass to pick this up;
   that was not done here.
