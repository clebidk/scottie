# Rubric: listicle (v0.2.0)

Not executed in V1 (grader lands in V1.5). Twelve one-line checks a reviewer scores, derived from cartridge.md. Items 1-8 are also deterministic gates (harness/listicle.py); a reviewer scoring them is checking that the gate measured the right thing, not re-deriving it.

1. `style` is one of reasons/mistakes/questions/myths/tested and the headline follows the run's headline template (the style's formula unless the run names one from headlines.yaml), 8-14 words, no price. N equals the item count in every style; `tested`'s items report a claims check against verified specs, never a physical test.
2. Item count N is 5-7; each item's `number` matches its position; each heading is <=10 words with no numeral and asserts what the style says it should.
3. Each item body is 50-150 words and closes with a `proof` line that either cites a verified claim_id or reads, to a reader, as an attributed customer statement.
4. Every claims-bearing sentence (number, %, $, medical/clinical/study/proven/rated/reviews) carries a claim_id that exists in facts_pack.verified_claims -- including every FAQ answer.
5. FAQ has 5-7 questions a buyer actually asks, each answered first and qualified second.
6. `audience_fit.not_for_you` names real, checkable limits -- someone who reads it and leaves was never going to be happy. A fake drawback or a humblebrag scores zero here.
7. Closing block: exactly 3 recap bullets; the warranty line is exactly the fixed warranty sentence; the financing line is exactly the allowed financing sentence; neither appears a second time anywhere else.
8. No urgency, countdown, discount or guarantee language anywhere; one CTA text and one cta_url, repeated verbatim in the header, after items 2 and 4, in the closing block and in the sticky bar.
9. Word count is 900-1,400.
10. One hero image plus one image per item, all distinct, lifestyle or installation over stock/illustration; none depict before/after or a clinical setting.
11. Renderer-owned sections are honest by omission: the trust line, pull-quote band, model picker and HSA/FSA line appear only where this run's facts_pack verified them, and the page reads complete without the ones that are missing.
12. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's ({{ authors.author.name }}'s) own first person. Voice has no exclamation marks and none of: "game-changer", "unlock", "elevate", "journey".
