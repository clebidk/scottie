# Research: Hormozi on landing pages, mapped onto the harness

Fetched 2026-09-14. Two source files named in the task brief --
`docs/design-notes-batch50.md` and `docs/RESEARCH-LISTICLE.md` -- do not
exist in this clone (checked `docs/` listing directly); this note draws
instead on `docs/ARCHITECTURE.md`, the five `cartridges/*/cartridge.md`
files, and `harness/design_skills/rules.json`, which do exist and cover the
same ground (cartridge structure/rules and the take/adapt/decline format).

## 1. The principles, in source order

**Value equation.** Hormozi's own shop copy states the goal plainly: "Make
what you sell worth more than your prospects have ever received" (primary,
shop.acquisition.com, fetched 2026-09-14). The four-part formula -- dream
outcome and perceived likelihood up, time delay and effort/sacrifice down --
is corroborated by a secondary summary describing the ideal as a prospect
who wants to "simply 'say yes' and have their dream outcome happen" with no
added effort (Greg Faxon's $100M Offers summary, secondary, fetched
2026-09-14). The same
summary frames the mechanism: "We want clients to have a big emotional win
early" -- i.e. shrink time-to-first-benefit, not just time-to-purchase.

**One page, one action.** Search aggregation over $100M Leads material
describes "a generic opt-in page, headline, image, one call to action" as
the book's baseline landing-page shape (secondary, via search synthesis over
shortform.com/gregfaxon.com/manassaloi.com, fetched 2026-09-14). This is the
most consistently repeated Hormozi landing-page claim across every source
checked.

**Clarity over cleverness.** shedevelops.online attributes to Hormozi the
mantra "Clear > Clever" and paraphrases a 3-second test: what do I get if I
keep reading (secondary, fetched 2026-09-14).

**Conversational, not corporate, copy.** The same source paraphrases his
copy advice as writing "here's how it works in 3 simple steps" instead of
jargon, and says a landing page should "mirror a real conversation" and
answer objections before the reader has to scroll back up (secondary,
fetched 2026-09-14).

**Guarantees as risk reversal.** A search synthesis over $100M Offers
commentary describes Hormozi walking through guarantee structures "from
unconditional money-back guarantees to performance-based and even
unconventional 'anti-guarantees'" as ways to raise perceived likelihood
(secondary, fetched 2026-09-14).

**Scarcity and urgency, conditionally.** The same synthesis is explicit that
Hormozi treats scarcity/urgency as legitimate only "when they're true," and
warns against fabricated versions -- "these tactics only work long-term when
they're real, not fabricated" (secondary, fetched 2026-09-14). His own
Scarcity & Urgency training module exists at acquisition.com/training/offers9
but its body content sits behind the paid course and was not retrievable
(primary page confirmed to exist; content not accessible, fetched
2026-09-14).

**Testing discipline, not a fixed template.** A secondary paraphrase
describes his advice as "ONE split test per week," prioritizing "headlines
and hero images," sustained "for 5 years" to compound into "a Massive
Business Asset" (secondary, typeshare.co, fetched 2026-09-14). Treat this as
a process habit, not a page spec.

**What his own company's page actually looks like.** Direct observation of
acquisition.com/offers (primary, fetched 2026-09-14) found a full navigation
bar (workshops, education, media, partner, about, careers, 20+ links total),
3 distinct CTA destinations, and a results disclaimer rather than
testimonials. This is Hormozi's top-of-funnel marketing site, not a single
ad-to-page his simplicity advice describes -- worth flagging as a gap
between the stated principle and his own top-level site's execution; his ad
landing pages (not independently verified in this pass) are the examples
usually cited for the stripped-down version.

