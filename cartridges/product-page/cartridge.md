# Cartridge: product-page  (v0.1.0)

Purpose: a stripped product page for one hero SKU, matched to the ad's angle. For retargeting and Shopping traffic that already knows the product.
Audience temperature: warm.

## Structure (in order)
1. Header: "Advertisement" label small, Peak logo, no nav.
2. Hero: product short_name, one-line promise taken from the ad angle, price, financing line ("Financing available" unless a lender is configured), one primary CTA (Buy / Shop <model>). Hero image from asset library.
3. Three proof bullets: each one verified claim with a short label and a one-line explanation. Choose the three that match the ad's angle.
4. Angle section: 1 H2 + 2–4 short paragraphs or an image-and-text pair that expands what the ad said.
5. Specs table: 6–10 rows from facts_pack.specs.
6. Trust strip: warranty, shipping/delivery, returns, review count and rating (from verified_claims only).
7. Repeat CTA (same text as hero).
8. Footer: disclosure paragraph.

## Rules
- 250–500 words. One CTA text, shown twice. No reviews carousel, no related products, no nav, no newsletter.
- Price always visible above the fold. Financing carries "/mo" and a lender name only if facts_pack.product.financing.lender is set; otherwise "Financing available" with no figure.
- Every bullet, spec, and trust item must map to a verified_claims id or facts_pack.specs row.
- Images: hero + 1–2 detail shots from the asset library.
- Voice: short declarative sentences. No superlatives without a source.

## From the ad
Angle → hero promise and angle section. Features shown → which proof bullets to pick.

## From facts_pack
Product, specs, price, financing, warranty, shipping, reviews summary, assets.
