# Block catalog (cycle 91)

A look used to arrange only the few fields its cartridge writes, so no look
could show a photo essay, a hotspot image, stats inside the hero, a feature
grid, a model card or a dark statement band. The block catalog
(`harness/blocks/catalog/`) adds those sections. The harness owns each
block from end to end. A look composes blocks. The writer fills only the
fields of the blocks that the look uses.

Do not confuse the catalog with the cycle 34 registry in the same folder
(`harness/blocks/registry.json`, kebab-case names such as `proof-stat-row`).
A registry block is a layout variant of one slot and carries no data. A
catalog block (snake_case id) is a whole section with its own data, gate,
markup and CSS.

## The catalog

| id | what it shows | writer fields | data source |
|---|---|---|---|
| `stat_strip` | 3-4 stats, a display value over a short red rule and a label. Option `layout`: `band` (light ground) or `inline` (inside a dark hero). | none | `first_screen.stats` (rating, standard outlet, width, capacity, free shipping), filled from `landing.spec_strip` |
| `photo_essay` | 2-4 photos, each with one short line. Option `layout`: `contained` (a grid) or `bleed` (edge to edge). Option `ad_still`: add the ad still as a last photo with no line. | `lines[]`: `{text, claim_ids}`, one for each photo, at most 14 words; a line with a number cites a verified claim | photo-library photos in `facts_pack.assets`, by tag: a room shot, a detail, an exterior, then the next best of each |
| `hotspot_image` | the product image with 3-5 numbered dots. On a phone, the labels are a list under the image. From 768px, each label is beside its dot. Option `hero`: load the image eagerly. | none | the product cut-out, else an exterior photo. The labels come from verified specs and claims (heater, bench, panel, glass, control, wood). The positions come from `tenants/<t>/brand/hotspots.json`, else the dots are placed evenly. |
| `feature_grid` | 3-6 cells: an icon, a label, a line | `cells[]`: `{label, line, claim_ids}`; label at most 3 words, line at most 14 words, at least one verified claim id | the icon comes from what the cell's claims state (the proof look's icon set). The writer does not pick it. |
| `model_card` | the run's product: cut-out, name, rating, price, financing sentence, two specs, the CTA. Option `variant`: `card`, or `panel` (a buy panel: no image, every spec row). | none | `landing.price`, `first_screen.rating_stat`, `landing.spec_strip`, `pdp.spec_rows`, `pdp.financing_sentence` |
| `model_grid` | the card for each model that has a price claim and a cut-out. Each card links to the page's one CTA. | none (rule: `cta_text` names no model) | `facts_pack.models`, which is added only when the look declares this block |
| `statement_band` | a full-bleed dark band with one display line and the CTA pill | `line`: at most 12 words, no number | none |
| `faq` | the page's FAQ as plain Q/A on hairlines | none (it uses `page.faq`) | `page.faq.questions` |
| `trust_bar` | a thin dark strip at the top: warranty term, free shipping, the fixed financing sentence, the HSA/FSA claim | none | `landing.warranty_term`, `listicle.free_shipping_claim`, `pdp.financing_sentence`, `listicle.hsa_claim` |

Renderer-owned values never come from the model when a fact exists.
Prices, specs, stats, ratings, financing, labels, photos and icons are
computed from `facts_pack` and the tenant files. Every claim that a block
cites joins the page's Sources list. When a block's facts are missing, the
block renders nothing. It never shows a softened version.

## How a look uses blocks

1. Put a `look.json` next to the look's `template.html`:

   ```json
   {"blocks": ["stat_strip", "photo_essay", "feature_grid", "statement_band", "faq"],
    "options": {"stat_strip": {"layout": "inline"}}}
   ```

   This works for a built-in look (`cartridges/<c>/looks/<look>/`) and for a
   tenant look (`tenants/<t>/looks/<c>/<slug>/`). An unknown id, a repeated
   id, or options for a block that the look does not list makes the render
   fail (`catalog.parse_look_spec`).

2. In the template, include each block where it goes:

   ```jinja
   {% if blocks.feature_grid and blocks.feature_grid.cells %}
   <section class="cn-sec"><div class="cn-c">
     <p class="cn-eyebrow">A label the look owns</p>
     {% include "blocks/feature_grid.html" %}
   </div></section>
   {% endif %}
   ```

   The block's rendered context is `blocks.<id>`. Each block has its own
   key that shows whether it has content: `stats`, `photos`, `dots`,
   `cells`, `name`, `cards`, `line`, `questions`, `facts`. Most blocks sit
   inside the look's container and bring no gutters. `statement_band` and
   `trust_bar` are full-bleed bands, so include them outside any container.
   A block brings no heading. The look writes its section labels.

