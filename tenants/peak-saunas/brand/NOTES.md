# PEAK brand files -- notes

Cycle 78 (2026-10-05): generated pages match the **live storefront theme**
("mono", theme 182943580461). The 2026-09-22 rebrand palette (cycle 52:
stone ground, fossil-dust panels, cedar rules, solar-flare orange CTA,
Acid Grotesk uppercase headlines, Epika) is retired. The owner banned all
orange on 2026-09-28. History: `docs/FIXLOG.md` cycles 52 and 78,
`tenants/peak-saunas/docs/REBRAND-APPLIED-2026-09-22.md` (superseded).

## What the engine reads

- `base.css` -- the `:root` `--ps-*` tokens every cartridge resolves, and
  the "live theme layer" (headings, CTAs, no shadows, no bottom sticky
  bar). The storefront export carries both, scoped under `.adv-wrap`.
- `logo.svg` -- the PEAK wordmark in Basalt (`find_tenant_logo`).
- `photo-library.json` -- photo tags, including `old_logo_visible`.
- `tenant.yaml` `brand:` -- `headline_case: none`, `font_stylesheet`,
  `theme_marker` (`data-pk-theme` on the page wrapper).

`tokens.json` is the readable reference for the same decisions.

## Palette

| Name | Hex | Role |
|---|---|---|
| Basalt | `#161817` | dark bands, footer, primary CTA fill |
| Ink | `#1A1A1A` | text |
| Red | `#702B34` (theme `#712B33`) | CTA hover, badges, numerals |
| Stone | `#F0E5D3` | accent panels only (spec boxes) |
| White | `#FFFFFF` | page ground |
| Panel | `#F1F1F1` / `#F2F2F2` | light panels |
| Muted | `#616161` / `#6B6B6B` | secondary text, labels |

Never: orange, green/cyan, cream, sage, cedar, gradients, glows, shadows,
all-caps headings.

## Type

DM Sans 400/500 body (16px / 1.55). Poppins 600 headings, sentence case,
-.01em. Both are loaded site-wide by the theme; the review page loads them
from Google Fonts. No `@font-face` is emitted, so nothing is uploaded to the
storefront at publish time.

## Logo

`logo.svg` / `logo-basalt.svg` / `logo-basalt.png`: PEAK wordmark, Basalt.
The theme's own marks (wave icon, `pk-icon-white.png`,
`pk-logotype-white.png`, `pk-logo-red.png`) and the new-mark product
cut-outs (`pk-cutout-<model>.webp`) live on the theme and are not in this
repo.

## Removed in cycle 78

`base-legacy.css`, `tokens-legacy.json` (pre-rebrand green theme record),
`logo-on-dark.svg`, `logo-stone.svg`, `logo-stone.png` (cream wordmark),
`fonts/Epika-Regular.*` and `fonts/cdn-manifest.json`. Nothing read them.
They are in git history.
