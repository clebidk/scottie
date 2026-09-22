# Rubric: quiz (v1.0.0)

Not executed in V1 (grader lands in V1.5). Ten one-line checks a reviewer scores, derived from cartridge.md. Items 1-6 are also deterministic gates (harness/quiz.py, keys `quiz:*`); a reviewer scoring them is checking that the gate measured the right thing, not re-deriving it.

This file is the REVIEW rubric for a written page. The SCORING rubric -- which answer recommends which model -- is tenant data in `tenants/<tenant>/quiz/rubric.yaml`, validated by `harness/quiz.py`'s `validate_rubric` before any writer call.

1. The headline is exactly "Which <category> is right for <audience>?" in sentence case with nothing after it; the category names no company or model, the audience names people by situation or goal. The dek is one sentence of at most 20 words.
2. One question per rubric question, in rubric order; each prompt is one short question in the page's voice that asks the same thing as the rubric prompt; the option labels are the rubric's, verbatim and in order.
3. Each interstitial sits after the question the rubric names and teaches one verified fact on its topic in at most 25 words. Any number, price or trigger word carries a claim id.
4. FAQ has 3-5 questions a buyer asks before choosing a model; every answer that states a fact carries claim ids.
5. No result card, price, capacity line, "why" list, trust line, financing, HSA or warranty line is written in page.json -- the renderer builds them from verified data.
6. No urgency, countdown, discount or sale language, no email request, no competitor name, no retired name. One CTA text from the allowlist and the featured model's own url.
7. Word count is 400-700.
8. With the script on: one question at a time, "Question N of M" and the progress bar move, Back works, a chosen tile moves on by itself, the result names one model with its verified price, image and CTA, and every "why it fits you" row is an answer the reader chose.
9. With the script off: every question and interstitial reads as one list, and the result shows the featured model's card with the note that says why.
10. The hero image shows the featured model; the voice has no exclamation marks and none of "game-changer", "unlock", "elevate", "journey".
