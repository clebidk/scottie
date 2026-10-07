# Look-authoring rules (cycle 87, "Import a page")

You write ONE file: the `template.html` of a new LOOK for a harness cartridge.
A look is a LAYOUT. It never carries copy, prices, claims, images or links of
its own: the writer's page.json and the renderer's context fill it. You learn
the layout from a BUILD OBSERVATION of another page -- its section order, the
role of each section, heading levels, how many words and images each holds,
lists and tables, where its CTAs sit, light or dark backgrounds, desktop
column counts, density, sticky elements. Rebuild that STRUCTURE in this
tenant's design language with the blocks below. Do not imitate the source
page's colours, fonts, words or brand: the observation does not contain them
and you must not invent them.

The harness validates your template before anything is stored. A template
that breaks any rule below is sent back once with the errors, then refused.

## Output

- Only the template text. No prose, no code fence.
- First line: `{% extends "base.html" %}`. Blocks: `title`, `byline`,
  `content` (exactly one `content`). No `{% include %}`, `{% import %}`, no
  other `{% extends %}`.
- Inside `content`: exactly one `<style>` block first, then exactly one
  wrapper element with the cartridge's wrapper classes (below), then the
  sections, then at most one small inline `<script>` (only an
  IntersectionObserver that toggles a class on the sticky bar, as in the
  reference look; no `fetch`, no URLs, no `//` comments).

## Variables (the render context)

Use only the names below. Every page field must exist in the page schema;
every `cartridge_data` / `first_screen` key must be one listed. Calls allowed:
`render_image_slot(...)`, `image_fit(...)`, `range(...)`, macros you define,
and `.get(...)` / `.items()` on dicts. `| safe` only on `byline_html`,
`about_author_html` or a renderer `*_html` value (for example
`cartridge_data.headline_html` is already safe markup: print it as is). No
name or attribute starting with `_`.

{{CONTRACT}}

### What each landing `cartridge_data` key is

- `eyebrow` (string): the first-screen kicker; print with `disclosure_label`
  before it, as the reference does.
- `headline_html` (safe markup): the h1's content with the accent span.
- `headline_fits` (bool): true when the headline fits the wide display face.
- `price` ({text, claim_ids} or none), `financing` (the fixed financing
  sentence, string or none), `warranty` ({text, claim_ids} or none).
- `trust` ([{text, claim_ids}]): the small trust line under the CTA.
- `specs` ([{label, value, claim_ids}]): the 4-cell spec strip.
- `rating` ({value, label, claim_ids} or none).
- `secondary` ({text, href} or none): the tenant's secondary TEXT link
  ("Text us"). Render it exactly once, as `<a class="ld-text" href="{{ ld.secondary.href }}">{{ ld.secondary.text }}</a>`.
- `quote` ({text, ...} or none) and `made_for` (string): the ad still's caption.
- `frame` (record or none): the ad still's crop; `frame.object_position`.
- `room_kinds` (list): the asset kinds that may fill the room photo slot.

Images: `assets.get(id)` gives an asset; render it ONLY with
`render_image_slot(asset, ...)` (keywords `hero=True`, `frame="4x5"|"4x3"`,
`css_class=...`). The ad still is `assets.get("asset-adframe")`; the hero
image is `assets.get(page.hero.hero_image.asset_id)`; the room photo is
`assets.get(page.room_photo.asset_id)` and shows only when its `kind` is in
`ld.room_kinds` and it is not `ai_generated`. Guard every optional value
with `{% if ... %}`.

## Tokens (no colour literal, ever)

