# Images: selection, markup, and delivery (Cycle 31)

Full audit this cycle acted on: `docs/IMAGES-AUDIT-2026-09-14.md`. This doc is the reference
for how the pieces work now; that one is the record of what was wrong before.

## 1. Selection (`harness/ground.py`)

`facts_for()` still returns the full flat `assets` list it always has (Shopify product photos
first, then Drive assets, then the listicle pack) -- the writer is free to pick any asset id
from it, unchanged. Two things are new:

- **`image_slots`**: `{"hero_id": <asset id>|None}`, a lean recommendation attached to
  `facts_pack`. Computed by `build_slot_plan()` -> `pick_hero()`: prefers a real
  lifestyle/interior/installation photo, falls back to the product's first Shopify image,
  never picks a logo/video/ugc/favicon-kind asset, and never picks an `ai_render` unless the
  tenant's `allow_ai_renders` is true *and* no real photo exists at all. Kept to just the
  hero id (not a full slot plan) because `facts_pack` has almost no size headroom --
  `tests/test_ground.py::test_facts_pack_stays_small` caps it at ~4k tokens.
- **`enforce_slot_plan()`** (called from `render.render_page`, gated behind
  `download_assets=True` -- see "Dry runs" below): a render-time backstop over whatever the
  writer actually put in `page.json`. It fixes, without ever touching copy:
  1. **Hero policy** -- if this cartridge's hero slot (see `ground.hero_container` below)
     holds a never-eligible kind, a disallowed `ai_render`, or an id already used by an
     earlier cartridge in this run, it's swapped for a `pick_hero()` pick.
  2. **No duplicate asset id within a page** -- a repeated `asset_id` anywhere in `page.json`
     is swapped for a fresh eligible one, longform's own `images` field excepted (see below).
  3. **No disallowed `ai_render` in a non-hero slot** -- same swap.

### Cross-cartridge dedupe (no repeats across the three cartridges of one run)

`facts_for()` is called once per run and shared by article/longform/product-page (listicle
runs alone); dedupe across them has to be tracked as each cartridge renders in turn.
`render.render_page` calls, in order:

1. `ground.all_used_asset_ids(run_dir, exclude_cartridge=this_cartridge)` -- everything a
   *different* cartridge already used this run, read from `<run_dir>/.image-selection.json`.
2. `ground.enforce_slot_plan(page, ..., exclude_ids=that_set)` -- treats those ids the same
   way it treats any other ineligible id: preferred to avoid, not preferred over having no
   hero at all if the pool is too small.
3. `ground.record_used_asset_ids(run_dir, this_cartridge, final_ids)` -- writes this
   cartridge's own final picks back to the same file for the next cartridge to read.

### Which slot counts as "hero"

`ground.hero_container(page, cartridge_name)`:

| Cartridge | Hero slot |
|---|---|
| `longform`, `product-page` | `page.hero.hero_image` (a real page.json field both schemas declare) |
| `article` | `page.images[0]` -- no dedicated hero field exists in `cartridges/article/schema.json`; the first image in reading order is treated as the de facto hero for selection-policy and markup purposes only, never a new page.json field |
| `listicle` | `page.reasons[0].image` -- same reasoning |

### The one field that's exempt from the duplicate check

`cartridges/longform/schema.json`'s `images` is documented there as *"a convenience index of
everything used [elsewhere on the page]"* -- it's meant to repeat `hero.hero_image`'s and
`how_it_works.steps[].image`'s own ids, not name a distinct slot. Both
`pagechecks.find_duplicate_asset_violations` and `ground.enforce_slot_plan` skip it
(`skip_keys={"images"}` for `cartridge_name == "longform"` only -- article's own `images` is
a real, distinct rendering slot and stays in scope).

## 2. Markup (`render.render_image_slot`, in `harness/render.py`)

One function builds every `<img>`/`<picture>` tag on every cartridge template and every
image-bearing block (`hero-editorial`, `hero-split`, `hero-fullbleed`, `numbered-reason`,
`press-logo-strip`) -- registered as a Jinja global in `render_page`'s environment, so a
block partial can call it exactly like a cartridge template does. One call site is the fix
for exactly how the audit's problem 6 happened: a hand-written `<img>` on `product-page`
simply forgot `loading="lazy"`.

```jinja
{{ render_image_slot(asset, hero=False, css_class="", caption=None, sizes=None, aspect_box=True) }}
```