**No verified numeric claims found for:** headline word count, a fixed
above-the-fold proof/testimonial count, or page-speed thresholds. Several
search summaries floated numbers (e.g. "2-3 video testimonials above the
fold") but none traced to a page this pass could fetch and quote -- they are
omitted rather than reported as his claims.

## 2. Take / adapt / decline mapping

Same format as `harness/design_skills/rules.json` (id, title, kind,
measurable, action, check, harness note).

| id | title | kind | measurable | action | check | harness note |
|---|---|---|---|---|---|---|
| H1-one-action | One page, one action (single CTA, no competing offers) | strategy | true | take | `existing:claims.find_second_cta_violation` | Already a hard gate on every cartridge (`ARCHITECTURE.md` gate (h), the one-CTA rule; longform's "Roman hub" anti-pattern language is the same idea in the harness's own words). Hormozi restates it; no new check needed. |
| H2-headline-states-offer | Headline states the offer/outcome plainly, no cleverness for its own sake | copy | false | adapt | soft, per-cartridge headline rule | Product-page hero's "one-line promise taken from the ad angle" and longform's "headline from the ad angle" already do this. Article/listicle keep their own curiosity/number formulas by design (cold-traffic warm-up) -- Hormozi's rule adapts there as "state the audience and topic plainly," not "state the product." |
| H3-value-equation-checklist | Value equation as a writer checklist: dream outcome, likelihood via proof, time to first benefit, effort to install | copy | true | adapt | new soft check on product-page's 3 proof bullets | Product-page's 3 proof bullets already require verified claim_ids and product-benefit weighting. Reframe the selection prompt to ask the writer to pick one bullet per value-equation lever (outcome, proof-based likelihood, time-to-benefit, effort/install) instead of picking "whatever fits the angle" -- same claims-gate constraints, better bullet spread. No new gate; a prompt-wording change plus an optional soft warning if all 3 bullets map to the same lever. |
| H4-guarantee-risk-reversal | Guarantee / risk reversal as a value-equation lever | strategy | true | decline (as stated) / adapt (reworded) | n/a | Declines as Hormozi states it: tenants have a locked warranty sentence and a restocking fee (per the global voice block referenced across all cartridges) -- the harness cannot invent or restructure a guarantee. Adapts as "state the verified return/warranty terms plainly, near the ask" -- already partially true (trust strip on product-page, warranty line in longform/listicle closers); this rule would formalize proximity to the CTA rather than invent new guarantee language. Mirrors `rules.json` A4-risk-reversal exactly. |
| H5-scarcity-urgency | Scarcity/urgency when real, never fabricated | strategy | true | decline | n/a | Harness policy is stricter than Hormozi's own conditional allowance: no urgency, countdowns, or fake scarcity, full stop, regardless of whether a real constraint exists -- there is no mechanism in `facts_pack` for verifying a scarcity claim the way there is for a product spec. Matches the task's locked rule directly. |
| H6-conversational-copy | Conversational tone, answer objections before the scroll-back | copy | false | adapt | existing per-cartridge voice rules | Longform's Problem section ("objections from the ad become the problem statements") and FAQ section already do this structurally. Article's "why the usual alternatives fall short" section is the same move. No new gate; already-encoded pattern, not a new rule. |
| H7-testing-cadence | Weekly split-testing headline/hero image over years | process | false | decline | n/a | Out of scope for a one-shot generation pipeline (V1 static, per `ARCHITECTURE.md`); this is an operating cadence for a human-run funnel, not a page-generation rule. Would belong to V1.5/V2 (`grader`/`cartridge_smith`, not built) if ever adopted, and only after a scored corpus exists per the no-self-improvement-before-scoring rule. |
| H8-clear-over-clever | Clear > Clever as a copy default | copy | false | take (already the harness default) | n/a | Every cartridge's voice rules already say plain/specific/no hype; this is not a new instruction, just confirmation the harness already agrees with Hormozi here. |

## 3. Proposed "simplicity gate": measurable checks

All would live alongside the existing deterministic gate in `harness/claims.py`
/ `harness/repair.py`, in the same "fix -> reask -> exception" vocabulary
`ARCHITECTURE.md` already documents.

| Check | Threshold | Effort |
|---|---|---|
| Distinct links above the fold | <= 1 (the single CTA; a same-page `#faq` anchor doesn't count, per existing `B9-dead-hash` handling) | Low -- reuse `find_second_cta_violation`'s existing link-scan, extend to non-CTA `<a>` tags in the rendered hero region. |
| Headline word-count band per cartridge | Match each cartridge's stated band exactly (article/listicle already specify 8-14 words in `cartridge.md`; longform/product-page currently have no stated band -- would need one added) | Low -- word count is already computed (`count_words` per `ARCHITECTURE.md`'s table); this is a bound check on `page.json`'s headline field. |
| Sections before first CTA (product-page) | <= 1 (hero only; today the hero already carries the only CTA before the repeat CTA at the bottom, so this should already pass -- the check makes it enforced rather than incidental) | Low -- structural check on `page.json`'s known field order, no new writer behavior required. |
| Reading-grade band | Flesch-Kincaid grade 6-9 for body prose (soft warning, not a STOP, since some cartridges legitimately run longer/technical -- e.g. specs tables) | Medium -- needs a readability library added as a dependency (a real, visible addition per Dependencies discipline) and calibration against known-good pages before it can be a hard gate. |
| One offer element per page | Exactly one CTA destination + no second discount/offer card anywhere in `page.json`'s blocks | Already covered -- this is the existing "Roman hub" anti-pattern check (longform's rule) generalized to every cartridge. Low effort to confirm it already runs on all five, not just longform/comparison. |