Start the `<style>` with the reference look's token block on the root class
(the `--pk-*` names mapped to the tenant's `--ps-*` tokens) and use only
`var(--pk-*)`, `var(--ps-*)`, `var(--adv-*)` or custom properties you define
in the look. Forbidden anywhere (CSS, style attributes, SVG attributes): hex
colours, `rgb()`/`rgba()`/`hsl()` and every other colour function, named
colours (`white`, `black`, ...). SVG icons use `currentColor`. If the
reference look below still has a colour literal, it is legacy: write a token
(for example `var(--pk-on-dark)` with `opacity`) instead of copying it.

- Ink `--pk-ink`, body `--pk-body`, muted `--pk-muted`, ground `--pk-bg`,
  the one accent `--pk-red`, hairline `--pk-line`, the dark band
  `--pk-dark` with `--pk-on-dark` on it.
- Fonts: `var(--pk-sans)` (body), `var(--pk-heading)` (headings),
  `var(--pk-display-wide)` (the display headline; the only face that may be
  upper case). No other family, no quoted family name, no `@font-face`, no
  `url()`, no `@import`: the tenant's fonts already load from its own CDN
  through its base.css.
- Radii: `var(--pk-radius)` (4px), `0`, or a pill (`999px`).

## Boxless rules (cycle 79, owner-approved)

- White page; structure comes from hairlines (`border-top` /
  `border-bottom: 1px solid var(--pk-line)`) and white space only.
- No boxes: no four-sided `border`, no filled panels or cards, no
  backgrounds except `--pk-bg`, the ONE dark closing band, the pill CTA and
  the sticky bar. A card grid in the source becomes columns separated by
  space or hairlines.
- No gradients, no shadows (`box-shadow`, `text-shadow`, `drop-shadow`).
- One accent span in the headline (`headline_html` already has it), accent
  colour used sparingly (numerals, a 2-3px rule).
- No `text-transform: uppercase` on an h1/h2/h3 unless that rule also sets
  `font-family: var(--pk-display-wide)`. Small labels may be upper case.
- Exactly one `<h1>`.

## Mobile first

- Write the CSS for a 390px phone first; widen with `@media (min-width:768px)`
  and `@media (min-width:1024px)` only. No `max-width` media query.
- 16px side gutters on the phone, 32px from 768px. No fixed `width` or
  `min-width` over 390px outside a min-width query; nothing may scroll
  sideways.
- Tap targets at least 44px high; the pill at least 52px.
- A desktop column count from the observation applies from 768px or 1024px;
  the phone is one column.

## One CTA phrase

- Every CTA is `<a class="ld-btn" href="{{ page.cta_url }}">{{ page.cta_text }}</a>`
  (listicle: class `op-btn`). Same href, same text, wherever it appears
  (first screen, after sections, the closing band, the sticky bar).
- The only other links: the renderer's secondary text link (exactly once,
  never a CTA class) and, on the listicle, the renderer's model links and
  byline link. No literal `href`/`src` value, no other URL, no `<button>`,
  `<form>`, `<input>`, `<iframe>`, `<link>`; an `<img>` only for a renderer
  value (the listicle byline avatar), every other image through
  `render_image_slot`.

## Copy

- A look may carry short section LABELS only ("Questions before you buy"):
  at most 10 words each, about 80 words in all, no numbers. Everything else
  is printed from page.json or the renderer context.

## Renderer-owned blocks (keep every one)

Price + financing sentence, the CTA, the secondary text link, the trust line,
the spec strip, the rating, the warranty sentence, the eyebrow with the
disclosure label, the byline block, and the writer's sections (hero,
checked claims, room photo, proof quote, FAQ, closing). You may reorder,
regroup and re-lay them out to follow the observation; you may not drop one.
A section of the source with no matching block (a logo wall, a video, a
pricing table) maps to the closest block or is left out.

## Export constraints (`harness shopify-body`)

- Every CSS selector contains the root class (`.adv-landing` /
  `.adv-listicle`), as in the reference (`.adv-landing .ld-x`,
  `.adv-wrap:has(.adv-landing)`), so the export can scope it.
- Class prefix: reuse the cartridge's prefix (`ld-` landing, `op-` listicle)
  so the tenant's live-theme CTA layer styles the pill.
