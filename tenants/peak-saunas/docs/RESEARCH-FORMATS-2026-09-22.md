# Research: Two Best Non-Listicle Paid-Traffic Formats for a $5k-$9k Considered Purchase

Compiled 2026-09-22. Read first: `tenants/peak-saunas/docs/REFERENCE-LANDERS-2026-09-18.md` (listicles, advertorials, product/design landers — not repeated here). Companion data file: `tenants/peak-saunas/refs/reference-formats.json`. Sources: curl on raw HTML/CSS plus a rendered-browser pass where a page is client-rendered (noted inline). Nothing below is invented; where a fact could not be verified it is marked `null` in the JSON and flagged in prose.

## Method

Eight candidate formats were evaluated. For each, live examples were sought first from the brands named in the brief, then from the closest available substitute when the named brand had no live page of that shape. Four candidates got full HTML/CSS measurement (quiz, comparison, calculator, expert-roundup); four could not turn up a genuine, fetchable, cold-traffic landing page at the $5k+ tier after multiple searches — that absence of evidence is itself scored, not papered over with a weaker analog dressed up as a match.

---

## 1. Quiz / self-assessment funnel

**Live example measured: Sun Home Saunas — https://sunhomesaunas.com/pages/quiz** (direct competitor, same category, same price band)

Curl returns only ~1,062 words and a single `sr-only` H1 — this is a client-rendered quiz app, so it was walked step-by-step in a rendered browser instead.

