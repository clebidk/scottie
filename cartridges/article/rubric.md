# Rubric: article (v0.1.0)

Not executed in V1 (grader lands in V1.5). Eleven one-line checks, derived from cartridge.md.

1. Word count is 1,000-1,600 words.
2. Education content is at least 70% of body words (body_sections, excluding close/CTA).
3. Exactly one CTA link; no sticky bar, no countdown, no discount language anywhere.
4. Headline contains neither "Peak" nor a price.
5. Every claims-bearing sentence (number, %, $, medical/clinical/study/proven/EMF/rated/reviews) carries a claim_id that exists in facts_pack.verified_claims.
6. At least 2 cited facts appear in body_sections, cited inline as "(source name, year)" -- no raw URL in the text; the source is resolvable from the claim_id via the Sources list.
7. Any health statement names the study and its population; it never promises an outcome for Peak hardware.
8. Any competitor statement is either sourced or phrased as the speaker's own experience.
9. 2-3 images, lifestyle over product shots; none depict before/after or a clinical setting.
10. Voice has no exclamation marks and none of: "game-changer", "unlock", "elevate", "journey".
11. First-person attribution: if ad_brief.speaker_pov is first_person, the speaker's story is attributed to "a customer" (or facts_pack.speaker_name) -- never told in the author's (Austin's) own first person.
