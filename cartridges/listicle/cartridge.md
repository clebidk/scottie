# Cartridge: listicle  (v0.2.0)

Purpose: a numbered "N reasons" pre-sell built by **adapting a proven listicle**, not by inventing a long advertorial from scratch. Prefer a high-performing winner from the tenant's listicle library (or the shipped live reference when that is the only exemplar): keep its component map (hero / proof / reason rhythm / mid-CTA / close), section density, and scannable voice; swap brand name, colors (via render tokens), images (asset_ids), and tone/voice to this tenant and this ad. Do not pad toward article length.
Audience temperature: warm-to-cold. The reader may already know the category; the ad's own hook decides which.
Opt-in only: `discover_cartridges` finds this cartridge, but `adv run`'s default random-3 selection never includes it -- it only runs when `--cartridges listicle` (or a comma list containing it) is passed explicitly, until Caleb approves it for the default rotation.

## Structure (in order)
1. Header block: "Advertisement" label, headline, dek, byline block (same include as article -- `{{ byline_html | safe }}`, not the base-template footer byline block).
2. Optional 3-stat proof row directly under the dek: live {{ tenant.reviews.platform_name }} rating and count, warranty term, free shipping -- only these, only if verified. Skip it rather than force a weak stat. If the winner used a short trust line and you lack a verified equivalent, omit the proof row; never invent a customer-count or star figure.
3. 5-7 numbered items, in order (the page.json field is called `reasons`, not `items` -- see schema.json). Each item: number, an H2 title (<=10 words, no numeral -- the renderer draws the number), a short body (one idea, a few sentences -- match the winner's density, not article body length), one image slot (asset_id), and claim_ids for any fact stated in that item. Proof lives inside each item -- never a separate stacked proof section after the items.
4. One CTA, shown twice: right after item 3, and again in the closing block. Same text and URL both times (page.cta_text / page.cta_url) -- never vary it.
5. Closing block: optional short headline, 1 paragraph naming {{ tenant.name }}, the fixed warranty sentence, and the allowed financing sentence given in the prompt's Financing rule. Disclosure and sources follow, same as every other cartridge.

## Rules
- **No page-level word minimum.** Do not pad to hit a count. Length follows the winner (or library entry) you are adapting -- typically a few hundred words of body, not 600+. There is no generation floor and no "N-M words" gate for this cartridge. A library entry may document its own length band; when one does, match that entry's density, not a cartridge-wide floor.
- Items: N between 5 and 7. Every item's `number` field matches its 1-indexed position (1, 2, 3, ...), and the headline's own "N Reasons" states that same N.
- Headline: "N Reasons ..." formula, 8 to 14 words (phrased "to", not "8-14", so it cannot match a page-level "N-M words" gate pattern). Never contains a price. Name the audience when the ad names one (e.g. "N Reasons Busy Parents Are Switching to {{ tenant.name }}"); otherwise name the product category, never the company by name in the headline.
- Exactly one CTA text (rendered twice, same text/url both times). No sticky bar. No countdowns, no discount language. CTA text must be one of the allowed options in schema.json's `allowed_cta_texts`.
- Proof inside every reason: each of the 5-7 reasons must carry at least one claim_id, or an attributed customer statement (attributed_to_customer: true, phrased as the customer's own words) -- a reason with neither is flagged in review (see rule 3 above: "Proof lives inside each item").
- No claims outside verified_claims. Health statements cite the study and its population; never promise an outcome for {{ tenant.name }} hardware. A winner's unsourced trust line or product claim is a *pattern* to remake with verified facts -- never paste its numbers unless the same fact is in this page's facts_pack.verified_claims.
- The proof row (if present) and every item's claim_ids may only cite facts_pack.verified_claims -- never invent a stat. Omit the proof row entirely if there's nothing verified to put in it (e.g. facts_pack.reviews_summary is null).
- Images: one per item from the asset library, lifestyle or product over stock/illustration, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field. No before/after, no clinical settings. Prefer assets that play the same *role* as the winner's slots (hero/item/lifestyle), not a random pick.
- Closing block: the warranty line must be exactly the fixed warranty sentence (see the global voice block); the financing line must be exactly the allowed financing sentence given in the prompt's Financing rule, whether or not a lender is configured. Neither may appear anywhere else on the page.
- Voice: plain, specific, second person or first person. Short sentences. One idea per reason. No exclamation marks. No "game-changer", "unlock", "elevate", "journey". Match the winner's punchiness; do not lecture or expand into advertorial depth.

## From the ad
Hook and angle -> headline and dek. The ad's own list of features/objections/benefits -> the item set, one reason per item, in the order that reads best (not necessarily the ad's own order) -- still mapped onto the winner's component rhythm (short heading + short body + image), not a new layout. Speaker's story, if the ad is first person, folded into an item's body attributed to "a customer" (or facts_pack.speaker_name), never told in the author's own first person -- same rule as every other cartridge.

## From facts_pack
Specs, price, financing, warranty, verified studies, reviews summary, the asset library.

## From exemplars / winner library
Exemplars are a secondary voice reference. **Winner skeletons** in `cartridges/listicle/skeletons/` are the primary adaptation source for one-shot runs: keep structure, section order, item density, and CTA placement pattern; rewrite copy into this brand's voice and this ad's angle; swap every image to an asset_id from facts_pack; replace every claim with verified_claims only. Never treat an exemplar or skeleton as a claims source.

Pass `--skeleton <id>` to pin a page map (e.g. `hormozi-value-stack`, `classic-n-reasons`) and `--headline <id>` to pin a pre-sell title swipe (e.g. `everyones-switching`, `most-dont-work`). When omitted, the harness picks both from ad angle tags (defaults: `classic-n-reasons` + `everyones-switching`). Peak fill hints live on each file under `peak_saunas`. See `cartridges/listicle/skeletons/README.md` and `.../headlines/`.

Library groups: 2 Hormozi page maps, 1 native article+comments, 1 simplified PDP (`simplified-pdp` → product-page), 6 classic listicles, plus **17 headline swipe templates**.
