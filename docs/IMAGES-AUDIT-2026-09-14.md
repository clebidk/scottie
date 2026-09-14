# Image audit, 2026-09-14 (Cycle 31)

Scope: the 23 review pages under `tenants/peak-saunas/out/FRIDAY-2026-09-11/`, and the 9
underlying run directories named in that folder's `README.md`'s sweep table. Method: a
script (`/tmp/audit_images2.py`, not committed) parsed every rendered `index.html` for
`<img>` tags, cross-referenced `assets/*` file sizes on disk, and diffed asset ids used
across the three cartridges of each ad. Findings below are all sourced from that parse plus
direct reads of `harness/render.py`, `harness/ground.py`, `harness/structure.css`, the four
cartridges' `template.html`, and `harness/pagechecks.py` at the `master` commit this branch
forked from. No pixel content of any image was inspected (out of scope, same as
`DRIVE-AUDIT-LISTICLE.md`).

## Per-page counts

| Ad | Cartridge | Run id | `<img>` count | Hero marked? |
|---|---|---|---|---|
| hidden-costs-v2 | article | 20260911-001517-...-7tnk | 3 | no |
| hidden-costs-v2 | longform | same | 4 | yes |
| hidden-costs-v2 | product-page | same | 3 | yes |
| product-features-v2 | article | 20260911-011051-...-57yx | 3 | no |
| product-features-v2 | longform | same | 4 | yes |
| product-features-v2 | product-page | same | 4 | yes |
| price-comparison-v2 | article | 20260911-010829-...-ebcu | 3 | no |
| price-comparison-v2 | longform | same | 4 | yes |
| price-comparison-v2 | product-page | same | 1 | yes |
| still-lessthan300-4x5 | article | 20260911-002807-...-wngk | 3 | no |
| still-lessthan300-4x5 | longform | same | 4 | yes |
| still-lessthan300-4x5 | product-page | same | 2 | yes |
| still-levelup-4x5 | article | 20260911-003029-...-fg3w | 3 | no |
| still-levelup-4x5 | longform | same | 4 | yes |
| still-levelup-4x5 | product-page | same | 3 | yes |
| still-infraredglow-4x5 | article | 20260911-003614-...-nvz3 | 3 | no |
| still-infraredglow-4x5 | longform | same | 4 | yes |
| still-infraredglow-4x5 | product-page | same | 3 | yes |
| still-unforgettable-4x5 | article | 20260911-003834-...-ffl3 | 3 | no |
| still-unforgettable-4x5 | longform | same | 4 | yes |
| still-unforgettable-4x5 | product-page | same | 3 | yes |
| hidden-costs-v2 | listicle | 20260911-004038-...-widd | 7 | no |
| product-features-v2 | listicle | 20260911-004216-...-imuh | 5 | no |

23 pages, 79 `<img>` tags total. Every one of the 79 carries `width`, `height`, and a
non-empty `alt` -- the render-time downscale (`render.render_page` /
`download_asset`/`_image_dimensions`) and `asset_alt()` already guarantee that much. (One
false lead during this audit: a CSS comment in every page's `<style>` block literally
contains the text `<img>`, which a naive regex matches as a phantom zero-attribute tag --
excluded once found; it is a comment, not a real element.)

**Source split**: Shopify CDN product photos (`asset-peak-saunas-<slug>-<n>.jpg/png`) and
Drive assets (`asset-drive-<id>` / `asset-listicle-<id>`) are both in active use. No image in
any of the 23 pages is a favicon or an obviously-wrong pick by filename/kind (e.g. nothing
resolved from `kind: logo`, `video`, or `ugc` -- `select_drive_assets`'s
`DRIVE_ASSET_NEVER_KINDS` already excludes those at the pool level). No `ai_render` asset
was used in any of the 23 pages even though `claims/config.json` sets `allow_ai_renders:
true` for this tenant -- the writer simply never reached for one; nothing in the harness
guarantees it won't next time.

**Formats/sizes**: every JPEG is 138 KB-360 KB, consistent with the fix-cycle-15 downscale
(1600px long edge, quality 82). The two PNG heroes (see problem 4 below) are the outliers.

## The ten worst problems, with page paths

