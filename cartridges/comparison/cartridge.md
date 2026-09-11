# Cartridge: comparison  (v0.1.0 draft)

Purpose: a "X vs. Y" decision page for high-intent traffic comparing the run's product against an alternative -- the tenant's own other models (from facts_pack.comparison_targets, always available) or an approved claims/competitors entry. Seeded from the type-05 wireframe in the design notes: eyebrow -> literal H1 -> one value-prop subhead -> single CTA with risk-reversal microcopy -> proof -> the sourced comparison table -> deep dives -> honest alternative strengths -> FAQ -> verdict.
Audience temperature: hot. The reader is already comparing; the page's job is to make the comparison concrete, sourced, and fair.
Draft status: opt-in only, same as listicle -- it runs only when `--cartridges comparison` (or a comma list containing it) is passed explicitly. A run against a named competitor requires an approved claims/competitors entry (pending entries never load); the tenant's vocab bans on competitor names apply to this cartridge exactly as to every other.

## Structure (in order)
1. Header block: "Advertisement" label, eyebrow, headline, subhead, optional proof-stat row, one CTA, optional risk-reversal microcopy line, byline block (`{{ byline_html | safe }}`).
2. Comparison table (`comparison_table`): the sourced spec table. Columns: the spec dimension, the run's product, then one column per compared subject from facts_pack.comparison_targets. Every cell is `{text, claim_ids}` -- a claim id on EVERY cell, no exceptions (the gate rejects a cell without one). Rows come from the target's own rows; never state a spec that has no claim.
3. Deep dives (`deep_dives`): 2-4 sections, one per dimension the run's product wins on, each a heading plus 1-3 paragraphs with claim_ids on every stated fact.
4. Honest alternative strengths (`alternative_strengths`): a heading plus 2-4 items naming where the alternative genuinely wins or fits better. Compare on facts, never disparage -- this section is required, and it is what makes the page fair rather than an attack ad. Items still need claim_ids (a real, sourced fact about the alternative -- e.g. a lower price on a smaller model -- never an invented concession).
5. FAQ (`faq`): 3-5 questions a comparer actually asks; answers carry claim_ids for any stated fact.
6. Verdict (`verdict`): the bottom line -- a heading and 1-2 paragraphs naming who should pick which, then the same CTA once more (same text and url, verbatim).

## Rules
- 800-1,300 words. Word count covers prose fields; headings, urls, asset ids, claim ids, and table cells' structure are not part of the count (same accounting as every other cartridge).
- Eyebrow: literal "<A> vs. <B>" naming the run's product and the compared subject. Headline: 8 words or fewer, a literal repeat or a plain reframe of the eyebrow -- no hype, no superlative that isn't a sourced claim.
- Exactly one CTA text for the whole page, from schema.json's allowed_cta_texts -- it appears in the header and the verdict box, verbatim both times, never a second differently-worded CTA or offer card anywhere.
- Never disparage the alternative. No invented weaknesses, no fear framing, no banned-topic sections -- a topic the tenant's vocab forbids is forbidden on this cartridge too, as copy AND as a table row or column.
- No claims outside verified_claims. Every table cell, every deep-dive fact, every strengths item, every FAQ answer's facts cite claim_ids from facts_pack.verified_claims (which include the compared targets' backing claims).
- The warranty and financing sentences stay exactly as the global voice block fixes them -- verbatim or absent, never paraphrased.
- Images: 2-4 from the asset library, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field.
- Voice: plain, specific, fair. No exclamation marks. No hype words.
- Layout blocks: the proof row, table, FAQ, and verdict render through registered layout blocks; set top-level `"blocks"` per schema.json's block_slots to choose a variant, or omit it for the defaults. Blocks are layout only -- they never change what the copy may say.

## From the ad
The ad's angle decides which comparison the page makes (the writer picks the compared subject from facts_pack.comparison_targets by best fit to the angle) and which dimensions the table rows cover. The ad's objections become FAQ questions where they fit.

## From facts_pack
Specs, price, financing, warranty, reviews summary, verified claims, comparison_targets (each with its own sourced rows), the asset library.
