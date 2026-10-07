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
| `caps_over_photo` | Caps over photo | listicle, landing | a wide photo that reads under a dark overlay: the ad still, else a library photo (people in use, lifestyle, exterior; wide; dark) | dark_statement | peak-lp-pack/01-ridge |
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

One function, so cycle 93's resolver can replace the matcher without touching a composition.
Sources in the need's order:

- `ad_still`: the run's ad still (`asset-adframe`, harness/ad_frames.py), cropped by its face
  position.
- `library`: the photo-library photos the facts pack offers (brand/photo-library.json tags),
  never one with a text overlay, the retired mark (`old_logo_visible`) or blur, never one
  already on the page; ranked by orientation (`wide` / `portrait`), setting, a dark mean
  luminance when `dark_ok` (measured on the local file), clutter, then `photo_library.hero_rank`.
  A real render skips a photo whose file is not on the machine.
- `cutout`: the product's cut-out (harness/cutouts.py), shown contained.

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
