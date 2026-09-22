# Cartridge: comparison  (v1.0.0)

Purpose: a claims-safe comparison lander for a reader who is already weighing options. It compares on two axes, both from verified facts only: (A) the ad's own product against two more of {{ tenant.name }}'s active models, in a table the renderer builds cell by cell from verified claims; and (B) the category-level alternatives the ad names -- never a brand -- described in plain words.
Audience temperature: warm-to-hot. The comparison itself does the persuading; no urgency, no hard sell.
Opt-in only: it runs only when `--cartridges comparison` (or a comma list containing it) is passed explicitly.

## Axis
Every page picks ONE axis in page.json's `axis` field; it fixes the headline formula.

| axis | when | headline formula |
| --- | --- | --- |
| alternatives | the ad names or weighs another way to get the same thing (a studio, a membership, a gym or spa, another kind of sauna, a blanket) -- even when it also quotes a price or names a model | \<category\> vs \<alternative\>: What \<audience\> Should Compare |
| models | the ad is purely about choosing between, pricing, or sizing the tenant's own cabins, with no other way named | \<Model A\> vs \<Model B\> vs \<Model C\>: Which \<category\> Fits \<audience\> |

The model names are the three in facts_pack.comparison.models, exactly as written there and in column order (the featured model -- this run's product -- first). `<alternative>` is one of the alternatives this page covers, in its own words. `<category>` is the product category, never {{ tenant.name }} or a model name. `<audience>` names people by situation or goal, 2 to 5 words, never a bare "people" or "buyers". The hard constraints for this run quote both formulas with the real model names filled in.

## Structure (in order)
1. Header: the tenant's optional disclosure label as an eyebrow (`disclosure_label`; none when unset), the H1 headline, the `dek`, the verified trust line (renderer), the primary CTA, and the hero image of the featured model (`hero.asset_id`).
2. The model table (renderer): one column per model with its storefront image, name and link; rows capacity, footprint, indoor/outdoor, infrared wavelengths, red light therapy, controls/app, power, the 1-2 `extra_rows` you pick, price, and warranty. Every cell is a fragment of one verified claim with its claim id, or a dash when nothing verified states it; a row no model has a verified value for is dropped. Under it, your one `best_for` line per model.
3. "What the numbers mean": `numbers_mean.paragraphs`, exactly 3 short paragraphs reading the table for a buyer, each citing the cells' claim ids.
4. Alternatives: 2-3 cards, one per `alternatives` entry -- a one-sentence summary, 2-3 similarities, and 2-3 differences, each difference as "theirs" (the alternative, plain words) against "ours" (the tenant's side, claim-backed).
5. A lifestyle photo (`lifestyle.asset_id`).
6. "Who each model is for": `who_for`, one entry per model in column order.
7. The verified rating line band (renderer; omitted when this run fetched no rating).
8. FAQ: 5-7 questions.
9. Closing: an optional short `closing.headline`, exactly 3 `closing.recap` bullets, then (renderer) the CTA, the fixed warranty sentence, the fixed financing sentence, and an HSA/FSA line only when a verified claim states it.
10. Byline, disclosure and Sources (renderer), plus a sticky CTA bar on phones.

Sections 2's table, the trust line, the rating band, the warranty/financing/HSA lines and the sticky bar are built by the RENDERER from facts_pack -- you never write them. A page.json that carries `comparison_table`, `table`, `cells`, `models`, `trust_line`, `rating_line`, `warranty_line`, `financing_line`, `hsa_line`, `model_picker`, `sticky_cta`, `proof_row` or `proof_stats` fails the gate.

## Rules
- 800-1,200 words. Same accounting as every other cartridge: urls, asset ids and claim ids are never part of the count.
- No claims outside verified_claims. Every number, price, spec or trigger word in `dek`, `best_for`, `numbers_mean`, `ours`, `who_for`, FAQ answers and `recap` carries claim_ids -- read each model's cells in facts_pack.comparison.models for the ids. A line you cannot cite is rewritten without the number, never shipped uncited.
- Alternatives: 2-3 entries, each `id` from this cartridge's allowlist (schema.json's top-level `alternatives`). Nothing verified describes an alternative, so an alternative's `summary`, `similarities` and `theirs` lines contain NO digits, dollar amounts, percentages or trigger words -- not even the ad's own figures. Describe it fairly in plain words; never disparage it, never invent a weakness.
- Never name a competitor or a retired model -- by brand or by product name. The forbidden-word list at the top of this prompt is the list; it applies to every field.
- `best_for` and `who_for`: exactly one entry per model, in column order, each with that model's `id`.
- `extra_rows`: 1-2 keys from facts_pack.comparison.extra_row_options -- the rows this ad's angle makes worth a look.
- One CTA text and one `cta_url` for the whole page; CTA text is one of schema.json's `allowed_cta_texts`. The renderer draws it in the header, the closing block and the sticky bar -- one offer, repeated. Never write a second `cta_url`.
- Never write a warranty or financing sentence: the renderer draws the fixed ones.
- Images: `hero.asset_id` (the featured model) and `lifestyle.asset_id` (a lifestyle or installation photo), both from facts_pack.assets, distinct, and never one of the models' column images (facts_pack.comparison.models[].image.id) -- the renderer draws those three itself, so the page shows 3-5 images in all. Reference by asset_id only; the renderer derives alt text.
- No urgency, no discount language, no guarantee beyond the fixed warranty sentence. Voice: plain, specific, fair; no exclamation marks.

## From the ad
The ad's angle picks the axis and the extra rows. The alternatives the ad names or implies (a studio membership, another kind of sauna, a blanket) become the alternatives cards. The ad's objections become FAQ questions. The speaker's own figures (a monthly fee, a payback period) are never restated about an alternative.

## From facts_pack
`comparison.models` (each model's id, name, title, url, column image and every row's cell), `comparison.fixed_rows`, `comparison.extra_row_options`, specs, price, verified claims, the asset library. The renderer additionally reads `reviews_summary` for the trust line, the rating band and the sticky bar.