3. That is all. `render_page` renders only the declared blocks. Their
   images join the download set. The writer prompt gets only the fields of
   those blocks (`write._append_landing_guidance`). The gate checks only
   those blocks (`repair.check_page_gates(look=...)`, keys
   `blocks:<id>:<rule>`, paths `$.blocks.<id>.<field>`). The Library
   rebuilds a version when its `look.json` or one of its blocks changes.

A look with no `look.json` declares no blocks. Its `blocks` context is
empty and it renders as before. The landing `open` and `proof` looks
include `blocks/faq.html` without declaring it. The partial then emits
their old FAQ markup, byte for byte (`tests/test_blocks_cycle91.py` pins the
render hashes). A look that declares `faq` gets the catalog markup.

## page.json

The writer puts block fields in a top-level object:

```json
"blocks": {
  "photo_essay": {"lines": [{"text": "...", "claim_ids": ["..."]}]},
  "feature_grid": {"cells": [{"label": "...", "line": "...", "claim_ids": ["..."]}]},
  "statement_band": {"line": "..."}
}
```

A string value under `blocks` is still a cycle 34 registry slot pick (the
classic longform look). An object value is catalog fields. Each value has
its own gate. The gate fails these cases:

- a block that is not in the catalog;
- a block that the look does not use;
- fields written for a renderer-only block;
- a declared writer block that is left out. `photo_essay` is required only
  when the run has library photos to show.

The generic gates (claims, numbers, hype words, word range) walk block
fields like any other field. The deterministic pre-repair fixes also apply
to them. The degenerate-repair guard counts a dropped block as a lost
section, unless the gate told the writer to remove it.

## How to write a block

1. Write `harness/blocks/catalog/<id>.py` that calls `register(Block(...))`
   with these fields:
   - `id`, `title`, and `needs` (the data it reads);
   - `render(ctx) -> dict`, where `ctx` is a `BlockContext` with `page`,
     `facts_pack`, `tenant`, `options`, `assets` and `frame_asset`. Return
     `asset_ids` for any image that page.json does not name (add the asset
     to `ctx.assets`), and `claim_ids` for every claim the block shows;
   - optionally: `writer` (the schema fragment), `writer_lines(facts_pack,
     tenant)`, `gate(fields, facts_pack, tenant)`, `required(facts_pack,
     tenant)`, `page_gate(page, facts_pack, tenant)`, `options` (the
     defaults), and `covers` (the look-contract pairs that the block
     prints, for `harness/look_import.py`).

   State the prompt lines and the gate from the same constants. Reuse
   `catalog.number_reason`, `catalog.check_claim_ids` and the landing and
   listicle helpers.
2. Write `harness/blocks/catalog/<id>.html`. Start with a `{#- ... -#}`
   comment, then `{%- set bk = blocks.<id> if blocks and blocks.<id> else
   none -%}`, then a `<style>` whose selectors all contain `.bk-<id>`. Use
   tokens only: the `--bk-*` names map to `--pk-*`, then `--ps-*`, then
   `--adv-*`. Do not use a colour literal or a fixed width over 390px. Use
   `bk-<id>-*` classes. Put `data-look-pattern="<id>"` on the root element.
   Cycle 90's fidelity scoring reads that attribute. Use the CTA class of
   the cartridge (`ld-btn` for landing, `op-btn` for listicle) on any link
   to `page.cta_url`.
3. Add the import at the bottom of `catalog/__init__.py`, a row to the
   table above, and tests. `tests/test_blocks_cycle91.py` already checks
   every partial for the marker, the scope, the tokens and the widths.

## The two block-built looks

- `cartridges/landing/looks/cinematic/` (pack page 08): a full-bleed first
  screen with `stat_strip` inside it. The ground is dark behind the ad
  still or the real-room photo; with no photo it is light with the
  cut-out. Then the checked claims as numbered steps, then `photo_essay`,
  `feature_grid` and `statement_band`, then proof, a large price, `faq`,
  and the closing section.
- `cartridges/landing/looks/buybox/` (pack page 06): `trust_bar`, then
  `hotspot_image` beside a buy panel (eyebrow, headline, subhead and
  `model_card` in its `panel` variant), then the checked claims, the room
  photo, `faq`, and the dark closing band.

Select one with `--look cinematic|buybox`, or with the A/B/C entries
`landing:cinematic` and `landing:buybox`. Copies of both are tenant looks
for the peak-saunas Library: `tenants/peak-saunas/looks/landing/block-cinematic/`
and `.../block-buybox/`. These folders are gitignored runtime data, so the
operator copies them to prod.