- **Flow observed:** utility/promo bar + full site nav → thin flame-icon progress bar → 6 question screens (goals, setup type, sauna type, usage occasions, group size, placement, familiarity — branching, so exact order/count can vary) → 3 non-interactive **"Did you know?"** educational interstitials (photo + 2-3 sentences + single "Continue" button) interleaved every ~2 questions → a **soft** email-capture step ("Last step! Enter your email address so we can send you updates" with a plainly visible **"Skip and View Results"** link — not a hard gate) → results page.
- **Question mechanics:** single-select auto-advances on click (no extra "Next" tap); multi-select shows a "Next" button and enforces "Select up to 2" with inline red-banner validation if you try to skip.
- **Interruption:** an unprompted popup ("Bring Wellness Home — Start with $200 Off Plus Free Shipping ($1,500 Value)", email field, "Unlock My Discount") fired after the very first question — before the visitor had invested any real time in the quiz. It had no reachable close button in the accessibility tree (likely an iframe) and only closed on Escape. This is a real UX cost worth avoiding in a rebuild: don't interrupt a quiz with a second, unrelated conversion ask before it delivers its own payoff.
- **Result page:** "RECOMMENDED SETUP" eyebrow → "Your recommendation is ready." → one personalized product (photo, name, "From $13,599") → **"WHY THIS MATCHES YOU"** — 4 checkmarked bullets, each explicitly tied back to an answer the visitor gave (e.g., "You prioritized recovery — heat therapy accelerates muscle repair...") → dual CTA (**"Shop Now"** primary, **"Talk to a Sauna Expert"** secondary text link) → **"Complete Your Setup"** cross-sell grid (3 more products, each with price + "Shop Now") → "Retake Quiz" link.
- **Design:** body/heading font `"Suisse Intl", sans-serif` (same theme as Sun Home's blog pages, already logged in the reference-landers doc). Primary CTA: background `rgb(26,39,68)` (dark navy), white text, `border-radius:10px`, `padding:16px 28px`, height ≈57px.
- **What this proves for a $5k-9k product with real SKU variation:** the format earns its length by (a) pacing questions with pure-education breaks so it doesn't feel like an interrogation, (b) never hard-gating the payoff behind an email, and (c) making the result feel earned — a specific price and a specific, answer-referencing rationale, not a generic "you got: Wellness Seeker" quiz-personality result.

**Fit note:** Peak Saunas already runs its own quiz (`peaksaunas.com/pages/sauna-selector-quiz`) and its own compare page (`peaksaunas.com/pages/compare`) — these exist as real product-funnel pages already, separate from the five ad-lander looks this project is building. This research is about the *paid-traffic ad-lander* version of the format, which is structurally different (built to run cold, from an ad brief, not a persistent nav destination).

---

## 2. Comparison / "vs" page

**Live examples:**
- **Sun Home Saunas — https://sunhomesaunas.com/blogs/saunas/sun-home-vs-other-home-sauna-brands** (already fully measured in `REFERENCE-LANDERS-2026-09-18.md`: 1,494 words, 4 H2s — "How Sun Home Compares at a Glance" / "Dedicated Brand-by-Brand Comparisons" / "How Each Brand Differentiates" / "How to Decide Between Brands" — structured as a comparison table + decision framework, `warranty` mentioned 12×, no FAQ, no sticky purchase bar, shares Sun Home's `"Suisse Intl"` theme.) That doc already calls this "the more directly applicable model for Peak Saunas given the shared category" — this research confirms that conclusion still holds against a second, cross-category example.
- **Eight Sleep — https://www.eightsleep.com/blog/eight-sleep-versus-tempur-pedic/** (first-party, $2k-4k smart-mattress category, newly measured): 1,301 words, H1 names both products directly ("Comparing the Eight Sleep Pod Pro and LuxeBreeze by Tempur-Pedic"), structured as **H3 "Key Similarities:" then H3 "Key Differences:"**, each with short bullet-style contrasts. 7 images. No purchase CTA found in the fetched HTML — this specific article reads as SEO/editorial content that funnels traffic back to the product page via internal links, not a hard-sell ad lander with its own CTA.

**Pattern across both:** neither brand-run "vs" page leans on urgency or a bottom sticky-ATC bar the way the DTC listicles do. Both use a **table or two-column bullet contrast** as the core device, name the competitor directly, and let the comparison itself do the persuading — appropriate for a buyer who is already evaluating alternatives (which is exactly the mental state a "Peak Saunas vs Sunlighten vs Clearlight" cold ad would target).

**Fit note:** Peak Saunas already has a first-party competitor comparison, `peaksaunas.com/blogs/wellness/clearlight-vs-sunlighten-vs-peak-saunas-comparison` and `peaksaunas.com/pages/compare` — so the facts needed to build a paid-traffic "vs" ad-lander (EMF levels, warranty terms, heater tech, price) are very likely already assembled in-house; this format has the lowest net-new research burden of the two winners.

---

## 3. Calculator / ROI page — runner-up

**Live example measured: Solar.com — https://www.solar.com/learn/solar-calculator/**

2,609 words (mostly SEO/editorial content below the tool — the calculator widget itself is a small fraction of the page and is client-rendered, so its live interaction couldn't be exercised headlessly), 5 H2s, 15 images, H1 "Solar Panel Cost Calculator." Output fields are framed as concrete numbers a financed buyer actually cares about: **Monthly payment, Bill reduction, Lifetime energy cost, Lifetime savings, Average electricity rate** — payment-first, not price-first, which matches how a $15k-30k solar purchase is actually sold (financed, monthly). A mid-page CTA reads "See your real solar price in 90 seconds," setting a low-friction time expectation.

**Why it's a runner-up, not a winner:**
1. **Peak already has this page live** (`peaksaunas.com/pages/sauna-cost-calculator`) — it is not a gap to fill, unlike quiz and comparison which don't yet exist as ad-lander looks.
2. **Weaker message-match to a cold ad.** A calculator serves a visitor who has already decided to evaluate cost — it's a strong mid-funnel/retargeting tool, but a cold-traffic hook needs a bigger emotional hook than "estimate your monthly payment" up front.
3. **Build complexity is the real constraint.** A calculator that stays "claims-safely run from verified facts only" needs real math (kWh cost × session length × frequency, or financing APR × term) validated against Peak's actual numbers — that's a heavier lift for a template-plus-writer harness than a static comparison table or a branching quiz tree, even though both are also "static HTML + tiny inline script."

---

## 4. Expert roundup / research & evidence page — loser

**Live example measured: Oura — https://ouraring.com/science-and-research**

1,491 words, H1 "Grounded in science, driven by research," 17 images. The page is a **library of individually named, individually linked peer-reviewed studies** (not paraphrased claims) plus an "Independently validated sensors" section and the claim "Powering research published in over 130 peer-reviewed publications." No above-fold CTA, no offer, no urgency device was found anywhere on the page — it reads as an organic/SEO trust destination, not a page built to receive cold paid-social traffic.

**Why it loses:** this is structurally the same "claim library with named, checkable sources" mechanic that Sun Home's own guide already runs — the existing reference doc already measured Sun Home's **evidence-labeling system** (manufacturer-published / independently tested / editorial designation / documented absence) plus its **Claim Ledger** and **Sources** section, and flagged it as the standout trust pattern for this vertical. Building a second, separate "expert roundup" format on top of that would duplicate work the advertorial/guide format already does, for a format that (per Oura's own real-world usage) isn't actually run as a cold-traffic ad lander in the first place.

---

## 5. Founder letter / long-form narrative sales page — loser

Searched specifically for Purple, Snow, and MUD\WTR (the three brands named in the brief) for a *dedicated, first-person, conversion-built* founder-letter page distinct from their existing "about us" / blog founder-story content. None turned up a live, fetchable page of that shape:
- Purple's founder story lives on `purple.com/about-us` and in third-party press (Shopify, Forbes) — no standalone paid-traffic founder-letter lander found.
- MUD\WTR's own named landing pages (per a marketing case study covering their optimization work) are **"Rise-2," "Rise-2-Coffee," "Calm Seeker," "Gut Guardian," and "Product Comparison"** — i.e., their paid-traffic pages are listicle- and comparison-shaped, not founder-letter-shaped. `mudwtr.com/pages/rise-2` was fetched but is a heavy, JS-rendered page-builder page consistent with a listicle template, not a narrative letter.
- Snow's founder story exists in podcast/press interviews, not a page.

**Why it loses even without a disqualifying live example:** it overlaps with the advertorial format Peak already has (`REFERENCE-LANDERS-2026-09-18.md` already documents Resilia's narrative-arc advertorial), and a founder letter specifically requires a real, verifiable founder biography and voice — for Peak, that means real quotes and real history from an actual person, which the "no invented testimonials" rule extends to: no invented founder narrative either. Worth a real "why we built Peak" page eventually, but as a *researched, verified* piece, not as a template a writer fills from an ad brief.

---

## 6. Customer case study / "a week with" diary — loser

No live, fetchable $5k-9k example was found. Search results describe the format only as marketing-agency advice ("a first-week diary can become a 30-second video... a landing page section that explains what to expect") — theoretical guidance, not a page that exists to measure.

**Why it loses:** by definition this format needs a real customer's real day-by-day experience (photos, quotes, a believable timeline) collected over weeks. The harness builds from an ad brief plus verified facts; it has no mechanism to source or verify an ongoing customer diary, and fabricating one would directly violate the no-invented-testimonials constraint. Highest claims-safety risk of any candidate.

---

## 7. Video sales page with transcript — loser

No live DTC example with a fetchable transcript was found at any price tier, let alone $5k+; search results are all VSL "how-to" guides, not real pages. **Why it loses:** it requires a produced video asset (script, talent, filming, editing) that sits entirely outside a static-HTML-page-generator's scope — this project's own interactivity constraint ("works as static HTML + tiny inline script only") rules out the one thing that defines the format.

---

## 8. Interactive configurator — loser

Eight Sleep's closest analog is a **"Choose your size"** selector plus financing options (Affirm/Klarna/Autopilot subscription) embedded in the Pod 5 product page itself (confirmed via search: Full $2,799 / Queen $2,999 / King $3,199 before required first-year Autopilot) — not a standalone landing page. No public Peloton or Tempo configurator *landing page* (as opposed to a PDP variant-picker) was found. **Why it loses:** where this pattern exists, it lives on the money page (the PDP), not as a first-touch paid-traffic lander — and building real multi-variable pricing math across dozens of options is a heavy lift for a one-off ad lander built from a brief, for a payoff (a size/color picker) that a $5k-9k sauna's actual configuration surface (mostly: which model, which size) doesn't need.

---

## Scoring table (1-5, 5 = best)

| Candidate | Fit for $5-9k purchase | Cold-ad message match | Claims-safe from verified facts | Build complexity (lower=better→scored inverse) | DTC evidence at scale | **Total** |
|---|---|---|---|---|---|---|
| **Quiz / self-assessment** | 5 | 5 | 5 | 3 | 4 (in-category) | **22** |
| **Comparison / "vs"** | 5 | 4 | 5 | 5 | 4 (in-category + Eight Sleep) | **23** |
| Calculator / ROI | 3 (already exists for Peak) | 3 | 3 (needs real math) | 2 | 3 | 14 |
| Expert roundup / research | 3 (redundant w/ existing guide) | 2 (not run as cold-ad format) | 4 | 3 | 2 | 14 |
| Founder letter | 3 | 3 | 2 (needs real founder voice) | 3 | 1 (no live example found) | 12 |
| Case study / diary | 3 | 3 | 1 (needs real ongoing customer) | 2 | 1 (no live example found) | 10 |
| Video sales page | 2 | 3 | 2 | 1 (needs produced video — outside static-HTML scope) | 1 (no live example found) | 9 |
| Interactive configurator | 2 (low config surface for a sauna) | 2 | 4 | 1 | 1 (only PDP-embedded, no lander) | 10 |

---

## Winners: two concrete page specs

### Winner 1 — Quiz / self-assessment funnel

**Section order:**
1. Promo/utility bar + minimal nav (logo + phone number only — drop the full site nav Sun Home keeps, since this is a cold-traffic ad lander, not a persistent destination)
2. Thin progress bar (visual only, no step-count label — Sun Home's flame-marker pattern)
3. Question screens — **from ad brief:** which 4-6 questions to ask (goal, use-case, space, budget signal); **from renderer:** icon-card grid layout, single vs multi-select logic, inline "select up to N" validation
4. 1-2 educational interstitials between question blocks — **from verified facts:** the actual claim used (infrared vs traditional heat mechanics, EMF facts, etc. — pull only from Peak's verified spec sheet, never invent a health claim)
5. **No mid-quiz popup interruption** — this is the one thing to explicitly *not* copy from Sun Home
6. Optional, clearly-skippable email step (never hard-gate the result)
7. Result screen: recommended SKU (photo, name, **real price from the product catalog**) + "why this matches you" bullets **generated by mapping quiz answers to verified product attributes**, not free-text copy
8. Cross-sell grid of 2-3 alternate SKUs with prices
9. Dual CTA: primary "Shop Now" → PDP, secondary "Talk to a Sauna Expert" → contact/chat

**Interactivity as static HTML + tiny inline script:** a simple JS state machine (array of question objects, `currentStep` index, answer object in memory) with a small deterministic scoring function (weighted match against 3-4 product profiles) — no backend call needed if the product set and quiz logic are baked in at build time.

**CTA plan:** none until the result screen (by design — a CTA during the quiz would undercut the "help me decide" framing).

**Proof plan:** the personalized "why this matches you" list *is* the proof — it substitutes for testimonials/reviews entirely, and it's inherently claims-safe because every line traces to a real product attribute plus the visitor's own click.

**Mobile notes:** icon-card grids need to collapse to 2-across max on mobile (Sun Home's desktop grid is 4-6 across); the progress bar and question headline must stay above the fold on a 375px viewport without the icons getting cut off.

**References:** https://sunhomesaunas.com/pages/quiz (primary model); Prose (prose.com/consultation/haircare), Warby Parker, and Function of Beauty confirmed via search as the standard long-personalization-quiz precedent outside this category, for pacing/interstitial-style borrowing only — not separately measured here.

### Winner 2 — Comparison / "vs" page

**Section order:**
1. H1 naming Peak Saunas + the specific competitor (from ad brief: which competitor the ad is targeting — Sunlighten, Clearlight, or a category generic like "cheap Amazon saunas")
2. "At a Glance" summary table — **from verified facts:** price, warranty length, EMF spec, heater type, HSA/FSA eligibility, financing availability, review count — every cell must be a verified, sourced number (reuse Peak's own existing `compare` page data as the fact source)
3. "Key Differences" section, Eight Sleep's H3-pair pattern adapted: **"Where They're Similar" / "Where They Differ"** as two short bullet blocks, not paragraphs
4. "How to Decide" — a short decision-framework paragraph (Sun Home's pattern), not a hard sell
5. Single CTA near the bottom ("See the full spec sheet" or "Shop Peak Saunas") — deliberately not repeated 3-5× the way a DTC listicle does, since neither brand-run reference page leans on urgency/repetition
6. Optional FAQ (2-3 questions anticipating the "but what about X competitor feature" objection)

**Interactivity as static HTML + tiny inline script:** none required — this format is fully static; the only "script" is a responsive table-to-card collapse on mobile.

**CTA plan:** one primary CTA, placed after the comparison table, not above the fold — the comparison itself is the persuasion device, matching both measured references.

**Proof plan:** the comparison table cells are the proof (a claims-safe format almost by construction, since every fact is either Peak's own verified spec or a competitor's publicly stated spec — no testimonials needed at all).

**Mobile notes:** the comparison table is the highest-risk element on mobile — both references handle this differently (Sun Home: horizontal-scroll table; a stacked-card fallback, one card per competitor with the same field labels repeated, is safer for a template than horizontal scroll, which is easy to miss on a phone).

**References:** https://sunhomesaunas.com/blogs/saunas/sun-home-vs-other-home-sauna-brands (primary in-category model); https://www.eightsleep.com/blog/eight-sleep-versus-tempur-pedic/ (secondary, cross-category, confirms the "Similarities/Differences" H3 pattern at a comparable price point).

---

## Runner-up

**Calculator / ROI page** — real evidence it works for financed, high-ticket categories (Solar.com), and financing/HSA-FSA is core to Peak's own positioning, but it loses to the two winners because Peak already has this page live, it fits mid-funnel better than a cold ad, and doing the underlying math "claims-safely" is a heavier build than either winner. Worth building third if a third format is wanted later.
