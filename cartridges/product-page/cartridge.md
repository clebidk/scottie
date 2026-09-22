# Cartridge: product-page  (v0.2.0)

Purpose: a product page whose headline, proof tiles and FAQ answer the ad the visitor clicked. It looks like the brand's own product detail page (gallery, buy panel, specs), so the click lands where a buyer expects, but every word above the specs is about the ad's angle. For retargeting and Shopping traffic that already knows the product.
Audience temperature: warm.

## Structure (in order)
The renderer lays these fields out in the page's look (cycle 54: `pdp`, the default, is a product-detail-page layout -- gallery and sticky buy panel, ad-proof band, benefits, specs table, model compare, reviews summary, FAQ, closing buy band; `classic` is the older stripped page). The gallery, price, rating, spec table, model compare, lead-time line, HSA/FSA line and the fixed financing/warranty sentences in the buy panel are built by the renderer from facts_pack -- never write them.
1. Header: the tenant logo, no nav.
2. Hero: product short_name, one-line promise taken from the ad angle (`hero.promise` -- the page's headline: the ad's main claim, rewritten; an uncited number here fails the gate, see hero.claim_ids), price, financing line (the exact allowed financing sentence given in the prompt's Financing rule -- see Rules). Hero image from asset library. CTA is `cta_text`/`cta_url` (top-level, see Rules) -- exactly one of schema.json's `allowed_cta_texts`, with {short_name} filled in with the product's actual short_name.
3. Ad-proof tiles (`ad_proof`): 3-4 tiles that answer the ad's angle point by point, each one verified claim with a claim_id -- the band a visitor sees right under the buy panel, so it must answer what the ad said (an ad about hidden costs gets tiles on the published price, free shipping, installation, the warranty; an ad about features gets the features it showed).
4. Three proof bullets (the "Why this model" benefit blocks, each with its own optional `image`): each one verified claim with a short label and a one-line explanation. Choose the three that match the ad's angle, and make at least 3 of them product-benefit claims (see Rules). Value-equation checklist (order matters): bullet 1 states the outcome the buyer wants; bullet 2 is proof that outcome is likely (a verified claim -- a spec, a review stat, a certification); bullet 3 is time to first benefit or install effort (how soon/how easily the buyer gets there). Use a different verified claim_id for each bullet -- never the price, warranty, or shipping/returns claim_id.
5. Angle section: 1 H2 + 2–4 short paragraphs or an image-and-text pair that expands what the ad said.
6. Specs table: 6–10 rows from facts_pack.specs.
7. Trust strip: warranty, shipping/delivery, returns, review count and rating (from verified_claims only).
8. FAQ (`faq.questions`): 5-7 questions a visitor who clicked this ad asks before buying, each answered in 1-3 sentences; any answer that states a number, a price, a spec or a trigger word carries claim_ids.
9. Repeat CTA -- reuses the same `cta_text`/`cta_url` as the hero; the renderer places it, do not repeat it here.
10. Footer: disclosure paragraph. No byline -- this is intentional (a product page has no author, unlike article/longform/listicle); the disclosure paragraph still renders.

## Rules
- One CTA phrase per page: `cta_text` is a single phrase, chosen once, that may repeat verbatim in the hero and the repeat CTA -- never two different phrases anywhere on the page, and never a second, differently-worded CTA link or button.
- 400–800 words. Return top-level `cta_text` and `cta_url` exactly once -- the renderer reuses them verbatim in the hero and the repeat CTA. `cta_text` must be exactly one of schema.json's `allowed_cta_texts` with `{short_name}`/`{model_name}` filled in with the product's actual short_name/model name (e.g. "Shop the Fuji") -- never invent different wording (the gate rejects anything else and the run repairs/STOPs). No reviews carousel, no related-products carousel, no nav, no newsletter, no urgency (no countdown, no "limited time", no discount push), no invented testimonials.
- Price always visible above the fold. `financing_line` must be exactly the allowed financing sentence given in the prompt's Financing rule -- verbatim, no monthly figure, APR, or lender name typed in by hand, whether or not a lender is configured.
- At least 3 of proof_bullets' claim_ids must be product-benefit claims -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Every proof tile, bullet, spec, and trust item must map to a verified_claims id or facts_pack.specs row. A proof tile with no claim_id fails the gate.
- Images: hero + one image per proof bullet (or 1–2 detail shots) from the asset library, referenced by asset_id only; optional `gallery_order` lists up to 5 more asset ids (plain strings) for the product gallery -- the renderer derives alt text, never write your own "alt" field.
- Voice: short declarative sentences. No superlatives without a source.

## From the ad
Angle and main claim → hero promise, ad-proof tiles and angle section. Objections raised → FAQ questions. Features shown → which proof bullets to pick.

## From facts_pack
Product, specs, price, financing, warranty, shipping, reviews summary, assets.

## Design-skills pack (optional)
This cartridge is the skill's type-A classic landing page (one offer, one audience, one action). Optional `tagline.lines` (two or more) renders after the proof bullets through the `tagline-reveal` block. Soft-warned when omitted. The skill's raw visual tokens do not apply; see `harness/design_skills/`.
