# Rubric: landing (v0.2.0, cycle 110)

Ten one-line checks for a human reviewer, derived from cartridge.md. The gate column names the deterministic check that already enforces each line (harness/landing.py, harness/persuasion.py, harness/design_gate.py).

1. The first screen pays off the ad's hook: the headline restates it (same words, number, person) or quotes the speaker. Gate: `landing:hook_match`.
2. On a 375px phone inside the store theme (y 116 to h-212), the eyebrow, headline, subhead, proof row, price line and CTA pill all show on load, and the photo's top edge shows. Gate: design `chrome_window` at 375x812, 390x844, 360x780.
3. Nothing on the page quotes, grades or corrects the ad ("the ad", "actually", "not lifetime", "priced plainly", "really says"). Gate: `landing:ad_reference`.
4. The reasons give the reader's outcome first, then the spec that makes it true, and cover outcome, likelihood, time and effort; on a sale page the sale is reason one. Gate: `landing:reason_*`, `landing:reason_levers`.
5. Proof: the store rating (else the product rating) beside the first CTA, units delivered when verified, two or three verbatim reviews when the facts carry them, the founder line and support in the trust block; the rating shows in at most two places.
6. The offer: the net sale price with the struck list price and the code (or the price and the offer line), the verified deadline, the monthly figure only with its disclosure; never a countdown, a stock count, "applies automatically" or a returns promise. Gates: `landing:auto_offer`, `landing:returns`, the urgency gate.
7. One CTA phrase for the ad's angle (sale, financing, story, default), in six placements plus the sticky bar, each with `data-el`; never more than 1.5 phone screens without one. Gates: CTA allow-list by angle, design `cta_spacing`.
8. The FAQ answers the buyer (use, session, heat-up, fit, paying over time, HSA/FSA when verified): at most one logistics question, no medical question, no returns. Gates: `landing:faq_logistics`, `landing:faq_health`, `landing:faq_hsa`.
9. Reading grade 7.5 or lower; no placeholder text; every first-screen line written. Gates: `landing:reading_level`, `landing:placeholder`, `landing:hero_required`, the rendered placeholder scan.
10. White page, no gradients or shadows, 4px radii, Basalt pills, DM Sans and Poppins only (no Montserrat or Acid Grotesk on the page); HTML at most 60 KB, hero image at most 60 KB WebP, LCP at most 2.5 s and CLS at most 0.1 under Slow 4G with a 4x CPU slowdown. Gate: design `load_budget`.
