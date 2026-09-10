# Rubric: product-page (v0.1.0)

Not executed in V1 (grader lands in V1.5). Eleven one-line checks, derived from cartridge.md.

1. Word count is 250-500 words.
2. Exactly one CTA text string, shown twice (hero + repeat).
3. No reviews carousel, no related products, no nav, no newsletter signup.
4. Price is visible above the fold in the hero.
5. Financing line always carries "/mo" and a lender name.
6. Every proof bullet maps to a verified_claims id.
7. Every specs_table row came from facts_pack.specs (nothing invented).
8. Trust strip items (warranty, shipping, returns, reviews) map to verified_claims only; reviews is omitted if facts_pack.reviews_summary is null.
9. Hero image plus 1-2 detail shots, all from the asset library.
10. No superlative ("best", "#1", "unmatched") appears without a claim_id.
11. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's ({{ authors.author.name }}'s) own first person.
