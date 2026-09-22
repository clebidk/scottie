# Cartridge: quiz  (v1.0.0)

Purpose: a self-assessment lander. The reader answers 5-7 short questions and the page recommends ONE of {{ tenant.name }}'s own active models, with its verified price and a short "why this matches you" list built from the reader's own answers. Built on the in-category quiz pattern (question screens, short educational interstitials, a result that names a model and a price) without its weak spots: no mid-quiz popup, no email gate, no discount.
Audience temperature: cold to warm. The reader clicked an ad and has not chosen a model; the page's job is to help them choose, not to push.
Opt-in only: `discover_cartridges` finds this cartridge, but the default random-3 selection never includes it -- it runs only when `--cartridges quiz` (or a comma list containing it) is passed explicitly.

## What the rubric decides (tenant data, not your copy)
The tenant's `quiz/rubric.yaml` fixes the questions (their ids and order), every option label, how each option scores each model, where the interstitials go, and the tiebreak. It is in facts_pack.quiz.rubric. You never change a score, a label, or the order -- you only write the words around them.

## Structure (in order)
1. Header: the tenant disclosure label as an eyebrow (only when the tenant sets `disclosure_label`), H1, one-line dek, the start link ("Start the quiz", renderer text, a same-page anchor), the compact byline, and the hero image (`hero.asset_id`, one of the featured model's assets).
2. The quiz: a thin progress bar, then one question at a time -- your rephrased prompt over the rubric's option buttons -- with a back button. After the questions the rubric names, one short interstitial line of yours ("Good to know"). Without a script, every question and interstitial shows as one plain list.
3. The result: one card per active model, all hidden but the winner. Each card shows the model's display name, its verified price, its capacity, its product image, a "why this matches you" list (the reader's own chosen labels that scored the model) and the CTA to the model's page. Without a script, the card for the model the ad featured shows, with a note that says so.
4. Under the result: the verified trust line, the fixed financing sentence, the HSA/FSA line (only when a verified claim exists), and the fixed warranty sentence.
5. FAQ (`faq.questions`): 3-5 questions a buyer asks before choosing a model.
6. Byline, disclosure and Sources, same as every other cartridge.

Sections 3 and 4 are built by the RENDERER from verified data -- you never write a result card, a price, a capacity line, a "why" list, a trust line, or a financing, HSA or warranty line. A page.json that carries `models`, `cards`, `result`, `scores`, `tiebreak`, `why`, `trust_line`, `financing_line`, `hsa_line`, `warranty_line` or `price` fails the gate.

## Rules
- 400-700 words. Same accounting as every other cartridge: urls, asset ids and claim ids are never part of the count.
- Headline: exactly "Which <category> Is Right for <audience>? Take the 60-Second Quiz". `<category>` is the product category (e.g. "home infrared sauna"), never the company or a model name. `<audience>` names the people the ad speaks to by their situation or goal, 2-5 words, never a bare "people" or "buyers".
- Dek: one sentence on what the quiz does for the reader -- no number unless it is cited.
- Questions: exactly one entry per rubric question, in rubric order, with the rubric's `id`, a `prompt` you rephrase (one short question ending in "?", in the voice of the page), and `options`: the rubric's labels copied verbatim, same count, same order.
- Interstitials: exactly one entry per rubric interstitial, in order, `{"after": <question id>, "line": ..., "claim_ids": [...]}`. One to three short sentences that teach one verified fact on the rubric's topic. A line with a digit, a price, a measurement or a trigger word carries claim_ids; otherwise it has no digit at all. Never a health promise.
- FAQ: 3-5 questions; every answer that states a number, a price, a spec or a trigger word carries claim_ids.
- One CTA text and one `cta_url` for the page: `cta_url` is the featured model's own url (facts_pack.product.url), and the CTA text is the allowed option in schema.json's `allowed_cta_texts`. The renderer puts it on the featured model's card; every other card gets the same allowed wording with its own model's name.
- No claims outside verified_claims. No urgency, no countdown, no discount or sale language, no email request, no competitor name, no retired name.
- Images: `hero.asset_id` from the asset library, referenced by id only -- the renderer derives alt text.
- Voice: plain, specific, second person. No exclamation marks. No "game-changer", "unlock", "elevate", "journey".

## From the ad
Hook and angle -> the H1's audience slot and the dek. The ad's objections -> FAQ questions where they fit.

## From facts_pack
The rubric (facts_pack.quiz.rubric), verified claims, the asset library, the product (the featured model and its url). The renderer additionally reads facts_pack.quiz.models (one entry per active model with a verified price) and reviews_summary for the sections listed above.
