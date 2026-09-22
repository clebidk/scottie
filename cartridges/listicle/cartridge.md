# Cartridge: listicle  (v0.2.0)

Purpose: a numbered pre-sell lander in one of five styles -- the DTC listicle shape (headline, hero, CTA, numbered items, proof, FAQ, close), written with an evidence-labelled, honest voice rather than an urgency-driven one.
Audience temperature: warm-to-cold. The reader may already know the category; the ad's own hook decides which.
Opt-in only: `discover_cartridges` finds this cartridge, but `adv run`'s default random-3 selection never includes it -- it only runs when `--cartridges listicle` (or a comma list containing it) is passed explicitly, until it is approved for the default rotation.

## Style
Every run writes in exactly ONE of five styles, named in this run's hard constraints and echoed in page.json's `style` field:

| style | headline formula | what one item is |
| --- | --- | --- |
| reasons | N Reasons \<audience\> Are Choosing \<category\> | one reason to choose the category, stated as the benefit it buys the reader |
| mistakes | N Mistakes \<audience\> Make When Buying \<category\> | one mistake a buyer makes, named as the mistake itself |
| questions | N Questions \<audience\> Should Ask Before Buying \<category\> | one question to ask a seller, phrased as a question |
| myths | N \<category\> Myths \<audience\> Still Hear, and What the Evidence Says | one myth, stated as the myth, with the body answering it from the evidence |
| tested | We Checked N \<category\> Claims \<audience\> Keep Hearing. Here Is What Held Up | one claim people hear about the category, stated as the claim itself, with the body saying what the verified facts support or do not |

The headline's N is the number of entries in `reasons`, in every style (cycle 49: `tested` used to lead with a number of weeks -- see the note below). Every formula names an `<audience>` (2 to 5 words, by situation or goal, e.g. "apartment dwellers" -- never a bare "people" or "buyers", never a real-estate term, never the brand or a model name) so two ads never land on the same headline; `<category>` never contains the brand or a model name. Item headings never carry the numeral -- the renderer draws the number.

`tested` reports a claims check -- verified facts and published specifications checked against claims people repeat about the category -- never a physical test, trial, or usage period; nobody ran one. `listicle:tested_no_fake_test` fails the headline, dek, any item heading/body, `audience_fit`, an FAQ answer, or the closing recap in ANY style (not only `tested`) if it asserts first-person testing or usage: "we tested", "our test", "we used", "we ran", "weeks of use", "weeks of testing", "session by session", "in our testing", "hands-on", "we measured".

## Look
The style fixes the COPY. A second, independent dimension -- the LOOK -- fixes the LAYOUT: which template under `cartridges/listicle/looks/<look>/template.html` renders that copy. All five consume the same page.json and the same renderer-built sections; `cartridges/listicle/template.html` is a dispatcher that extends the resolved one.

| look | reference shape | what makes it look different |
| --- | --- | --- |
| editorial | publisher advertorial | one 680px column, serif display face and 19px serif body, an eyebrow carrying the tenant's `disclosure_label` (none when unset), author row under the H1, numbered subheads with inline full-width images, proof lines as pull-quote callouts, CTAs as links with one solid button mid-page and one at the end, plain Q/A FAQ, no sticky bar |
| cards | DTC listicle | two-column hero, alternating image/text cards on soft bands, big numerals, micro-CTAs after items 2 and 4, sticky bottom bar |
| pillars | image-led band lander | every item a full-width band with a 3:2 edge-to-edge image (a product cut-out instead gets a two-column band, image on a panel 40% / copy 60%, and a cut-out hero a split hero), an uppercase pillar label over the H2, narrow copy under the picture, proof lines as badges, hero headline reversed out over the image, `<details>` FAQ, horizontal model cards, sticky proof bar |
| scorecard | evidence buyer's guide | a trust row of bordered verified facts, every item a bordered "claim vs what the facts say" panel with a check line and an evidence label chip, a summary table before the FAQ, a closing CTA band carrying the warranty sentence, no sticky bar |
| lander | product lander | wide 1100px two-column hero with eyebrow, dual CTA (button + ghost link, same url) and trust line, items as a 2-up grid of panels with a 4:3 thumbnail and a numeral chip, check/cross audience-fit columns, three model cards, `<details>` FAQ, dark closing band, sticky bar on phones only |

The look is never written by the writer. It resolves in `harness/listicle.py`'s `resolve_look`: `harness run --look` / `harness rerender --look` when an operator gave one, else page.json's own recorded `look`, else `tenant.yaml`'s `cartridges.listicle.look_by_style`, else the default pairing -- reasons->cards, mistakes->editorial, questions->scorecard, myths->pillars, tested->lander. `tenant.yaml` may also pin the allowed set with `cartridges.listicle.looks: [...]`.

