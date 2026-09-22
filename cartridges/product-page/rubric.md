# Rubric: product-page (v0.3.0)

Not executed in V1 (grader lands in V1.5). Fourteen one-line checks, derived from cartridge.md.

1. Word count is 200-360 words of writer copy (250-450 words on the rendered page).
2. Exactly one CTA text string, shown in the buy panel, the closing CTA and the phone bar -- nowhere else.
3. No reviews carousel, no related-products carousel, no compare table, no proof tiles, no chips or badges, no nav, no newsletter signup, no urgency.
4. Price is visible above the fold in the buy panel.
5. Financing line is exactly the allowed financing sentence given in the prompt's Financing rule -- never a freeform "/mo" figure or lender name typed in by hand, whether or not a lender is configured.
6. Every included item maps to a verified_claims id, and there are 3-6 of them.
7. The spec rows came from facts_pack.specs (renderer-owned, nothing invented).
8. The buy panel's shipping and warranty lines are the renderer's fixed, claim-backed sentences; the rating line is omitted if facts_pack.reviews_summary is null or under the review floor.
9. Hero image plus at most one promise-band image, all from the asset library.
10. No superlative ("best", "#1", "unmatched") appears without a claim_id.
11. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's ({{ authors.author.name }}'s) own first person.
12. hero.promise answers the ad's main claim in 4-10 words; any number in it is cited in hero.claim_ids.
13. The promise band has one heading and exactly 2 short paragraphs; power/outlet wording matches the product's own electrical claim.
14. Exactly 5 FAQ questions a visitor from this ad would ask; every factual answer carries claim_ids. Sentence case throughout.