## 4. What NOT to adopt, and why

- **B1-fonts / B1-type-scale / B2 / B3 / dark-hex / hero-gradient / hero-680**
  (all in `rules.json` already) -- any fixed visual token (font family, type
  scale, spacing table, corner-radius formula, hex palette, gradient text,
  fixed-width hero) conflicts with tenant brand tokens owning type and color.
  Hormozi's material doesn't specify these anyway; noted only because the
  same "tenant owns visual tokens" reasoning applies to anything a future
  landing-page source might suggest here.
- **Fabricated scarcity/urgency** -- declined even though Hormozi allows it
  conditionally ("when real"). The harness has no source of truth for a
  scarcity claim's truth the way `claims/verified.json` sources a spec or
  price, so there is no safe implementation short of a new verified-claims
  category, which is out of scope for a "simplicity gate."
- **Guarantee language as a new copy block** -- the harness cannot invent a
  guarantee a tenant doesn't offer; the fixed warranty sentence and
  restocking-fee disclosure are locked precisely so a writer can't improvise
  one. Hormozi's guarantee-stacking advice assumes the business controls its
  own guarantee terms in real time, which this pipeline deliberately does
  not allow a model to do.
- **Weekly split-testing cadence** -- a human/ops practice for a live funnel,
  not a page-generation rule; would require the V1.5 generate/grade/revise
  loop this repo has not built (`ARCHITECTURE.md`'s ladder).
- **Testimonial-count claims found only via search-engine synthesis** (e.g.
  "2-3 video testimonials above the fold") -- not adopted because no fetched
  page could be quoted as the source; adopting an unverifiable number would
  itself violate the harness's own "every claim traces to a verified source"
  rule, applied here to the research itself.

## 5. Sources

| URL | Fetched | Takeaway |
|---|---|---|
| https://shop.acquisition.com/products/100m-offers-hardcover | 2026-09-14 | Primary. Hormozi's own book-page copy: "Make what you sell worth more than your prospects have ever received." |
| https://www.acquisition.com/offers | 2026-09-14 | Primary (direct observation). Acquisition.com's own top-level page runs full nav + 3 CTAs, not a stripped one-action page -- his site doesn't fully match the stated principle. |
| https://www.acquisition.com/training/offers4 | 2026-09-14 | Primary page confirmed (Value Equation course module); body content paywalled, not retrievable. |
| https://www.acquisition.com/training/offers9 | 2026-09-14 | Primary page confirmed (Scarcity & Urgency course module); body content paywalled, not retrievable. |
| https://www.gregfaxon.com/blog/100m-offers-summary | 2026-09-14 | Secondary. Clean paraphrase of the four value-equation levers and grand-slam-offer components. |
| https://www.gregfaxon.com/blog/100m-leads-summary | 2026-09-14 | Secondary. Confirms lead-magnet-value framing; no landing-page-specific detail found. |
| https://www.shedevelops.online/blog/landing-page-lessons-hormozi-webflow | 2026-09-14 | Secondary. "Clear > Clever" quote, 3-second hook test, conversational-copy paraphrase. |
| https://www.linkedin.com/posts/uihssn_alex-hormozi-on-landing-pages-brutal-but-activity-7383997934650085376-6ljH | 2026-09-14 | Secondary, no direct Hormozi quotes -- poster's own paraphrase of clarity/proof/CTA themes. |
| https://typeshare.co/wakato/posts/the-best-tip-from-alex-hormozi-on-landing-page-optimization | 2026-09-14 | Secondary. Weekly headline/hero split-test cadence, sustained over years. |
| https://www.cursorup.com/blog/alex-hormozi | 2026-09-14 | Secondary. Author's own paraphrase of testimonial and dream-outcome-headline advice; no verifiable Hormozi quote on proof count. |
| https://leaderself.com/alex-hormozi-3-landing-page-tests-to-skyrocket-conversions-and-optins/ | 2026-09-14 | Fetch failed (HTTP 520); not used as a source. |
| https://www.youtube.com/watch?v=Qgtq-xxA00I | 2026-09-14 | Fetch returned only YouTube boilerplate, no transcript; not used as a source. |
