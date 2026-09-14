# Cartridge: product-page  (v0.1.0)

Purpose: a stripped product page for one hero SKU, matched to the ad's angle. For retargeting and Shopping traffic that already knows the product.
Audience temperature: warm.

## Structure (in order)
1. Header: "Advertisement" label small, the tenant logo, no nav.
2. Hero: product short_name, one-line promise taken from the ad angle, price, financing line (the exact allowed financing sentence given in the prompt's Financing rule -- see Rules). Hero image from asset library. CTA is `cta_text`/`cta_url` (top-level, see Rules) -- exactly one of schema.json's `allowed_cta_texts`, with {short_name} filled in with the product's actual short_name.
3. Three proof bullets: each one verified claim with a short label and a one-line explanation. Choose the three that match the ad's angle, and make at least 3 of them product-benefit claims (see Rules). Value-equation checklist (order matters): bullet 1 states the outcome the buyer wants; bullet 2 is proof that outcome is likely (a verified claim -- a spec, a review stat, a certification); bullet 3 is time to first benefit or install effort (how soon/how easily the buyer gets there). Use a different verified claim_id for each bullet -- never the price, warranty, or shipping/returns claim_id.
4. Angle section: 1 H2 + 2–4 short paragraphs or an image-and-text pair that expands what the ad said.
5. Specs table: 6–10 rows from facts_pack.specs.
6. Trust strip: warranty, shipping/delivery, returns, review count and rating (from verified_claims only).
7. Repeat CTA -- reuses the same `cta_text`/`cta_url` as the hero; the renderer places it, do not repeat it here.
8. Footer: disclosure paragraph. No byline -- this is intentional (a product page has no author, unlike article/longform/listicle); the disclosure paragraph still renders.

## Rules
- One CTA phrase per page: `cta_text` is a single phrase, chosen once, that may repeat verbatim in the hero and the repeat CTA -- never two different phrases anywhere on the page, and never a second, differently-worded CTA link or button.
- 250–500 words. Return top-level `cta_text` and `cta_url` exactly once -- the renderer reuses them verbatim in the hero and the repeat CTA. `cta_text` must be exactly one of schema.json's `allowed_cta_texts` with `{short_name}`/`{model_name}` filled in with the product's actual short_name/model name (e.g. "Shop the Fuji") -- never invent different wording (the gate rejects anything else and the run repairs/STOPs). No reviews carousel, no related products, no nav, no newsletter.
- Price always visible above the fold. `financing_line` must be exactly the allowed financing sentence given in the prompt's Financing rule -- verbatim, no monthly figure, APR, or lender name typed in by hand, whether or not a lender is configured.
- At least 3 of proof_bullets' claim_ids must be product-benefit claims -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Every bullet, spec, and trust item must map to a verified_claims id or facts_pack.specs row.
- Images: hero + 1–2 detail shots from the asset library, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field.
- Voice: short declarative sentences. No superlatives without a source.

## From the ad
Angle → hero promise and angle section. Features shown → which proof bullets to pick.

## From facts_pack
Product, specs, price, financing, warranty, shipping, reviews summary, assets.

## Design-skills pack (optional)
This cartridge is the skill's type-A classic landing page (one offer, one audience, one action). Optional `tagline.lines` (two or more) renders after the proof bullets through the `tagline-reveal` block. Soft-warned when omitted. The skill's raw visual tokens do not apply; see `harness/design_skills/`.
