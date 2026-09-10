# Image map

Two independent Drive indexes, both read-only and re-indexed by hand (no live Drive API
call inside `adv run`):

| Drive folder id | Local index file | Covers |
|---|---|---|
| `16gomD6ar2ZasJTepvuZv-aLT2GorLvz1` | `brand/assets.json` | The full brand library, every model (lifestyle/render/installation/video/logo/ugc), 1,046 files indexed. |
| `1TJIKdHrD4AheKYq-0D4dLnbGfZrFwIVA` | `brand/assets-listicle-pack.json` | A separate, curated pack for the listicle cartridge -- Mini and Matterhorn only, 105 files (`photo_product`, `photo_install`, `still_video`, `ai_render`). |

Each index row: `id` (the Drive file id), `title` (raw filename, not a description),
`mimeType`, `bytes`, `model` (slug or `null`), `kind`, and -- listicle-pack rows only --
`ai_generated` (true for a Firefly/Gemini/gpt-image composite, never a real photo).
`download_url_pattern` on each index (`https://drive.google.com/uc?export=download&id={id}`)
is confirmed to serve the real file directly, not an HTML interstitial.

## How slots are filled per cartridge

`adv/ground.py`'s `LocalFactsSource.facts_for()` assembles one `assets` list per run, in
this order:

1. **Shopify product images** (`select_drive_assets`'s sibling, built from
   `claims/products.json`'s own `image_urls`) -- always first, so the writer's hero slot
   defaults to a real product photo already on the live site.
2. **`brand/assets.json` Drive assets** (`select_drive_assets`) -- up to 6, tiered
   lifestyle/interior first, then render, then installation; `video`/`logo`/`ugc` and any
   row flagged `excluded` are never eligible.
3. **`brand/assets-listicle-pack.json` assets** (`select_listicle_pack_assets`, fix cycle
   15) -- only added when the run's product is Mini or Matterhorn. Real photos
   (`photo_product`, `photo_install`, plus brand-general `still_video` stills) are always
   preferred; an `ai_render` is only ever eligible when `claims/config.json`'s
   `allow_ai_renders` is `true` (default `false`). An asset actually rendered with
   `ai_generated: true` gets "Rendering:" prefixed to its alt text by
   `adv/render.py`'s `asset_alt()` -- the renderer's own field, so the model can't drop
   or reword it.

The writer picks an `asset_id` from this combined list per image slot in `page.json`; it
never invents a URL or writes its own alt text.

## Where files land per run

`adv/render.py`'s `render_page()` downloads only the assets the page actually references
(`collect_asset_ids`) into `out/<run-id>/<cartridge>/assets/<asset-id>.<ext>`, rewrites
each image's `src`/`url` to that relative path, and (fix cycle 15) downscales every one to
a 1600px long edge, re-encoded at JPEG quality 82 (a PNG stays a PNG unless it would still
exceed 1.5 MB, then it also converts to JPEG) -- logging original and final byte counts.
`adv review out/<run-id>` then inlines each cartridge's images as `data:` URIs into
`<cartridge>-review.html`, which is why the per-run downscale matters: without it, a
single 12+ MB Drive original inlined at full size is how the Mini sample review file
reached 46 MB.

## What the Shopify assets manifest is for

`adv shopify-body out/<run-id>/<cartridge>` writes `shopify-body.assets.json` alongside
`shopify-body.html`: one entry per image the page body references, each with its local
`assets/...` path, the renderer-derived alt text, and an intended Shopify Files CDN
filename (`pk-<cartridge>-<NN>-<slug-of-alt>.<ext>`). It exists for a later, not-yet-built
publish step to know what to upload and what filename to give each file on the Shopify
Files CDN -- the image `src` in `shopify-body.html` itself stays a relative local path
until that step exists. No Shopify API call is made anywhere in this harness today.
