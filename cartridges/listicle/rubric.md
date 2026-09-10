# Rubric: listicle (v0.1.0)

Not executed in V1 (grader lands in V1.5). Ten one-line checks, derived from cartridge.md.

1. Word count is 600-1,100 words (proof_row and item bodies only).
2. Item count N is between 5 and 7; each item's `number` matches its position; the headline's "N Reasons" states the same N.
3. Headline is 8-14 words, follows the "N Reasons ..." formula, names the audience when the ad names one, never contains a price.
4. Exactly one CTA text, shown after item 3 and at the close, same text and url both times, from schema.json's `allowed_cta_texts`. No sticky bar, no countdown, no discount language anywhere.
5. Every claims-bearing sentence (number, %, $, medical/clinical/study/proven/EMF/rated/reviews) carries a claim_id that exists in facts_pack.verified_claims.
6. Proof lives inside each item's own claim_ids, not stacked in a separate section after the items.
7. The optional proof row, if present, states only a verified live rating/count, warranty term, or free shipping -- never a fourth or invented stat, never a review stat when facts_pack.reviews_summary is null.
8. One image per item, lifestyle or product over stock/illustration; none depict before/after or a clinical setting.
9. Closing block: warranty line is exactly the fixed warranty sentence; financing line is exactly "Financing is available at checkout." while no lender is configured; neither appears a second time anywhere else on the page.
10. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story (if used in an item) is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's ({{ authors.author.name }}'s) own first person. Voice has no exclamation marks and none of: "game-changer", "unlock", "elevate", "journey".