Each look scopes its own `<style>` block under `.adv-listicle.look-<name>` and owns a class prefix nothing else uses (`ed-`, `lst-`, `pil-`, `sc-`, `ld-`), so two looks can never collide; `tests/test_listicle_looks.py` asserts the prefixes stay disjoint and that no two looks render the same set of section classes. The `cards` look keeps its original `.lst-*` rules unprefixed, since that namespace was already its alone and re-writing 130 working selectors would be churn.

## Rhythm (cycle 55)
One vertical scale for every look, as `--pk-*` tokens on each look's root so no look names a spacing number twice:

| token | desktop | phone (<=600px) | what it spaces |
| --- | --- | --- | --- |
| `--pk-gap` | 40px | 32px | a section's top and bottom padding; FAQ 40/40 |
| `--pk-item-gap` | 32px | 32px | items inside a list (editorial items, scorecard panels, the lander grid, micro-CTA rows) |
| `--pk-hero-top` | 32px | 32px | the hero's top padding (its bottom is `--pk-gap`, so hero 32/40) |
| `--pk-close-gap` | 48px | 48px | the closing band, top and bottom |
| `--pk-panel-pad` / `--pk-panel-img` | 24px / 420px | 24px / 300px | the panel a product cut-out sits on, and the tallest the image may be |

Sections have no margin between them: adjacent bands touch and alternating grounds do the separating. Where two sections share one ground (editorial throughout; the scorecard and lander middles; two plain pillars sections in a row) only one side carries the gap, so the space between them is one `--pk-gap`, never two.

Image framing follows `harness/render.py`'s `image_fit`: a product cut-out (the white-border detector) is always contained, whole and centred, on a panel of the muted ground (`--ps-bg-muted`) -- never a full-bleed cover band and never behind an overlay. Only an image that fills its frame keeps a cover treatment (pillars' 3:2 band, capped at 480px; its overlay hero, capped at 560px).

A top-level section should stay under 700px at a 1280px viewport unless it carries 3+ images or 1,200+ characters.

## Structure (in order)
1. Header: the tenant's optional disclosure label (`disclosure_label` in tenant.yaml; nothing renders when it is unset), H1 headline, one-line dek, hero image (`hero.asset_id`), the primary CTA button, a trust line under it, and the byline.
2. 5-7 numbered items (`reasons`). Each: number, an H2 heading (<=10 words, no numeral), a 50-150 word body, one image, and a closing `proof` line. A micro-CTA with the same `cta_text` renders after items 2 and 4.
3. A pull-quote band after item 3, when this run's facts pack carries a customer quote.
4. "Who this is for / who it is not for" (`audience_fit`): two lists of 2-4 one-line entries.
5. The model picker: up to three of the tenant's own active models, each with a one-line fit, its verified price, and a CTA to that model's page.
6. FAQ (`faq.questions`): 5-7 questions with 2-4 sentence answers.
7. Closing block: an optional short headline, a 3-bullet `recap`, the CTA, the fixed warranty sentence, the allowed financing sentence, and (only when this run's facts pack verifies it) an HSA/FSA line.
8. Disclosure and Sources, same as every other cartridge, plus a sticky bottom CTA bar.

Every look renders all eight, in its own arrangement -- a look may move a section or change how it is drawn, never drop one. Where this list names a placement (a micro-CTA after items 2 and 4, a sticky bar), that is the `cards` look's own; see the Look table above for what each of the others does instead.

Sections 3, 5, the header's trust line, the HSA/FSA line and the sticky bar's rating line are built by the RENDERER from facts_pack -- you never write them, and a page.json that carries `trust_line`, `pull_quote`, `model_picker`, `models`, `hsa_line` or `proof_row` fails the gate. They are omitted entirely when the facts pack does not verify them; that is the design, not a gap to fill in prose.

## Rules
- 900-1,400 words. Same accounting as every other cartridge (see `adv/cli.py`'s `count_words`): urls, asset ids and claim ids are never part of the count.
- Items: N between 5 and 7. Every item's `number` matches its 1-indexed position.
- Headline: this style's formula, 8-14 words, with N written as a numeral (5, not "five"). N is the number of entries you actually put in `reasons`, in every style -- count them before you answer, and if a revision adds or drops an item, change the headline's number in the same edit. Never contains a price. `<audience>` names the people the ad speaks to (2 to 5 words, by situation or goal), never a bare "people"/"buyers"; `<category>` is the product category, never the company by name in the headline.
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
