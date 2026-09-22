# Cartridge: product-page  (v0.3.0)

Purpose: a product page whose promise band and FAQ answer the ad the visitor clicked. It looks like the brand's own product detail page (gallery, buy panel, specs), so the click lands where a buyer expects, but the words above the specs are about the ad's angle. For retargeting and Shopping traffic that already knows the product.
Audience temperature: warm.

## Structure (in order)
The renderer lays these fields out in the page's look (cycle 62: `pdp`, the default, is the live product page's bones only -- gallery and buy panel, one promise band, what's included, specs, five FAQs, one closing CTA; `classic` is the older stripped page). The gallery, price, rating, spec table, lead-time line, HSA/FSA line and the fixed financing/warranty sentences in the buy panel are built by the renderer from facts_pack -- never write them.
1. Header: the tenant logo, no nav.
2. Buy panel: the product's short model name, then `hero.promise` -- one line of 4 to 10 words, sentence case, that says the ad's main point about the product in plain words (an uncited number here fails the gate, see hero.claim_ids). Price and the financing line (the exact allowed financing sentence given in the prompt's Financing rule -- see Rules). Hero image from the asset library. CTA is `cta_text`/`cta_url` (top-level, see Rules) -- exactly one of schema.json's `allowed_cta_texts`, with {short_name} filled in with the product's short model name.
3. Promise band (`angle_section`): one heading (at most 10 words) and exactly 2 short paragraphs (at most 45 words each) that carry the ad's angle -- what the ad said, then what this product does about it. Optional one image.
4. What's included (`included`): 3-6 items, each a plain noun phrase of at most 8 words ("Full-body red light panel"), each one verified claim with a claim_id.
5. Specs: built by the renderer from facts_pack.specs.
6. FAQ (`faq.questions`): exactly 5 questions a visitor who clicked this ad asks before buying, each at most 12 words, each answered in 1-2 sentences (at most 35 words, answer first); any answer that states a number, a price, a spec or a trigger word carries claim_ids.
7. Closing CTA -- reuses the same `cta_text`/`cta_url`; the renderer places it, do not repeat it here.
8. Footer: disclosure paragraph. No byline -- this is intentional (a product page has no author, unlike article/longform/listicle); the disclosure paragraph still renders.

## Rules
- One CTA phrase per page: `cta_text` is a single phrase, chosen once, that repeats verbatim in the buy panel, the closing CTA and the phone bar -- never two different phrases anywhere on the page, and never a second, differently-worded CTA link or button.
- 200–360 words. Return top-level `cta_text` and `cta_url` exactly once -- the renderer reuses them verbatim. `cta_text` must be exactly one of schema.json's `allowed_cta_texts` with `{short_name}`/`{model_name}` filled in with the product's short model name (e.g. "Shop the Fuji") -- never invent different wording (the gate rejects anything else and the run repairs/STOPs). No reviews carousel, no related-products carousel, no nav, no newsletter, no urgency (no countdown, no "limited time", no discount push), no invented testimonials.
- Price always visible above the fold. `financing_line` must be exactly the allowed financing sentence given in the prompt's Financing rule -- verbatim, no monthly figure, APR, or lender name typed in by hand, whether or not a lender is configured.
- At least 3 of the included items' claim_ids must be product-benefit claims -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, the cabin wood, the speakers) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Every included item and every factual FAQ answer maps to a verified_claims id.
- Power and outlet: say only what the product's own electrical claim says (dedicated or standard outlet, volts, amps). Never say an electrician or a dedicated circuit is or is not needed unless that claim says so.
- Images: the hero, and optionally one promise-band image (`angle_section.image`), from the asset library, referenced by asset_id only; optional `gallery_order` lists up to 5 more asset ids (plain strings) for the product gallery -- the renderer derives alt text, never write your own "alt" field.
- Voice: the brand's own product pages -- short, concrete, product first. Short declarative sentences. Sentence case in every heading, question and item. No superlatives without a source. No lists of three joined by "and" for rhythm, no second-person scenario openers, no stock marketing verbs -- name the part, the number or the step instead.

## From the ad
Angle and main claim → hero promise and promise band. Objections raised → FAQ questions. Features shown → which included items to list first.

## From facts_pack
Product, specs, price, financing, warranty, shipping, reviews summary, assets.
