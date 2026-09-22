# PEAK brand files -- notes

Cycle 52 (2026-09-22) applied the finalized PEAK rebrand to this tenant.
The previous extraction (the live Aurora storefront: green `#16C47F`,
DM Sans / Poppins, white ground) is kept beside these files as
`tokens-legacy.json` and `base-legacy.css`. Neither is loaded.

## Name

Display name is **PEAK**. Never "Peak Saunas", never "PEAK SAUNAS", in any
new copy or label. Unchanged: the domain, the email addresses, every URL and
product handle, and the legal entity line (Peak Wellness USA LLC) wherever a
legal line is rendered. Shopify product titles still begin with
"Peak Saunas " on the storefront; the harness strips that prefix before a
product name is displayed or handed to the writer -- see
`tenant.yaml`'s `product_display_strip_prefix`.

## Palette (source: PEAK design system 2026, gbrain projects/peak-saunas/design-system-2026)

| Name | Hex | Role |
|---|---|---|
| Basalt | `#181918` | primary dark: text on light, dark grounds, default logo on light |
| Stone | `#EFE3D2` | primary light ground, text/logo on dark |
| Fossil Dust | `#C0C8C3` | secondary light ground: panels and cards |
| Red | `#702B33` | secondary: editorial colour blocks, headline panels |
| Cedar | `#483215` | thin accent bands, rules |
| Solar Flare | `#F27046` | accent: CTAs and highlights, sparingly |

No green, cyan, gold, purple or navy. No gradients, glows or drop shadows.
No pill shapes and no rounded cards: **radius 0 everywhere**.

Contrast rules that are not negotiable: buttons are a Solar Flare fill with
**Basalt** text (6.03). Never light or white text on Solar Flare (2.92).
Never Basalt text on Red (1.75). Solar Flare on Cedar or Red is large text
only.

`--ps-muted` is `#54524C`, Basalt mixed 28% toward Stone: 6.17 on Stone and
4.57 on Fossil Dust, so muted body text passes AA on both light grounds.

## Fonts

- **Epika** (secondary; paragraph titles, editorial moments, sentence case)
  is **licensed** -- Superior Type webfont EULA, in the toolbox's Fonts
  folder. `Epika-Regular.woff2` and `Epika-Regular.otf` are self-hosted in
  `fonts/` and declared with `@font-face` in `base.css`. Fallback stack:
  `"Epika", "Instrument Serif", "Playfair Display", Georgia, serif`.
- **Acid Grotesk license pending; fallback in use.** The toolbox carries a
  TRIAL build only, which must not be used, so nothing is self-hosted for
  the main face. `--ps-sans` is
  `"Acid Grotesk", "Schibsted Grotesk", "Inter", Helvetica, Arial, sans-serif`;
  in practice pages render in Schibsted Grotesk or Inter. Buy the licence and
  drop the webfonts into `fonts/` to finish this.
- One weight only. No bold, no italics for emphasis -- scale, case and
  placement carry it instead.
- Web scale: display 64/72, h1 48/56, h2 32/40, h3 25/32, body 18/28,
  small 14/20. Labels and eyebrows are uppercase 12px at 0.08em.

## Logo

Toolbox files only -- the wordmark is never redrawn and "PEAK" is never set
in a font. Downloaded from the toolbox root Drive folder
`1D8cG1cAY9sH0_fzMsUhseK_5sSWa_KCD`, `1 - Peak Logotype/RGB/`
(folder `1WM-zbfE94cpG4H_eZqCTTU-Jr9RGbRV8`):

| File here | Toolbox file | Drive id |
|---|---|---|
| `logo.svg`, `logo-basalt.svg` | `RGB-Peak-Logotype-Basalt.svg` | `1LPfUBvpBM-m_2-Qi5c7v7UbtnnrCHb-Q` |
| `logo-basalt.png` | `RGB-Peak-Logotype-Basalt.png` | `1rmbeUQhHUBGTrJ66OtdpvgwRkcPdqL4M` |
| `logo-on-dark.svg`, `logo-stone.svg` | `RGB-Peak-Logotype-Stone.svg` | `1_-JPu5BZat1BrIgII25Xei6w6XD7bCqe` |
| `logo-stone.png` | `RGB-Peak-Logotype-Stone.png` | `1xZsv0PxU7qQei7UvuaO4VvyuPf9PoIwz` |

`harness/render.py`'s `find_tenant_logo` prefers `logo.svg`, so a generated
page shows the Basalt wordmark on its Stone ground, which is the rule:
Basalt logo on light, Stone logo on dark, and **never** a Stone or White
logo on Solar Flare. Clear space is the height of the A's chevron cap.

Two details worth knowing rather than discovering: the toolbox SVGs fill
with `#171817` (Basalt) and `#eee2d1` (Stone), one or two steps off the
palette hexes above. They are the supplied artwork and are kept byte-exact.
And there was never a `logo.png` committed under `brand/` to rename -- the
old mark was referenced by storefront URL only (`tokens-legacy.json`'s
`logo.url`).

## Fonts on the storefront

The Shopify export keeps only what was inside `<body>`, so the `@font-face`
declarations travel there through `harness/page_body.py` (which now carries
them into the exported `<style>` alongside the `:root` tokens) and their
relative `brand/fonts/...` URLs are rewritten to Shopify CDN URLs at publish
time by `harness/publishers/shopify.py`. Uploaded URLs are cached in
`fonts/cdn-manifest.json`, so a font is uploaded once and reused. If a font
cannot be uploaded, the export keeps the fallback stack and the publish logs
which file failed.
