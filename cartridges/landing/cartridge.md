# Cartridge: landing  (v0.1.0)

Purpose: a short, ad-first landing page for a high-price product, built mobile first. A visitor from the ad gets the three answers in the first two thumb-scrolls -- what it is, what it costs, what the ad's claim really means -- and one easy next step.
Audience temperature: warm to hot.

## Structure (in order)
1. First screen: eyebrow (`hero.eyebrow`, the ad's hook in at most 8 words), headline (`hero.headline`, 6 to 12 words) with one accent phrase (`hero.accent_phrase`, copied from the headline; a phrase not found there renders no accent), subhead (`hero.subhead`, the ad's promise in plain words, at most 30 words). The renderer adds the ad's own still (or the product cut-out; `hero.hero_image` is the fallback), the price from facts_pack with the fixed financing sentence, a small trust line (rating, free shipping, warranty term), the one CTA pill (`cta_text`/`cta_url`) and, under it, the tenant's secondary text link. Never write a price, rating, spec value, phone number or link yourself.
2. Checked claims (`checked_claims`): 4-6 items, each `{ad_said, fact, claim_ids}` -- what the ad said or implied (at most 18 words), and the verified fact behind it (at most 36 words) with at least one claim_id. Cover the buyer's logistics where facts exist: electrical requirement, footprint/dimensions, delivery and shipping, assembly, warranty. At least 3 items cite a product spec or benefit claim (not price, shipping, warranty terms or returns).
3. One photo in a real room (`room_photo.asset_id`): an asset of kind lifestyle or installation, never a studio render or a cut-out. Omit it when the run has none.
4. Spec strip: the renderer builds 4 cells from facts_pack.specs. Write nothing here.
5. Proof (`proof.quote`, optional): the rating is rendered from facts_pack; a quote only when it exists word for word in facts_pack.reviews_summary, with its author and claim_ids.
6. FAQ (`faq.questions`): 3-5 entries from the ad's objections and the buyer's logistics. Each question at most 12 words; each answer first, at most 35 words; claim_ids on any number, price, spec or trigger word. Rendered with FAQPage JSON-LD.
7. Closing band (`closing.headline`, at most 10 words). The renderer adds the same CTA pill and the financing and warranty sentences.
8. Sticky bar: price + the CTA, rendered after the first screen scrolls out.
9. Footer: byline, disclosure, sources.

## Rules
- 250-450 words. Short, concrete, product first. Second person where it reads naturally.
- One CTA: `cta_text` is exactly one of schema.json's `allowed_cta_texts` with `{short_name}`/`{model_name}` filled in with the model name alone (facts_pack.product.name), and `cta_url` is the one destination. Never add a second url, link, button, phone number or offer anywhere in page.json -- the gate fails it.
- Every number, spec and quote maps to facts_pack through claim_ids.
- Power and outlet: state only what the product's own electrical claim says.
- `ad_said` has no digit, currency sign, percent sign or discount code: paraphrase a price or promo claim without figures ("a fall sale with money off every model") or pick another ad claim.
- Never put an ad claim the facts contradict in the hero, eyebrow, subhead, FAQ or closing; quote it in `checked_claims[].ad_said` and correct it in `fact` (`ad_said` is the one place an ad's wrong claim may appear).
- Images by asset_id only; the renderer derives alt text.
- No hype words, no urgency, no countdowns.

## From the ad
Hook -> eyebrow. Angle -> headline. Promise -> subhead. Claims and objections -> checked claims and FAQ.

## From facts_pack
Price, financing, warranty, shipping, rating, specs, verified claims, reviews summary, assets.
