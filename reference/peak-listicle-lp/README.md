# peak-listicle-lp — "5 Reasons People Love The Peak Mini Sauna"

Static replica of `https://landing-page-builder-mbmcgarry.replit.app/listicle/`
(a Vite + React + Tailwind v4 + Framer Motion build), rebuilt as plain HTML/CSS/JS.

## Files

| file | what it is |
|---|---|
| `index.html` | Standalone replica — includes the reference's own logo header and Shop/Our Story/Support footer, images from `assets/`. This is the 1:1 artifact; serve it with the `peak-listicle-lp` entry in `.claude/launch.json` (port 4791). |
| `shopify-body.html` | What is live on peaksaunas.com — the same page as `body_html` for a Shopify page. Header and footer removed (the Aurora theme supplies them), images point at the Shopify CDN, CTAs point at `/collections/all`. |
| `assets/` | Source images, resized for web. |

## Live

- Page: `https://peaksaunas.com/pages/5-reasons-to-love-peak-saunas` — page id **155864727853**
- Vanity URL: `https://peaksaunas.com/5-reasons-to-love-peak-saunas` → 301 → the page — redirect id **548101325101**
- Dark-launched 2026-08-12: published, no nav link.
- Rollback: `DELETE /admin/api/2024-10/pages/155864727853.json` + `DELETE .../redirects/548101325101.json`

## How the replica was derived

Values were read off the live reference, not guessed:

- Design tokens from its `:root` — primary `hsl(221 44% 18%)`, accent `hsl(156 80% 43%)`,
  secondary `hsl(40 20% 97%)`, border `hsl(221 20% 90%)`, muted `hsl(221 20% 50%)`,
  display font Poppins, body font DM Sans.
- Motion params from the JS bundle: copy blocks `{opacity:0,y:40} → {opacity:1,y:0}`,
  0.8s `cubic-bezier(.16,1,.3,1)`; images `{opacity:0,scale:1.05} → scale 1`, 1.2s ease-out;
  viewport `{once:true, margin:"-10%"}`. Reproduced with IntersectionObserver + CSS transitions.
- Layout verified by comparing bounding boxes at 1280×720: all 7 sections, both `h1`/`h2` sets,
  every image and the footer match the reference to the pixel, and document height is 5161px in both.

## Deviations from the reference (deliberate)

1. **Images 4 and 5 are broken on the reference.** `sauna_lifestyle.jpg` (35.6 MB) and
   `_AR42290.jpg` (46.3 MB) exceed the host's response limit and return HTTP 500 there.
   Recovered with HTTP range requests, downscaled to 1600px long edge, and shown.
2. **Webfonts are loaded.** The reference declares Poppins/DM Sans but only links Inter, so it
   renders in whatever the visitor happens to have installed. Poppins and DM Sans are linked here.
   Metrics differ slightly from a locally-installed Poppins, so the `h1` can be ~3% wider.
3. **Header and footer dropped in the Shopify build** — the theme already renders both.
4. CTAs point at `/collections/all`; they are dead `#` links in the reference.

## Traps

- The Aurora page template wraps `body_html` in `.container--small` with
  `.page__content{margin:3.2rem 0 0}`. Full-bleed comes from `:has()` rules at the top of
  `shopify-body.html` that neutralise the container padding, the section spacing, the
  `.page__content` margin, and the duplicate `.page__title`. Removing them re-narrows the page.
- Editing this page in Shopify admin's rich-text editor can strip the `<style>` block. Edit via
  the API only (`/tmp/pk_update_listicle.py` on prod reads `/tmp/pk_listicle_body.html`).
- The storefront serves page updates from several cache epochs. Verify with ≥8 pulls, not one.