- **`width`/`height`**: from the asset dict (set by `download_asset` after Pillow decodes
  it) whenever known.
- **`loading`/`fetchpriority`/`decoding`**: the hero gets `loading="eager"`,
  `fetchpriority="high"`; every other slot gets `loading="lazy"`. Every slot always gets
  `decoding="async"`.
- **`alt`**: unchanged from before this cycle -- always renderer-derived
  (`render.asset_alt()`, product short name + kind), never the writer's own field.
- **`class`**: `adv-img` plus `adv-img--4x3` or `adv-img--1x1` (the aspect box, skipped
  entirely when `aspect_box=False`), plus `adv-img--hero` on the hero, plus whatever
  `css_class` the caller passed (e.g. `adv-hero-image` for back-compat, `pk-zoom` for
  listicle's scroll-reveal motion).
- **`<figcaption>`**: only when `caption` is given (a writer-supplied `img.caption` field,
  currently only `article`'s `page.images[].caption`) -- wraps the whole thing in
  `<figure>`.
- **`srcset`/`sizes`**: present whenever the asset has `variants` (every asset downloaded via
  `download_asset` does). `sizes` defaults to a wider hero value or a general content value;
  either can be overridden per call.
- **`<picture>`**: only when the asset's variants include a WebP file (i.e. this Pillow build
  supports encoding WebP -- checked once, cached, by `render._webp_supported()`). One
  `<source type="image/webp" srcset="...">` ahead of the JPEG `<img>` fallback.

### Aspect detection

`render.detect_near_white_border()` samples an 8px strip on each of an image's four edges;
if the average channel value is >= 245, it's treated as a studio product-cutout-on-white shot
(`aspect="1x1"`). Everything else defaults to `aspect="4x3"`. Approximate by design -- no
real background segmentation -- and it only ever picks the CSS box, never which asset gets
used.

### Srcset/WebP generation

`render.generate_image_variants()` (called from `download_asset`, after the existing
1600px-long-edge/quality-82 downscale) writes one JPEG **and** one WebP (when supported) per
width in `IMAGE_SRCSET_WIDTHS = (480, 800, 1200, 1600)` that does not exceed the source's own
width (never upscaled), named `<asset id>-<width>.<jpg|webp>`. An asset narrower than every
configured width still gets exactly one variant, at its native size. Every variant is
re-encoded (a PNG source becomes JPEG/WebP too, at every width) -- this is also the fix for
the audit's oversized-PNG-hero problem, since a PNG can no longer pass through un-converted.
An asset Pillow can't decode at all is dropped (same treatment as a download that looks like
HTML) rather than shipped unsized.

### CSS (`harness/structure.css`)

