# Cartridge: landing  (v0.2.0)

Purpose: a short, ad-first landing page that pre-sells a high-price product, built mobile first. A visitor who just clicked the ad sees the ad's promise paid off in the ad's own words, the proof that it will work for them, the deal, and one easy next step -- all in the first phone screen, then repeated down the page.
Audience temperature: warm to hot.

## The persuasion model (cycle 110)
Match the ad, then follow the Value Equation: dream outcome x perceived likelihood / (time delay x effort).
- **Dream outcome**: the ad's own promise, in the ad's own words (recovery, sleep, the evening ritual, the deal, the founder's note). The page amplifies it; it never grades it.
- **Likelihood** (proof): the store rating, units delivered, verbatim customer reviews, the warranty, U.S.-based support, the founder who answers for every claim, the ad's speaker quoted word for word.
- **Time**: ships in days, preheats from the phone, short sessions -- only what a verified claim states.
- **Effort**: clasp-together assembly, the outlet, no gym commute, app control.
- **Offer**: the price, the net sale price with code and the real deadline, the monthly figure, what is included.
Write to the reader's outcome first, then the spec that makes it true. Grade 5-7 reading level, short sentences.

## Structure (in order)
1. First screen: eyebrow (`hero.eyebrow`, who the page is for or the sale, at most 8 words), headline (`hero.headline`, 6 to 12 words: the ad's hook restated as closely as the verified facts allow -- same words, same number, same person) with one accent phrase (`hero.accent_phrase`, copied from the headline), subhead (`hero.subhead`, the outcome and the time frame, about 20 words, at most 30; a testimonial ad: the speaker's strongest line word for word, credited "In the ad, she says"). Figures appear here only as tokens the renderer fills from verified claims: `{offer_amount}`, `{offer_code}`, `{net_price}`, `{price}`, `{monthly}`, `{deadline_day}`, `{deadline_date}`, `{rating}`, `{review_count}` -- e.g. "{offer_amount} off the <model>, today's price ends {deadline_day}". The renderer adds the offer bar, the proof row (store rating, units delivered), the price line (net sale price or price, the monthly figure), the CTA pill (`cta_text`/`cta_url`) and the photo. Never write a price, rating, spec value, phone number or link yourself.
2. Reasons (`reasons`): 3-5 items `{headline, body, claim_ids, lever, proof_ref?, asset_role?}` -- headline at most 10 words, outcome first; body at most 45 words, one mechanism sentence and one spec; `lever` one of outcome, likelihood, time, effort, covering min(4, n) different levers; `proof_ref` the claim_id of one review quote, the store rating or the units line; `asset_role` the photo beside it (people, room, detail, exterior). At least 3 product spec or benefit claims across the reasons. On an offer-led page the renderer adds the sale as the first reason and you write 3-4.
3. What's included, model cards, the review wall, the trust block and the "Delivery & setup" row: all renderer-built from facts_pack. Write nothing here.
4. FAQ (`faq.questions`): 3-5 questions a buyer asks before spending this much -- will I use it, how long is a session or how fast does it heat (only with a verified claim), will it fit (dimensions), can I pay over time, is it HSA/FSA eligible (only when verified). At most one logistics question (delivery, assembly, power, warranty). Never a question about returns, refunds or a trial. Each question at most 12 words; each answer first, at most 35 words; claim_ids on any number, price, spec or trigger word. Rendered with FAQPage JSON-LD.
5. Closing band (`closing.headline`, at most 10 words: the outcome or the offer). The renderer adds the recap, the price, the deadline and the CTA pill.
6. Sticky bar: the net price (or price), the monthly figure and the CTA, after the first screen scrolls out.
7. Footer: byline, disclosure, sources.

The renderer draws that one CTA in up to six places down the page plus the sticky bar -- that is one offer repeated, not six offers.

## The proof look (only when the hard constraints say so)
The page may render in the `proof` look. Then the renderer puts the rating first, the quote (`proof.quote`) under the subhead and the shipping and warranty facts under the CTA, and the writer adds two blocks; for the other looks, omit both.
- `benefits`: 4-6 rows `{text, claim_ids}` shown with a check mark after the first screen. Each text is a plain noun phrase of at most 8 words. At least one claim_id per row is a product spec or benefit claim or a benefit allowlist id.
- `icon_row`: 3-4 cells `{icon_key, label, claim_ids}` before the closing band. `icon_key` is one of the fixed keys the hard constraints list, each key once; the label is at most 3 words and its claim_ids state it.
Both count toward the word range.

## Offer-led pages (only when the hard constraints say so)
When the ad is about a verified offer (a promo claim in facts_pack.offer), the renderer makes it the page's focus: the offer bar, the net sale price (or the price and the offer line) with the code, the claim's deadline ("Today's price ends Thu, Oct 15"), a note under the first CTA, the code in the sticky bar and the sale as the first reason. The headline states the saving with the figure tokens, or names the sale in words. Write 3-4 reasons about the product and nothing elsewhere about discounts, sales, codes or deadlines, including no FAQ question about them. The offer needs its code: never say it applies automatically. When the ad's offer is NOT verified, say nothing about discounts, sales, codes or deadlines anywhere -- never that there is none.

## Rules
- 250-450 words. Short, concrete, outcome first. Second person where it reads naturally.
- One CTA: `cta_text` is exactly one of the phrases the hard constraints list for this ad's angle (sale, financing, story, default), and `cta_url` is the one destination. Never add a second url, link, button, phone number or offer anywhere in page.json -- the gate fails it.
- Every number, spec and quote maps to facts_pack through claim_ids.
- Power and outlet: state only what the product's own electrical claim says.
- Never quote, paraphrase, grade or correct the ad's claims: no "the ad", "actually", "real price", "plainly", "not verified", "not lifetime", "no separate", "nothing to submit", "no form required", "what holds up". An ad claim the facts do not support is simply absent (the ad-claims gate reports it to the media team). The one way to name the ad is to credit the speaker's own words.
- No copy about returns, refunds, restocking or a risk-free trial.
- Images by asset_id only; the renderer derives alt text.
- No hype words, no countdowns, no invented scarcity. A verified deadline may be stated, through its token.

## From the ad
Hook -> headline. Who it is for -> eyebrow. Promise -> subhead. The speaker's best line -> the subhead or a reason. Objections -> reasons and FAQ.

## From facts_pack
Price, offer, financing, warranty, shipping, ratings, reviews, units, specs, verified claims, assets.
