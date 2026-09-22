# Rubric: product-page (v0.2.0)

Not executed in V1 (grader lands in V1.5). Fourteen one-line checks, derived from cartridge.md.

1. Word count is 400-800 words.
2. Exactly one CTA text string, shown twice (hero + repeat).
3. No reviews carousel, no related-products carousel, no nav, no newsletter signup, no urgency.
4. Price is visible above the fold in the hero.
5. Financing line is exactly the allowed financing sentence given in the prompt's Financing rule -- never a freeform "/mo" figure or lender name typed in by hand, whether or not a lender is configured.
6. Every proof bullet maps to a verified_claims id.
7. Every specs_table row came from facts_pack.specs (nothing invented).
8. Trust strip items (warranty, shipping, returns, reviews) map to verified_claims only; reviews is omitted if facts_pack.reviews_summary is null.
9. Hero image plus 1-2 detail shots, all from the asset library.
10. No superlative ("best", "#1", "unmatched") appears without a claim_id.
11. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's ({{ authors.author.name }}'s) own first person.
12. hero.promise answers the ad's main claim; any number in it is cited in hero.claim_ids.
13. 3-4 ad_proof tiles, each answering the ad's angle and each carrying a claim_id.
14. 5-7 FAQ questions a visitor from this ad would ask; every factual answer carries claim_ids.