`.adv-img--4x3`/`.adv-img--1x1` set `aspect-ratio` + `object-fit: cover` so the box an image
renders into is fixed before it decodes. `.adv-img--hero` is full-bleed on mobile (negative
margins matching `.adv-wrap`'s own side padding, `<=480px` media query) and contained on
desktop by default (no override).

## 3. Delivery

- **Quality**: JPEG quality 82 (`render.ASSET_JPEG_QUALITY`, unchanged from before this
  cycle), WebP at the same quality setting when Pillow supports encoding it.
- **`harness review`** (`harness/review.py`): `inline_assets_as_data_uris` only ever matches
  a plain `src="assets/..."` attribute -- never `srcset`. Since `render_image_slot`'s fallback
  `src` is always the `IMAGE_SRCSET_FALLBACK_WIDTH` (1200px) variant (or the largest available
  below that), this already inlines exactly the "largest or the 1200 variant" the brief asks
  for, with no code change needed to the regex itself. A `<source>`'s `srcset` (and the
  `<img>`'s own `srcset`) are left as relative paths that don't resolve once the file is
  standalone -- a browser silently falls through to the inlined `<img>`. `REVIEW_HTML_MAX_BYTES`
  (12 MB) is checked after each file is written; going over it logs a warning to stderr, never
  raises.
- **`shopify-body.assets.json`** (`harness/page_body.py`'s `build_asset_manifest`): lists every
  distinct file a rendered page's `<img>`/`<picture>` markup references -- the plain fallback
  `src` and every `srcset` width variant, JPEG and WebP alike -- each with its own
  `cdn_filename` (width-suffixed so a 480w and a 1600w variant of the same photo never
  collide). **Publish contract** (for `harness/publishers/shopify.py`, not itself touched by
  this branch): upload each `local_path` under its `cdn_filename`, then rewrite that exact
  string wherever it appears -- a `src` attribute gets swapped outright to the returned CDN
  URL; a `srcset` entry keeps its own width descriptor (`"<cdn-url> 480w"`).

## 4. Checks (`harness/pagechecks.py`)

All new this cycle, all post-render backstops called from `render.render_page`'s
`structural_hits` (same fail-before-write pattern as the pre-existing HTML-validity/JSON-LD
checks):

- **`find_image_markup_violations(html)`**: every rendered `<img>` (excluding the brand logo,
  `class="adv-brand-logo"`) must have `width`/`height`, a non-empty `alt` that isn't a bare
  filename, and must not be favicon-sized (`<200px` on either dimension) in a content slot.
  Only runs when `download_assets=True` -- a dry run never downloads anything, so width/height
  genuinely don't exist yet; this is not a relaxed check, it's the honest state of the page.
- **`find_duplicate_asset_violations(page, cartridge_name)`**: no `asset_id` twice on one
  page (longform's `images` field excepted, see above). Runs on every render, dry or real --
  it's a `page.json`-level concern.
- **`find_hero_requirement_violations(page, cartridge_name)`**: `longform`/`product-page` must
  resolve a hero asset id. No-op for `article`/`listicle` (no dedicated schema field).

## 5. Tenant control

- **`allow_ai_renders`** (`claims/config.json`, read via `tenant.claims_config`): gates every
  `ai_render`-kind asset, both in `ground.pick_hero`/`pick_section_images`/`enforce_slot_plan`
  and in the pre-existing `select_listicle_pack_assets`. Default `False`.
- **`listicle_pack_models`** (`tenant.yaml`): which product models the listicle asset pack
  covers at all -- unchanged from before this cycle, `docs/IMAGE-MAP.md` covers it in detail.
- Preferred *kinds* per tier (real photo over render, render over installation, etc.) are
  currently hardcoded in `ground.py`'s `HERO_CANDIDATE_KINDS`/`SECTION_KIND_GROUPS`/
  `DRIVE_ASSET_TIERS`/`LISTICLE_PACK_TIERS` -- not yet tenant-configurable; every tier
  constant lives in one place in `ground.py` if a future tenant needs a different order.

## 6. Human review: asset-review.json (cycle 36)

`harness serve`'s `/images` site (harness/serve.py, harness/asset_review.py) lets a
reviewer look at every image a product could ever place on a page -- `ground.
full_asset_pool` (uncapped, unlike `facts_for()`'s own capped `assets`) -- write real
alt text per image, and exclude one entirely. Decisions land in
`tenants/<t>/brand/asset-review.json` (`{"version": 1, "assets": {"<asset id>": {"alt",
"excluded", "note", "by", "at", "url" (Shopify only)}}}`), written by the reviewer only
through the site; nothing else in the harness creates or edits this file.

`ground.LocalFactsSource.facts_for()` applies it (`asset_review.apply_asset_review`)
right after building `shopify_assets + drive_assets + listicle_pack_assets`, before
`pick_hero`/`build_slot_plan` or the writer ever see the pool: an excluded asset is
dropped, a non-empty override alt replaces the default. A Shopify override also pins
the url it was written against -- if the product's image list is later re-ordered or
changed, the stored url no longer matches and the override is ignored (logged, never
crashes a run) rather than mislabeling a different photo.

A malformed or hand-edited `asset-review.json` is treated as empty, with a warning
logged -- it never fails a run. Thumbnails are downloaded once (`render.download_asset`)
into `tenants/<t>/runs/asset-cache/`, resized to 480px with Pillow, and cached next to
the original; a dead link renders an inline "could not load" placeholder, never a 500.

## Dry runs stay exactly as before

`render_page(..., download_assets=False)` -- used by `harness/write.py`'s fast local
iteration path and the `evals/fake_run.py` baseline fixtures -- never calls
`enforce_slot_plan`, never touches `.image-selection.json`, and never runs
`find_image_markup_violations`. A dry run's `page.json` is unaffected by anything in this
cycle. (`evals/baseline/{founder-warranty-demo,hidden-costs-v2-transcript}/{longform,
product-page}.page.json` *were* deliberately re-captured this cycle -- not because dry runs
changed, but because those two fixtures are run with `download_assets=True`, the same as a
real `harness run`, and their committed `page.json` had a real duplicate baked in that
`enforce_slot_plan` now correctly fixes. See the commit that re-captured them.)
