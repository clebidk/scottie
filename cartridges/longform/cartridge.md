# Cartridge: longform  (v0.1.0)

Purpose: a classic single-goal landing page for high-intent traffic. Hero → problem → how it works → specs and proof → social proof → FAQ → sticky CTA.
Audience temperature: warm to hot.

## Structure (in order)
1. Header: "Advertisement" label, Peak logo, sticky CTA bar (appears after the hero scrolls out).
2. Hero: headline from the ad angle, subhead, hero image, financing line if the ad used price. CTA is `cta_text`/`cta_url` (top-level, see Rules) -- exactly one of schema.json's `allowed_cta_texts`, e.g. "See pricing" or "Shop the Peak Fuji 2-Person Infrared Sauna"; do not add a separate cta object here.
3. Problem: H2 + 2–3 paragraphs on the reader's problem as the ad frames it. Objections from the ad become the problem statements.
4. How it works: H2 + 3 steps or 3 feature blocks, each with an image slot. Technical facts from facts_pack.specs only. At least 3 steps combined must carry a product-benefit claim_id (see Rules).
5. Specs and proof: table (6–10 rows) + 2–3 verified claims with citations.
6. Social proof: review summary (count, rating) from verified_claims; up to 3 short quotes only if they exist in facts_pack.reviews_summary verbatim. Never invent quotes.
7. FAQ: 5–7 questions. Questions come from ad objections and common buyer questions; answers from facts_pack. Rendered with FAQPage JSON-LD.
8. Final CTA block: headline, financing line, warranty line. Same `cta_text`/`cta_url` as the hero -- the renderer places it, do not repeat it here.
9. Footer: byline block (author, verifier, dates), disclosure paragraph, sources.

## Rules
- 800–1,400 words. Return top-level `cta_text` and `cta_url` exactly once -- the renderer reuses them verbatim in the hero, sticky bar, and final block. `cta_text` must be exactly one of schema.json's `allowed_cta_texts` with `{short_name}` filled in with the product's actual short_name -- never invent different wording (the gate rejects anything else and the run repairs/STOPs).
- At least 3 of how_it_works.steps' claim_ids must be product-benefit claims -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Every spec, number, and quote maps to facts_pack. Health statements cite studies.
- Images: 4–6 from the asset library, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field.
- Voice: confident, specific, second person. No hype words, no fake urgency.

## From the ad
Angle → hero. Objections → problem and FAQ. Features shown → how-it-works blocks.

## From facts_pack
Everything: specs, price, financing, warranty, shipping, verified claims, reviews summary, assets.
