# Peak Saunas brand extraction -- notes

## Source pages fetched (read-only, curl with a real UA)
- https://peaksaunas.com/ (home.html)
- https://peaksaunas.com/products.json (to find the Fuji handle)
- https://peaksaunas.com/collections/all
- https://peaksaunas.com/products/peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy (product-fuji.html)
- https://peaksaunas.com/blogs/saunas/best-sauna-brands-2026 (blog-best-brands.html)
- https://peaksaunas.com/pages/why-trust-peak-saunas
- https://peaksaunas.com/pages/austin-laudenslager
- Theme CSS: https://peaksaunas.com/cdn/shop/t/43/assets/bundle.css (684KB) and .../peak-assist.css
All raw HTML/CSS saved under scratchpad/brand-raw/ for this session; not copied into /Users/calebniednagel/advertorial/brand/.

## Theme
- Name: "Live Peak Saunas Site" (shop's theme label, not a template name)
- Schema: **Aurora**, schema_version **4.0.1**, Shopify Theme Store id 1770
- Found in a `theme = {...}` JS object in the homepage `<head>`.

## Review app
- **Judge.me**, loaded as a Shopify extension (id `01a086c4-2869-7ea0-af53-a261e1038ec8`, `judgeme-749`).
- CSS: `https://cdn.shopify.com/extensions/01a086c4-2869-7ea0-af53-a261e1038ec8/judgeme-749/assets/shopify_v2.css`
- Judge.me CSS vars are all set to the brand green `#16C47F` (--jdgm-star-color, --jdgm-primary-color, etc.) -- the store's stars are green, not gold.
- The visible star widget on product cards/headers is actually a **custom** widget (`.pk-rating` / `.pk-stars`), not raw Judge.me markup: 5 unicode stars in a muted gray (`#D8DAD5`) with an absolutely-positioned green (`#16C47F`) overlay clipped by width to show the fill percentage. Reproduced in base.css as `.pk-stars` / `.pk-stars-on`.
- Site-wide aggregate rating (schema.org JSON-LD on product pages): 4.76 / 5, 3,958 reviews.

## Important structural finding
The site already runs its own advertorial/buyer's-guide content system, prefixed `pkx-` (`pkx-byline`, `pkx-tblwrap`, `pkx-direct`, `pkx-trust`, `pkx-fine`, `pkx-bio-*`), injected as an inline `<style>` block inside the article's rich-text field on both the blog post and the two /pages/ URLs. This is a closer match for "generated landing pages" than the theme's generic chrome, so base.css leans on this system (8px card radius, `#E9EAEC` borders, `#F4F5F6` zebra rows, `17px`/`1.75` body copy) rather than the theme's own product-card radius (2px). Noted both in tokens.json under `layout.card_radius_theme_px` vs `layout.card_radius_content_blocks_px`.

## Colors
- Primary color scheme (`.color-primary`, applied to `<body>`): bg `#ffffff`, text `#000000`, foreground/section-tint `#ebf9f2`, border `#efebdd`, button bg/accent `#16C47F`, button text `#ffffff`.
- Dark/secondary scheme (`.color-secondary`, footer-type sections): bg `#252525`/`#37`ish dark gray, text `#ffffff`, button bg `#fffdf5` (cream), button text `#222222`.
- The only **red** found anywhere in live CSS is `#c33b3b`, used solely for a shipping-calculator form-error state. The site has no red brand accent in general use -- if an ad needs a red accent (per the user's framing of the current ad creative), treat it as an ad-specific addition, not something sourced from the theme.
- Author-bio pages (e.g. /pages/austin-laudenslager) use a one-off secondary accent, a gold/tan `#d8b189`, on a dark hero card. This is scoped to bio pages only and was not folded into the main token set.

## Fonts
- Body: **DM Sans**, self-hosted on Shopify's asset CDN as woff2/woff (not Google Fonts). Weights present: 400/500/600/700.
- Headings: **Poppins**, same CDN hosting pattern, weights 400/500/600/700.
- A third face, **Figtree**, is used narrowly for header icon labels and product-card prices -- included in tokens.json for completeness but not wired into base.css since it isn't a primary content font.
- Root `html{font-size:62.5%}` means `1rem = 10px` against a 16px browser default; the theme then layers a `--gsc-body-font-scale:1.1` multiplier on top of the 1.6rem (16px) base body size, landing on ~17.6px rendered. base.css uses that resolved 17.6px directly rather than replicating the two-step calc.
- Heading sizes (h1-h4) are responsive with 4 breakpoint tiers in the CSS; NOTES + tokens.json record the **largest (desktop) tier** since that's what a landing page should target by default: h1 44px, h2 40px, h3 36px, h4 32px.

## Layout
- Max content width: **1320px** (`--gsc-large-container-width: 132rem`). Medium/small container variants exist (1140px/960px) for narrower content; the full-bleed background container is 2560px and is not a text-width value.
- Section vertical spacing is set **per-section** by the page builder, not a single fixed token. 48px was the most common value sampled on the homepage; some hero-type sections use larger custom values. Treated 48px as the safe default (`.section`) and added an optional `.section--lg` (80px) for hero-scale spacing -- this second value is an estimate, not a scraped constant.

## Uncertainties / things not found
- No literal "financing pill" or "Advertisement" label exists anywhere on the live site -- both are new to the advertorial use case. `.financing-pill` and `.ad-label` in base.css were designed to match the brand's existing green-pill (`.badge`) and muted-label visual language rather than lifted from a specific element.
- Exact desktop-vs-mobile section spacing scale (i.e. how much bigger spacing gets above the ~750px breakpoint) wasn't fully mapped; only one clean top/bottom pair was visible per section in the fetched HTML, so the "lg" variant is a reasonable estimate rather than a scraped value.
- Button hover state is not a color swap -- it's a `rgba(255,255,255,.2)` gradient overlaid on the existing background (subtle lighten). Reproduced as-is in base.css.
- Did not verify Figtree's full weight set (only saw weight 400 loaded on the pages fetched); if it's ever promoted to a primary content font, re-check weights before relying on 500/600/700.
