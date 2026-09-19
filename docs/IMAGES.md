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
{{ render_image_slot(asset, hero=False, css_class="", caption=None, sizes=None, aspect_box=True, frame=None) }}
```

- **`width`/`height`**: from the asset dict (set by `download_asset` after Pillow decodes
  it) whenever known.
- **`loading`/`fetchpriority`/`decoding`**: the hero gets `loading="eager"`,
  `fetchpriority="high"`; every other slot gets `loading="lazy"`. Every slot always gets
  `decoding="async"`.
- **`alt`**: unchanged from before this cycle -- always renderer-derived
  (`render.asset_alt()`, product short name + kind), never the writer's own field.
- **`class`**: `adv-img` plus `adv-img--contain` or `adv-img--cover` (the object-fit,
  skipped entirely when `aspect_box=False`), plus `adv-img--hero` on the hero, plus whatever
  `css_class` the caller passed (e.g. `adv-hero-image` for back-compat, `pk-zoom` for
  listicle's scroll-reveal motion).
- **`style`** (cycle 45): `aspect-ratio:<w> / <h>`, the image's OWN measured ratio, reduced
  by its gcd (`render.aspect_ratio_css`). Inline on purpose -- the ratio is per-image, and an
  inline declaration outranks a storefront theme's own `img` rules once
  `harness shopify-body` drops this markup into a Shopify page body. Omitted when
  `aspect_box=False`, and when Pillow could not measure the image (a missing box beats a
  wrong one).
- **`<figcaption>`**: only when `caption` is given (a writer-supplied `img.caption` field,
  currently only `article`'s `page.images[].caption`) -- wraps the whole thing in
  `<figure>`.
- **`srcset`/`sizes`**: present whenever the asset has `variants` (every asset downloaded via
  `download_asset` does). `sizes` defaults to a wider hero value or a general content value;
  either can be overridden per call.
- **`<picture>`**: only when the asset's variants include a WebP file (i.e. this Pillow build
  supports encoding WebP -- checked once, cached, by `render._webp_supported()`). One
  `<source type="image/webp" srcset="...">` ahead of the JPEG `<img>` fallback.

### Aspect box and cut-out detection

The box an image renders into is the image's **own** ratio, measured by Pillow on the
downloaded original and written inline on the tag. Nothing is cropped or stretched, and
`height: auto` keeps it that way.

Cycle 45 fixed this. Before it, the box was one of two hardcoded ratios chosen by
`render.detect_near_white_border()` -- a check on an image's *background* being read as a
claim about its *shape*. A 1067x1600 portrait cut-out was given `aspect-ratio: 1/1` with
`object-fit: cover` and cropped square (the reviewer's "tall product photo crushed into a
landscape box"), and a 1024x1024 square was given 4:3.

`detect_near_white_border()` itself is unchanged and still earns its keep, but it now answers
only the question it was ever able to answer: it samples an 8px strip on each of an image's
four edges and, if the average channel value is >= 245, reports `cutout: True` -- a studio
product shot on white. That flag picks **object-fit**, never the ratio and never which asset
gets used. It is deliberately not stored under `kind`: a facts_pack asset already has a
`kind` (`logo`/`lifestyle`/`installation`/`render`/...) that `asset_alt` and
`ground.enforce_slot_plan` read, and the two must not collide.

A slot that genuinely needs every image in a row to share one box passes `frame="4x3"`
(`render.IMAGE_FRAMES`). That is the only case where the box and the source disagree, and so
the only case where object-fit changes the picture: a lifestyle/installation photo fills the
frame with `cover`, a cut-out is shown whole with `contain` on a neutral band rather than
having the product sliced. Nothing in the tree asks for a frame today.

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

`.adv-img` sets `height: auto`, which together with the inline per-image `aspect-ratio` is
what reserves the box before the image decodes without ever distorting it.
`.adv-img--contain`/`.adv-img--cover` set only `object-fit`. `.adv-img--hero` is full-bleed on
mobile (negative margins matching `.adv-wrap`'s own side padding, `<=480px` media query) and
contained on desktop by default (no override).

`harness/structure.css` is loaded in the document head, and `harness shopify-body` keeps only
the style blocks it finds in the body -- so these rules do **not** survive a Shopify export.
A cartridge that depends on them restates them in its own `<style>`; the listicle cartridge
does. The inline `aspect-ratio` needs no such help, which is why it is inline.

**Trap:** a CSS rule that pins a `width` and a `height` (including `max-height`) on the same
image overrides the inline ratio and squishes the picture -- the cycle 45 bug, rebuilt in
CSS. To cap a tall image's height, pair the cap with `width: auto` and let the browser fit
it.

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

## 7. Paragraph-to-image matching (cycle 42)

`ground.match_images_to_text(page, assets, *, cartridge_name, exclude_ids, allow_ai_renders)`
runs right after `enforce_slot_plan` and before `record_used_asset_ids` in
`render.render_page` (same `download_assets=True` gate) -- deterministic, no model call.
Where `enforce_slot_plan` only fixes selection-*policy* violations (a never-eligible kind,
a repeat, a disallowed `ai_render`), this asks a content question: does the image next to a
paragraph actually show what the paragraph talks about?

**No-op by default.** It reads the active tenant's `asset-review.json`
(`tenant_mod.active()`, the same fallback `listicle_pack_models`/`benefit_allowlist_ids`
already use) and does nothing at all -- `page.json` unaffected -- unless at least one asset
anywhere in the file has a non-empty reviewed `alt`. A tenant that has never used `harness
images describe` or the `/images` review site sees no behavior change.

**Slots covered**, per cartridge (mirrors `ground.hero_container`'s own per-cartridge
knowledge):

| Cartridge | Slot(s) | Context text |
|---|---|---|
| `article` | `images[i]` | the `body_sections[i]` at the same index (images render immediately after `body_sections` in the template, and article has no per-image structural link to a specific paragraph -- pairing by index is the closest non-guessing reading of "the paragraph nearest the image" the schema allows) |
| `longform` | `hero.hero_image` | `hero.headline` + `hero.subhead` |
| `longform` | `how_it_works.steps[i].image` | `steps[i].title` + `steps[i].text` |
| `product-page` | `hero.hero_image` | `hero.product_name` + `hero.promise` (product-page's hero has no `headline` field of its own) |
| `listicle` | `reasons[i].image` | `reasons[i].heading` + `reasons[i].text` |

**Scoring.** Each eligible candidate's searchable text is its *reviewed* `alt` (never the
generic renderer-derived default -- see `render.asset_alt` -- since that text is identical
boilerplate across every asset of a kind and would just double-reward matching the kind)
plus the tags parsed out of its review `note` field (weight 2 each), plus the asset's own
`kind`/`title` (weight 1 each) -- lower-cased, stopwords dropped, a trailing plural "s"
stripped. +1 more when the asset's own `model` field is itself named in the context text
(defensive: `facts_for()`'s capped `assets` list doesn't carry a `model` field today --
`select_drive_assets`/`select_listicle_pack_assets` never added one, and adding one would
blow `test_facts_pack_stays_small`'s ~4k-token budget, which has almost no headroom left --
so this bonus is inert until/unless that changes, but is wired correctly for when it does).

A candidate must score at least `MATCH_REPLACE_THRESHOLD` (2) **and** strictly beat the
writer's current pick's own score to replace it -- a tie keeps the writer's original
choice. Never introduces a duplicate `asset_id` within the page or against `exclude_ids`
(ids another cartridge in this run already used), and never selects a `HERO_NEVER_KINDS`
kind or a disallowed `ai_render`.

**Where decisions land.** `render.render_page` writes the returned `{"matches": [...]}`
into the same `<run_dir>/.image-selection.json` `record_used_asset_ids` uses, under a
`"matches"` key keyed by cartridge name (`ground.record_image_matches`) -- each entry is
`{"path", "old_id", "new_id", "score", "matched_tokens"}`, so a reviewer can see why a slot
changed. One `log.event("ground", ...)` line is written per replacement.

## 8. `harness images describe` / `harness images pool` (cycle 42)

`harness images describe --tenant <t> --model <slug> [--limit N] [--force] [--dry-run]`
(`harness/asset_describe.py`) vision-drafts alt text + tags for a model's asset pool and
writes them into `tenants/<t>/brand/asset-review.json` -- the same file a human reviewer's
`/images` site save writes, in the same shape, so `ground.apply_asset_review` and
`ground.match_images_to_text` (section 7 above) treat a vision-drafted entry exactly like a
reviewer-typed one.

- **Pool**: `ground.full_asset_pool` for the product whose `product_name_slug(name)`
  matches `--model`, minus anything already `excluded` in `asset-review.json`, minus
  anything that already has a non-empty reviewed `alt` -- unless `--force`, which also
  re-describes an already-alt'd asset (an exclusion is never overridden by `--force`).
- **Download**: reuses `render.download_asset` into the same `tenants/<t>/runs/asset-cache/`
  the `/images` review site's thumbnails use, then resizes to at most 1200px on the long
  edge and re-encodes as JPEG in memory with Pillow -- the exact bytes sent to the vision
  call.
- **Model**: `tenant.yaml`'s `models.vision` if set, else `claude-haiku-4-5-20251001`.
  `harness/pricing.py` only prices bare model-family ids (no date suffix); a dated
  `models.vision` override is matched against the known families by prefix for cost-ledger
  bookkeeping only (`asset_describe._pricing_model_id`) -- the actual API call always uses
  the exact configured id.
- **Prompt**: tenant-neutral, built only from the product's own `name`/`title` (this
  schema has no separate "category" field) and the tenant's own `vocab.yaml` `emf_terms` --
  never a hardcoded company or product word. Always says "describe only what is visible; no
  health claims; never mention EMF"; a tenant's own forbidden terms are appended when its
  vocab.yaml sets any. `asset_describe.strip_forbidden_terms` is a backstop that removes any
  forbidden word the model's response used anyway, from both the drafted `name` and `alt`.
- **Response**: strict JSON `{"name", "alt", "tags"}` -- `tags` is filtered to
  `asset_describe.ALLOWED_TAGS` (a fixed list); anything else is dropped, not stored. An
  unparseable or incomplete response skips that one asset (logged) rather than aborting the
  batch.
- **Write**: `asset_review.save_asset_review`, atomically, after every 10 described assets
  and once more for the remainder -- a crash mid-batch keeps most of its progress. Each
  entry: `alt` (the drafted sentence), `excluded: false` unless already excluded, `note:
  "alt drafted by vision (<model>); tags: <a>, <b>, ..."`, `by: "vision-draft"`, `at`
  (`asset_review.now_iso()`).
- **Spend**: reserved against the tenant's daily cap (`budget.reserve_spend`) *before* a
  client is even constructed -- a tenant already at/over its cap describes nothing and
  exits 3, the same as `harness run`/`harness ingest`. Each vision call is tracked through
  the same per-run `Budget` object and `RunLog`; the actual cost is recorded
  (`budget.record_spend`) once at the end.
- **`--dry-run`** lists how many images would be described and an estimated cost
  (`asset_describe.ESTIMATED_COST_PER_IMAGE_USD`, ≈$0.002/image) and makes no calls.
- **Summary line**: `described N, skipped M, cost $X.XXXX`.

`harness images pool --tenant <t>` prints per-model `total`/`reviewed`/`excluded`/
`with-alt` counts across every active product's uncapped pool -- the same totals the
`/images` review site's own index shows, plus a with-alt column.

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