1. **The hero is architecturally always a plain product-on-white Shopify photo, never a
   lifestyle or installation shot, even when one is available.** `ground.py`'s `facts_for()`
   builds `assets = shopify_assets + drive_assets + listicle_pack_assets` -- Shopify images
   always first. There is no "hero" concept in `ground.py` at all; the writer just picks
   *some* `asset_id` for `page.hero.hero_image`, and in 6 of 7 non-listicle ads it picked the
   first-listed (Shopify) image every time: `.../20260911-001517-hidden-costs-v2-7tnk/longform/index.html`,
   `.../still-lessthan300-4x5-wngk/longform/index.html`, `.../still-levelup-4x5-fg3w/longform/index.html`,
   `.../still-infraredglow-4x5-nvz3/longform/index.html`, `.../still-unforgettable-4x5-ffl3/longform/index.html`
   (and each ad's `product-page` sibling) all use the identical asset
   `asset-peak-saunas-fuji-...-1.jpg` as hero. `DRIVE-AUDIT-LISTICLE.md`'s own curated "hero"
   role map (real lifestyle/install candidates) is never consulted by code.

2. **The same hero photo is reused across five unrelated ads.** All five Fuji-product ads
   (hidden-costs-v2, still-lessthan300-4x5, still-levelup-4x5, still-infraredglow-4x5,
   still-unforgettable-4x5) ship the byte-identical hero image
   (`asset-peak-saunas-fuji-...-1.jpg`, 279,704 bytes) in both their `longform` and
   `product-page` pages. A reader who sees two of these ads sees the same top-of-page photo
   twice.

3. **`product-page` has zero unique imagery of its own in 4 of 7 ads.** In
   `still-lessthan300-4x5-wngk/product-page`, `still-levelup-4x5-fg3w/product-page`,
   `still-infraredglow-4x5-nvz3/product-page`, and `still-unforgettable-4x5-ffl3/product-page`,
   every image (2-3 per page) is a repeat of one already used in that same ad's `longform`
   page -- 100% cross-cartridge duplication, not merely "some overlap." `price-comparison-v2`
   and `product-features-v2`'s `product-page`s repeat 1-2 of their `longform`'s images too.

4. **Two PNG heroes are 4-8x the size of an equivalent JPEG, unconverted.**
   `asset-peak-saunas-mini-...-1.png` is 1,209,801 bytes (1.18 MB) and ships as the hero on
   `product-features-v2-57yx/product-page`, `price-comparison-v2-ebcu/longform`, and
   `price-comparison-v2-ebcu/product-page` (three pages, one file). A second PNG on
   `product-features-v2-57yx/product-page`, `asset-peak-saunas-mini-...-2.png`, is only
   848x849 px yet 1,101,350 bytes (1.08 MB) -- a near-square image at roughly 1.5 KB/px²,
   worse per-pixel than the 1.18 MB hero. `render.resize_asset_bytes`'s
   `ASSET_PNG_MAX_BYTES` cap (1.5 MB) lets both through un-converted because they're under
   the threshold, not because they're efficient.

5. **`article` and `listicle` pages have no hero at all -- nothing gets priority loading.**
   `cartridges/article/template.html` and `cartridges/listicle/template.html` have no
   `page.hero`-equivalent slot; every image in both templates is emitted with
   `loading="lazy"` (article's `page.images` loop, line 31; listicle's `page.reasons[].image`
   loop, line 127) including the first image a reader reaches, right after the headline/hook.
   All 3 `article` pages per ad and both `listicle` pages lazy-load their most-visible image,
   which is the opposite of what `loading="lazy"` should do for an above-the-fold image and
   actively hurts LCP.

6. **One image slot renders with no `loading` attribute at all, inconsistently.**
   `cartridges/product-page/template.html` line 49 (`angle_asset`, the mid-page "angle"
   section image) has no `loading="lazy"` even though every sibling secondary image in the
   same template (`detail_images`, line 72) does. Confirmed on
   `product-features-v2-57yx/product-page` (`asset-listicle-17UghmkrmU...jpg`, 2nd `<img>` in
   the file, `loading` attribute simply absent) -- the browser defaults to eager for an image
   that is not the hero and was never meant to be.

7. **Every image on a page can carry byte-identical alt text.** `render.asset_alt()` derives
   alt from `{product_short_name} – {kind}` only; `kind` is one of a half-dozen buckets
   (`product photo`, `installation photo`, `lifestyle photo`, ...), so 3-5 images on one page
   routinely get the exact same string. Example:
   `hidden-costs-v2-7tnk/longform/index.html` has 4 images, all `alt="Peak Fuji 2-Person
   Infrared Sauna – product photo"`, indistinguishable to a screen reader or search crawler.
   No image's alt is empty or a raw filename (that much is fine), but "meaningful and
   distinguishing" is not met.

8. **No `srcset`, `sizes`, or `<picture>` anywhere.** `grep -rl srcset
   tenants/peak-saunas/out/20260911-*/*/index.html` and the same for `<picture` both return
   zero files across all 23 pages. Every client downloads the same single 1600px-long-edge
   JPEG/PNG regardless of viewport -- a phone on the listicle page (7 images, up to 332 KB
   each) downloads the same bytes a desktop would.

9. **No fixed aspect box; layout can jump.** `structure.css`'s only sizing rules are
   `img { max-width: 100%; height: auto; }` (line 38) and `.adv-hero-image { width: 100%;
   border-radius: 8px; }` (line 135) -- no `aspect-ratio`, no `object-fit`, anywhere in the
   file. Hero images ship at genuinely different aspect ratios between ads/cartridges
   (1600x1600 square on most Fuji pages, 1067x1600 portrait on Mini's `listicle`-sourced
   hero, 848x849 near-square on the oversized product-page PNG) with nothing to normalize the
   box they render into, so the hero's height relative to the page varies page to page and
   nothing reserves space before the image decodes.

10. **The review HTML inliner already handles the current markup correctly, but nothing
    validates it stays under a size budget.** `harness/review.py`'s `inline_assets_as_data_uris`
    correctly base64-inlines every `src="assets/..."` (spot-checked
    `product-features-v2-longform-review.html`: 4 `data:image/...;base64,` URIs, file size
    2.59 MB, well under any reasonable cap today). But there is no check anywhere that
    enforces a ceiling -- `docs/IMAGE-MAP.md` itself documents a prior incident where an
    unresized Drive original inlined at full size produced a 46 MB review file, and nothing
    in the current code would catch a regression like that before a reviewer's browser tries
    to load it.

## Markup summary (all four `template.html` + `structure.css` + `harness/blocks/*`)

- **Aspect boxes**: none. **`object-fit`**: not used anywhere in `structure.css` or any
  block's own CSS.
- **Lazy loading**: applied inconsistently -- present on most non-hero images, absent on
  `product-page`'s `angle_asset` (problem 6), and wrongly applied to the *only* image in
  `article`/`listicle` that's above the fold (problem 5) since neither template has a hero
  concept.
- **`srcset`/`sizes`**: absent from every cartridge template and every image-bearing block
  (`hero-editorial`, `hero-split`, `hero-fullbleed`, `numbered-reason`, `press-logo-strip`).
- **Captions**: `article`'s `page.images` loop is the only place with `<figcaption>` support
  (conditional on a writer-supplied `img.caption` field); no other cartridge or block
  supports a caption at all, even though several (`longform`'s `step_asset`, `product-page`'s
  `detail_images`) are exactly the kind of secondary image a caption would help.
  `img.caption` was never populated in any of the 23 sampled pages.
  (article's `cartridge.md`/`schema.json` are cycle30's files -- this audit only reports
  what the template already conditionally supports.)
  - **Full-bleed hero on mobile**: no mobile-specific hero rule exists in `structure.css`
  (checked the `@media (max-width: 480px)` block, lines ~176-230) -- `.adv-hero-image` is
  `width: 100%` at every breakpoint, which is full-bleed-by-accident on mobile (no side
  padding override) but not "contained on desktop" by design; it's just the same rule at
  every width, so there's no actual mobile/desktop distinction to point to.
- **Markup duplication risk**: 7 separate `<img ...>` call sites across the 4 cartridge
  templates, plus 5 more inside `harness/blocks/{hero-editorial,hero-split,hero-fullbleed,
  numbered-reason,press-logo-strip}/block.html`, each hand-writing its own attribute list --
  already visibly drifted (problem 6 is exactly this: one call site simply forgot
  `loading="lazy"`).
- **Review inliner**: handles today's plain `<img src="assets/...">` correctly (see problem
  10); has never been exercised against `<picture>`/`srcset` since neither exists yet.

## Checks currently enforced (`harness/pagechecks.py`)

Only one image-specific check exists today: `find_image_allowlist_violations` (every
`asset_id` in `page.json` must exist in `facts_pack.assets`). Nothing checks for missing
width/height/alt on the rendered `<img>` (moot today only because `render_page` always adds
them itself), hero presence, duplicate asset ids within or across a page, or an
undersized/favicon-like image landing in a content slot.
