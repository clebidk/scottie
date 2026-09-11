# Cartridge: listicle  (v0.1.0)

Purpose: a numbered "N reasons" pre-sell -- the format the tenant has already shipped live as a standalone landing page. Fast to scan, image-led, one reason per screen.
Audience temperature: warm-to-cold. The reader may already know the category; the ad's own hook decides which.
Opt-in only: `discover_cartridges` finds this cartridge, but `adv run`'s default random-3 selection never includes it -- it only runs when `--cartridges listicle` (or a comma list containing it) is passed explicitly, until it is approved for the default rotation.

## Structure (in order)
1. Header block: "Advertisement" label, headline, dek, byline block (same include as article -- `{{ byline_html | safe }}`, not the base-template footer byline block).
2. Optional 3-stat proof row directly under the dek: live {{ tenant.reviews.platform_name }} rating and count, warranty term, free shipping -- only these, only if verified. Skip it rather than force a weak stat.
3. 5-7 numbered items, in order (the page.json field is called `reasons`, not `items` -- see schema.json). Each item: number, an H2 title (<=10 words, no numeral -- the renderer draws the number), a 40-90 word body, one image slot (asset_id), and claim_ids for any fact stated in that item. Proof lives inside each item -- never a separate stacked proof section after the items.
4. One CTA, shown twice: right after item 3, and again in the closing block. Same text and URL both times (page.cta_text / page.cta_url) -- never vary it.
5. Closing block: optional short headline, 1 paragraph naming {{ tenant.name }}, the fixed warranty sentence, and the allowed financing sentence given in the prompt's Financing rule. Disclosure and sources follow, same as every other cartridge.

## Rules
- 600-1,100 words. Word count covers proof_row and item bodies; headings, urls, asset ids, and claim ids are not part of the count (same accounting as every other cartridge -- see `adv/cli.py`'s `count_words`).
- Items: N between 5 and 7. Every item's `number` field matches its 1-indexed position (1, 2, 3, ...), and the headline's own "N Reasons" states that same N.
- Headline: "N Reasons ..." formula, 8-14 words. Never contains a price. Name the audience when the ad names one (e.g. "N Reasons Busy Parents Are Switching to {{ tenant.name }}"); otherwise name the product category, never the company by name in the headline.
- Exactly one CTA text (rendered twice, same text/url both times). No sticky bar. No countdowns, no discount language. CTA text must be one of the allowed options in schema.json's `allowed_cta_texts`.
- Proof inside every reason: each of the 5-7 reasons must carry at least one claim_id, or an attributed customer statement (attributed_to_customer: true, phrased as the customer's own words) -- a reason with neither is flagged in review (see rule 3 above: "Proof lives inside each item").
- No claims outside verified_claims. Health statements cite the study and its population; never promise an outcome for {{ tenant.name }} hardware.
- The proof row (if present) and every item's claim_ids may only cite facts_pack.verified_claims -- never invent a stat. Omit the proof row entirely if there's nothing verified to put in it (e.g. facts_pack.reviews_summary is null).
- Images: one per item from the asset library, lifestyle or product over stock/illustration, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field. No before/after, no clinical settings.
- Closing block: the warranty line must be exactly the fixed warranty sentence (see the global voice block); the financing line must be exactly the allowed financing sentence given in the prompt's Financing rule, whether or not a lender is configured. Neither may appear anywhere else on the page.
- Voice: plain, specific, second person or first person. No exclamation marks. No "game-changer", "unlock", "elevate", "journey".

## From the ad
Hook and angle -> headline and dek. The ad's own list of features/objections/benefits -> the item set, one reason per item, in the order that reads best (not necessarily the ad's own order). Speaker's story, if the ad is first person, folded into an item's body attributed to "a customer" (or facts_pack.speaker_name), never told in the author's own first person -- same rule as every other cartridge.

## From facts_pack
Specs, price, financing, warranty, verified studies, reviews summary, the asset library.
