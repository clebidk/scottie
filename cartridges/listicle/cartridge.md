# Cartridge: listicle  (v0.2.0)

Purpose: a numbered pre-sell lander in one of five styles -- the DTC listicle shape (headline, hero, CTA, numbered items, proof, FAQ, close), written with an evidence-labelled, honest voice rather than an urgency-driven one.
Audience temperature: warm-to-cold. The reader may already know the category; the ad's own hook decides which.
Opt-in only: `discover_cartridges` finds this cartridge, but `adv run`'s default random-3 selection never includes it -- it only runs when `--cartridges listicle` (or a comma list containing it) is passed explicitly, until it is approved for the default rotation.

## Style
Every run writes in exactly ONE of five styles, named in this run's hard constraints and echoed in page.json's `style` field:

| style | headline formula | what one item is |
| --- | --- | --- |
| reasons | N Reasons \<audience\> Are Choosing \<category\> | one reason to choose the category, stated as the benefit it buys the reader |
| mistakes | N Mistakes People Make Buying \<category\> | one mistake a buyer makes, named as the mistake itself |
| questions | N Questions to Ask Before You Buy \<category\> | one question to ask a seller, phrased as a question |
| myths | N \<category\> Myths, and What the Evidence Says | one myth, stated as the myth, with the body answering it from the evidence |
| tested | We Tested \<category\> for N Weeks. Here Is What Held Up | one thing the test looked at, stated as what held up (or did not) |

For the first four styles the headline's N is the number of entries in `reasons`. For `tested`, N is the number of weeks; the item count is still 5-7. Item headings never carry the numeral -- the renderer draws the number.

## Structure (in order)
1. Header: the "Advertisement" label, H1 headline, one-line dek, hero image (`hero.asset_id`), the primary CTA button, a trust line under it, and the byline.
2. 5-7 numbered items (`reasons`). Each: number, an H2 heading (<=10 words, no numeral), a 60-150 word body, one image, and a closing `proof` line. A micro-CTA with the same `cta_text` renders after items 2 and 4.
3. A pull-quote band after item 3, when this run's facts pack carries a customer quote.
4. "Who this is for / who it is not for" (`audience_fit`): two lists of 2-4 one-line entries.
5. The model picker: up to three of the tenant's own active models, each with a one-line fit, its verified price, and a CTA to that model's page.
6. FAQ (`faq.questions`): 5-7 questions with 2-4 sentence answers.
7. Closing block: an optional short headline, a 3-bullet `recap`, the CTA, the fixed warranty sentence, the allowed financing sentence, and (only when this run's facts pack verifies it) an HSA/FSA line.
8. Disclosure and Sources, same as every other cartridge, plus a sticky bottom CTA bar.

Sections 3, 5, the header's trust line, the HSA/FSA line and the sticky bar's rating line are built by the RENDERER from facts_pack -- you never write them, and a page.json that carries `trust_line`, `pull_quote`, `model_picker`, `models`, `hsa_line` or `proof_row` fails the gate. They are omitted entirely when the facts pack does not verify them; that is the design, not a gap to fill in prose.

## Rules
- 900-1,400 words. Same accounting as every other cartridge (see `adv/cli.py`'s `count_words`): urls, asset ids and claim ids are never part of the count.
- Items: N between 5 and 7. Every item's `number` matches its 1-indexed position.
- Headline: this style's formula, 8-14 words, with N written as a numeral (5, not "five"). For every style except `tested`, N is the number of entries you actually put in `reasons` -- count them before you answer, and if a revision adds or drops an item, change the headline's number in the same edit. Never contains a price. Name the audience when the ad names one; otherwise name the product category, never the company by name in the headline.
- One CTA text and one `cta_url` for the whole page. The renderer draws that one CTA in five places (header, after item 2, after item 4, closing block, sticky bar) with the same text and url every time -- that is one offer repeated, not five offers. Never write a second `cta_url` anywhere in page.json. CTA text must be one of the allowed options in schema.json's `allowed_cta_texts`.
- Proof inside every item: each item's `proof` line either carries a claim_id or is an attributed customer statement (`attributed_to_customer: true`, phrased as the customer's own words, and the sentence itself must read as attributed -- "one customer told us ...").
- No claims outside verified_claims. Health statements cite the study and its population; never promise an outcome for {{ tenant.name }} hardware.
- FAQ: every answer that states a number, a price, a spec, or uses one of the trigger words carries claim_ids.
- `audience_fit.not_for_you` names real, checkable limits (space, power, household size, budget). Never a fake drawback, never a humblebrag.
- No urgency, ever: no countdown, no "limited time", "act now", "last chance", "while supplies last", "selling fast", or any other pressure phrase; no discount or sale language; no guarantee beyond the fixed warranty sentence.
- Every item carries its own `image.asset_id`, and `hero.asset_id` is one more -- every id on the page distinct, none left out.
- Any line stating a number, a price, a measurement, a spec or one of the trigger words carries its own claim_ids: `audience_fit` lines and closing `recap` bullets included, not only item bodies. A line you cannot cite is rewritten without the number, never shipped uncited.
- Images: `hero.asset_id` plus one per item, from the asset library, lifestyle or installation over stock/illustration, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field. Every slot on the page is a distinct asset. No before/after, no clinical settings.
- Closing block: exactly 3 recap bullets; the warranty line must be exactly the fixed warranty sentence (see the global voice block); the financing line must be exactly the allowed financing sentence given in the prompt's Financing rule, whether or not a lender is configured. Neither may appear anywhere else on the page.
- Voice: plain, specific, second person or first person. No exclamation marks. No "game-changer", "unlock", "elevate", "journey".

## From the ad
Hook and angle -> headline and dek. The ad's own list of features/objections/benefits -> the item set, one per item, in the order that reads best (not necessarily the ad's own order). Speaker's story, if the ad is first person, folded into an item's body or its proof line attributed to "a customer" (or facts_pack.speaker_name), never told in the author's own first person -- same rule as every other cartridge.

## From facts_pack
Specs, price, financing, warranty, verified studies, reviews summary, the asset library. The renderer additionally reads `reviews_summary`, `review_quotes` and `model_options` for the sections listed above.
