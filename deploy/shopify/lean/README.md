# Lean landing layout (cycle 110)

Two theme files that render a landing page without the store theme's chrome:

| Theme asset key | Repo file |
|---|---|
| `layout/landing.liquid` | `deploy/shopify/lean/layout/landing.liquid` |
| `templates/page.landing.liquid` | `deploy/shopify/lean/templates/page.landing.liquid` |

A page uses them when its `template_suffix` is `landing`. The publisher sets that suffix on **landing pages
only**, and only when `tenants/<t>/tenant.yaml` has `landing_lean_layout: true` (default `false`); every other
page keeps `shopify_publish.template_suffix` (`pk-harness`).

## Why

Measured 2026-10-15 at 375 x 812 on a live arm inside the theme: the promo bar (44px) and header (72px) cover
y 0-116, the "Chat with Becca" bar (68px, y 744) and the sales-notification popup (192px, y 620) cover the
bottom, so about 485px of our page shows on load; the trust line and the photo were under the popup. Our page
is 49 KB HTML + 34 KB images; the theme loads 2.97 MB / 338 requests around it.

## What the layout keeps

- `{{ content_for_header }}`: Shopify's own head (analytics, the customer-privacy banner, web pixels) and the
  app embeds Shopify injects into every layout.
- The two body fonts the theme already serves: DM Sans 400/500 and Poppins 500/600 (Google Fonts). The page
  body's own CSS brings Peak Grotesk Wide (5 KB woff2 from the theme CDN) for the stat figures.
- A 44px top row with a small `PEAK` wordmark linking to the home page.
- The page body (`page.content`) raw, inside `<main id="MainContent">`.

## What it drops

- The theme's stylesheets and scripts (`theme.liquid`'s `{{ 'base.css' | asset_url }}` and the rest).
- The promo / announcement bar, the header and its navigation, the cart drawer, the footer.
- The theme's sales-notification popup and "Peak Assist" / chat bar snippets, when the theme renders them.
- Theme-loaded review widgets (Judge.me snippets in the theme).
- Every web font except DM Sans and Poppins (no Montserrat, Inter, Figtree, Acid Grotesk).

## What it cannot drop (check after the first publish)

App embeds turned on in the theme editor (Online Store > Themes > Customize > App embeds) are injected by
Shopify into every layout, this one included. If the sales popup, the chat bar or Judge.me are app embeds,
they still load here; `harness theme verify-landing-layout` reports their markup. Turning an embed off is
theme-wide (every page), so that is an owner decision, not part of this layout. Analytics or tags pasted into
`theme.liquid` by hand (not through `content_for_header`) do not run on lean pages: check GA4 / Meta pixel page
views on the first lean page.

## Commands (operator; the harness never pushes on its own)

```
cd ~/advertorial
# 1. what would be written to each theme of the split (Caleb 182943580461, Austin 188604088621)
.venv/bin/harness theme push-landing-layout --theme 182943580461 --theme 188604088621 --dry-run --tenant peak-saunas
# 2. write them (a differing theme copy is backed up first to tenants/peak-saunas/out/theme-backups/)
.venv/bin/harness theme push-landing-layout --theme 182943580461 --theme 188604088621 --tenant peak-saunas
# 3. turn the flag on (tenant.yaml landing_lean_layout: true), publish ONE unlinked landing page, then
.venv/bin/harness theme verify-landing-layout https://peaksaunas.com/pages/<handle> --tenant peak-saunas
```

The verify step fails on promo bar, sales popup, chat bar, Judge.me widget or theme header markup; on more than
800 KB for the HTML plus the stylesheets, scripts and images it references; and on a theme in
`shopify_publish.theme_ids` missing either file. The theme a visitor sees is chosen by the Intelligems split
(cookie `pk_ab_theme_split_2026_10`), so both themes need the files before the flag goes on for live pages.

Rollback: set `landing_lean_layout: false` and re-publish the page (`harness publish --update`), or delete
the two assets in the theme editor; a page whose suffix names a template the theme lacks falls back to the
theme's default page template.
