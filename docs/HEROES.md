# Hero compositions (cycle 94)

The first screen of a page is a **hero composition**: one module and one Jinja partial in
`harness/heroes/`, shared by every look. A look does not draw its own first screen any more;
it includes `heroes/_pick.html`, which includes the partial of the composition the renderer
resolved. The composition is a page dimension like the look: `hero_style` on page.json, in
state.json, on an A/B/C variant, `--hero-style` on `harness run` and `harness rerender`.

## Catalog

| id | title | cartridges | image it needs | falls back to | reference |
|---|---|---|---|---|---|
| `face` | Face | listicle | the ad's still (required) | story (quotable speaker) or display | cycle 79 |
| `story` | Story | listicle | none | -- | cycle 79 |
| `display` | Display | listicle | none (the page's cut-out) | -- | cycle 79 |
| `ad_first` | Ad first | landing | none (still or cut-out when there) | -- | cycles 82/85 |
| `caps_over_photo` | Caps over photo | listicle, landing | a wide photo that reads under a dark overlay: the ad still when it is a product/room shot of at least 720px (cycle 97), else a library photo (people in use, lifestyle, exterior; wide; dark), else any ad still | dark_statement | peak-lp-pack/01-ridge |
| `dark_statement` | Dark statement | listicle, landing | optional: a dark library detail (macro) photo | -- | 02-hexclad, 07-oura |
| `split` | Split | listicle, landing | a portrait/room photo: the ad still, else a library photo (lifestyle, interior, people in use, exterior; indoor and portrait preferred), else the cut-out | the look's defaults | 03-bobbie |
| `letters_pill` | Letters and pill | listicle, landing (listed by the landing looks) | a library photo (exterior, lifestyle; outdoor preferred), else the cut-out in the pill | dark_statement | 09-rivian |
| `buybox` | Buy box | landing | the product cut-out | ad_first | 06-eight-sleep |

`face`, `story`, `display` and `ad_first` are **adapters**: their partials hold, byte for byte,
the markup the looks printed before cycle 94, and their CSS stays in the look. The shipped
looks' output is pinned by a hash test (`tests/test_heroes_cycle94.py::SHIPPED`). The adapters
carry no `data-look-pattern` attribute because adding one would change that output.

What each look renders (`look.json` beside its template):

| look | heroes | default pool |
|---|---|---|
| listicle `open` | face, story, display, caps_over_photo, split, dark_statement | face, story, display |
| landing `open` | ad_first, caps_over_photo, split, letters_pill, dark_statement, buybox | ad_first |
| landing `proof` | same as open | ad_first |
| landing `cinematic` | caps_over_photo, dark_statement, split, letters_pill | caps_over_photo |
| landing `cinematic-light` (cycle 97) | same as cinematic | caps_over_photo |

A look with no `look.json` (a tenant's imported look, cycle 87) renders its cartridge's legacy
first screens only: face/story/display for the listicle, ad_first for the landing page.

## look.json

```json
{"heroes": ["ad_first", "caps_over_photo", "split"], "default_heroes": ["ad_first"]}
```

`heroes`: every composition the look can include. `default_heroes`: the pool a run with no
request is seeded among (default: `heroes`). Cycle 91's look metadata format was not in this
clone; this file is the format until it lands, and the reader (`heroes.look_spec`) is the one
place to change.

## Selection

`heroes.resolve(cartridge, requested, look=, seed=, tenant=, available=, quote=)`:

1. A request (`--hero-style`, page.json's own `hero_style`, an A/B/C arm) that is not a
   composition of the cartridge is an error (the CLI refuses it; a hand-edited page.json renders
   the seeded pick, logged).
2. A request the look does not render: the seeded pick, with a note.
3. The request, else a pick seeded from the run (`random.Random("<cartridge>-hero-style:<seed>")`,
   the cycle 79 key, so a listicle run picks what it picked before) among the default pool.
   tenant.yaml `cartridges.<cartridge>.hero_styles` narrows the pool (only ids the look renders,
   in the pin's order; a pin that names none of them is ignored).
4. When the chosen composition's required image is not available, its `fallbacks` in order,
   then the rest of the pool; the note says why ("caps_over_photo needs a wide photo that reads
   under a dark overlay; none usable, so dark_statement").

The renderer (`render_page`) resolves once the look and the page's own images are known, logs
`hero composition: <id> (<reason>; image: <why> <asset id>)` and, on a fallback, the cycle 79
line `hero style: <note>`, and stamps `hero_style` on page.json. The pipeline then records the
rendered value in state.json (`listicle.hero_style`, `landing.hero_style`), so a fallback is
recorded as what rendered, not as what was asked. `harness rerender --hero-style` does the same.

## Images: `heroes.pick_image(needs, ...)`

One function; since the merge with cycle 93 it asks the slot resolver, `images.fill_slots`
(harness/images.py, docs/IMAGES.md section 11). A composition's image spec uses the resolver's
vocabulary: `sources` (order), `shots` (cycle 93 SHOTS: hero-wide, room, detail-macro, exterior,
lifestyle-person, cutout), and the slot `want` keys `fullbleed_ok`, `dark_ok`, `mood`,
`orientation`, `aspect_min/max`, plus a `crop`. Each source is one slot, tried in order:

- `ad_still`: the run's ad stills (`asset-adframe` and the cycle 93 extra stills), scored by the
  resolver (a face-checked frame first); positioned by the frame's face point.
- `library`: photo-library photos of the page's model (plus model-agnostic ones) whose shot is
  one of `shots`, filtered by the want keys on a strict pass and again with the frame filters
  relaxed; never a text overlay, never the retired mark above the fold, never a photo already on
  the page; ranked by the page's model, quality, blur, clutter. The file must be on the machine.
  The picture is positioned on its measured focal point; letters_pill cuts the `pill` window
  round it (`render_image_slot(crop="pill")`).
- `cutout`: the product's cut-out (harness/cutouts.py), shown contained.

Adapters (face, story, display, ad_first) print the look's own images; the resolver only answers
whether face has a still.

**Which still a full-bleed hero takes (cycle 97).** For a composition whose image spec has
`fullbleed_ok` and lists `ad_still` before `library` (today: caps_over_photo), the order is:
the ad still only when `images.product_still` is true (the ad_frames vision call saw no face in
it, so a product or room shot, and its short side is at least `images.HERO_STILL_MIN_SHORT` =
720px; the slot want key `product_still`), then a matching library photo, then any ad still.
The first-screen frame is chosen for the creator's face, so it never qualifies; an extra still
the vision call did not see (face unknown) does not qualify either. On the owner's first graded
page (2026-10-08) three of four variants showed a low-resolution talking-head still as the hero.
`split` (no `fullbleed_ok`) and the face/ad_first adapters still use the still first, on purpose.

## Phone-safe photo heroes (cycle 97)

At 390px a photo hero must put the eyebrow, headline, price and pill inside the first 844px.
caps_over_photo's band is `min-height: clamp(560px, 100svh, 900px)` on a phone (desktop keeps
`min(92svh, 880px)`), and its photo is an absolutely positioned `object-fit: cover` background on
its focal point (`--hr-pos`: the face point of an ad still, the measured focal point of a library
photo), so a 9:16 portrait still no longer sets the band's height. Measured at 390 with a
1080x1920 still: the pill starts at 699px (cinematic, open). split 643px, letters_pill 707px,
buybox 643px, dark_statement 386px, ad_first 338px. The design gate's `first_screen_cta` hard
check (docs/DESIGN-GATE.md) fails a page whose pill is lower.

## Add a composition

1. `harness/heroes/<id>.py`: `register(Hero(id=, title=, supports=, context=, needs=,
   fallbacks=, summary=))`. Build the context with `_common.fields(...)` (eyebrow, headline with
   the `hr-accent` span, display line, dek, byline, rating, price, financing, trust, CTA,
   secondary link, specs, product name) and add only what the partial needs. Never a new writer
   field.
2. `harness/heroes/<id>.html`: root `<header class="hr-root hr-<id> hr-bleed"
   data-look-pattern="hero:<id>">`; one `<style>` with every selector under `.hr-<id>` (or the
   shared `.adv-wrap:has(.hr-bleed)` gutter rules); classes `hr-<id>-*`; colours only from
   `--pk-*` / `--ps-*` tokens (dark variants by token, e.g. `--hr-ground:var(--hr-dark)`); no
   gradient or shadow; 390px first, widened at 900px; no fixed width over 390px; the CTA on the
   cartridge's CTA class plus `hr-cta` (52px), the text link 44px. An upper-case display line must
   use `font-family:var(--pk-display-wide)` (the brand lint allows upper case only there).
3. Add the id to the `heroes` list of each look's `look.json` that should render it.
4. Tests: add it to `NEW`/`EXPECTED` in `tests/test_heroes_cycle94.py`.

The Library lists a card per composition a look renders (listicle: x copy style; landing:
`landing/<look>/<hero>`), "Generate with this" pins it, and `harness abtest` takes
`listicle:<style>:<look>:<hero>` and `landing:<look>:<hero>`.
