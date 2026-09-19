# Reference Landers — Structural Spec for Listicle Template Rebuild

Compiled 2026-09-18. Source: real HTML/CSS pulled via curl and a rendered-browser pass (for JS-built pages). Companion data file: `tenants/peak-saunas/refs/reference-landers.json` (same facts, machine-readable, `null` where a fact could not be verified). Figures below are directly observed; nothing is invented.

---

## 1. Grüns — Listicle

**URL:** https://gruns.co/pages/green-powders-listicle
**Title:** "Grüns | The Greens Powder Alternative You'll Actually Take" · **H1:** "5 Reasons Why Millions Are Ditching Green Powders for Grüns"

- Word count (main): **4,975** · Images: **120** (very dense — lifestyle photos, ingredient shots, review avatars, product carousels)
- H2 count: **4** substantive — `TASTE`, `CONVENIENCE`, `FIBER`, `BENEFITS`. The "5 Reasons" framing is in the H1/hero only; the body is organized around 4 comparison pillars (Grüns vs. green powders), not 5 numbered sections.
- **Above the fold, in order:** urgency promo bar ("Limited-Time Sale: Get 55% off + free shipping") → logo → hero lifestyle photo with a "Clinicians' Choice" badge overlay ("1,901 clinicians share this on FrontrowMD") → offer badge → H1 → subhead → CTA button "Save 55% + Free Shipping" → star rating + review count + member count line ("4.8 stars · 100K+ reviews · 1M+ members").
- **CTAs:** 33 button-like elements. Top texts: "Save 55% + Free Shipping", "Start Now", "View Nutrition Label", "Learn more", "Why Grüns?", "Ingredients & Allergies", "Science & Certifications", "Directions".
- **Sticky bar:** present — confirmed via CSS (`.sticky-cta`, Tailwind component, active ≥992px). Holds: logo + "Save 55% + Free Shipping" CTA + the same star/review/member proof line.
- **Proof:** star-rating + review + member-count line, clinicians badge, "Real Reviews, Even Realer Results" review section.
- **Offer:** 55% off + free shipping, HSA/FSA eligible via Truemed, "Limited-Time Sale" urgency.
- **Design:** body font stack `Work Sans, Helvetica Neue, Helvetica, Arial, sans-serif` (CSS var `--font-primary`); `.container` max-width 992px; breakpoints at 395/400/992px. CTA renders as a green pill button on-screen; the CSS brand variable `--color-primary` resolves to red (#ed1c24) but is overridden by a campaign sub-theme on this page, so the exact live hex isn't attributable with confidence — treat as "brand green pill, rounded" rather than a hex.

---

## 2. Resilia — Advertorial

**URL:** https://resilia.shop/pages/i-thought-i-was-just-tired-and-bloated-then-i-learned-what-was-really-happening-inside-my-gut
**Title:** "I Thought I Was Just Tired and Bloated… Then I Learned What Was Really – Resilia" · **H1 (quote-style):** "'I Thought I Was Just Tired and Bloated… Then I Learned What May Really Be Happening Inside My Gut'"

- Word count: **1,863** · Images: **50** · H2 count: **11** — The Hidden Imbalance No One Talks About / Symptoms That May Be a Sign of Internal Imbalance / How Do Parasites and Candida Get Inside Us? / Why Most People Don't Know / What Happens If You Don't Address It? / The Natural, Research-Backed Approach / Your Resilia Timeline / What's In Resilia / What Customers Are Saying After Trying Resilia / Start Supporting Your Gut Health Today / Vital Flora Insight.
- **Above the fold, in order:** top promo bar ("Take Care Of Yourself · SHOP NOW") → quote-style H1 with highlighted orange words → subhead → **"Sponsored Content" disclosure label** → byline (author avatar photo + "By Jonathan Foster" + date "August 10, 2025") → before/after split photo → **sticky bottom bar** with product thumbnail + "TRY RESILIA RISK-FREE FOR 60 DAYS".
- **Structure is narrative, not a numbered list:** hidden problem → symptoms → cause (how parasites/candida get in) → why most people don't know → consequences → the natural solution → a "Resilia Timeline" (day-by-day progression) → ingredients → customer testimonials → CTA. This is the core advertorial shape to copy for editorial-style landers.
- **Sticky bar:** present, bottom-fixed, product thumb + CTA button — the clearest sticky-ATC example in the set.
- **Offer:** 60-day risk-free guarantee is the primary mechanic (no discount emphasis in this variant).
- FAQ: none on this specific article (FAQ lives on the listicle variant below).

---

## 3. Resilia — Listicle

**URL:** https://resilia.shop/pages/5-reasons-resilia-is-the-only-oregano-oil-that-actually-works
**H1:** "5 Reasons Resilia Is The Only Oregano Oil That Actually Works"

- Word count: **1,434** · Images: **48** · 5 numbered reasons (plus "What Happens When You Try…", "Clinically Credible. Statistically Clear.", and an FAQ section — 10 H2-level blocks total when counting those).
- **Above the fold:** dark-green hero band → logo → sticky "Shop Resilia Now" CTA (top-right, not bottom) → H1 (italic accent "5 Reasons" + serif rest) → subhead ("...Resilia uses wild mountain oregano proven to deliver the strength used in Harvard and Johns Hopkins studies") → product image (3 pouches) → **scrolling benefit-claim marquee ticker** ("85% Wild Greek Carvacrol Power", "No Burn, No Stomach Pain", "Black Seed Detox Support"...) → jump-nav tabs (Source / Black Seed Oil / Tried & Tested).
- **Reason headings** (curiosity + parenthetical benefit claim style): "It's Wild Oregano From Greek Mountains (Where Nature Makes It 85% Stronger)", "Softgels Stop The Burning (So You Can Actually Take It)", "Black Seed Oil Stops The 'Detox Crash' Before It Starts", "Every Batch Is Tested (Because Most Oregano Is Fake)", "Stays Fresh For 2 Years (Your Last Softgel Works Like Your First)". Each item has its own photo; no per-item micro-CTA (the single sticky header CTA carries the whole page).
- **FAQ: 10 questions** (confirmed by DOM scrape) — results timeline, difference vs. liquid drops, probiotic compatibility, prevention use, food timing, sensitive stomach, detox symptoms, storage, daily-use safety, guarantee.
- **Design:** heading font **PT Serif**, body font **Inter** (both Google Fonts, loaded explicitly for this page). Brand primary button color `#085946` (dark green) / white text, from `--colorBtnPrimary` in theme.css. `.page-width` max-width 1500px.

---

## 4. Sun Home Saunas — Guide/Buyer's-Guide (editorial, used as ad lander)

**URL:** https://sunhomesaunas.com/blogs/saunas/why-customers-choose-sun-home
**H1:** "Why Buyers Choose Sun Home Over Other Premium Sauna Brands (2026)"

- Word count: **4,261** · Images: **~1** in main content (this is a text-and-table-heavy editorial page, not a photo-heavy DTC lander).
- H2 count: **10** — Disclosure and How This Guide Labels Evidence / Sun Home at a Glance (Verified August 7, 2026) / The 10 Differentiators / Feature-by-Model Matrix / Who Should Not Choose Sun Home / FAQs / Research Methodology and Verification / Sources / Claim Ledger / Related Guides.
- **Above the fold:** full site nav (Saunas / Cold Plunges / Explore, search/account/cart/"Need help?") → hero title on a grey-gradient band → "Published April 22, 2026" → **byline credential block**: "Written by: [Name], Senior Heat Therapy Writer" + "Expert Contributor: [Name], Copywriting Specialist".
- **Category-specific proof/objection handling (the standout pattern for this vertical):** every claim in the article is tagged with one of four evidence labels — *manufacturer-published*, *independently tested*, *editorial designation*, or *documented absence* — spelled out in an explicit "Disclosure and How This Guide Labels Evidence" section. The page also runs a **"Claim Ledger"** and **"Sources"** section, and a **"Who Should Not Choose Sun Home"** section (honest anti-testimonial). `warranty` appears 33×, `HSA`/`FSA` 7× each, `financing` 5×.
- **The 10 Differentiators** are numbered H3s ("1. The feature combination across one lineup", "2. A design language protected by design patents and trade dress"…"10. Published pricing on every model, starting at $4,999–$5,599").
- **FAQ: 7 questions** — differentiation, comparison to established brands, worth-the-price, warranty terms, safety testing, customer service, HSA/FSA eligibility.
- No sticky purchase bar — only a sticky site nav and a live-chat widget (bottom-right).
- **Design:** font `"Suisse Intl", sans-serif` for both body and headings (`--font-body` / `--font-heading`); breakpoints 640/768/1440px; Tailwind-style containers at 40rem/48rem/64rem.

---

## 5. Sun Home Saunas — Comparison Guide

**URL:** https://sunhomesaunas.com/blogs/saunas/sun-home-vs-other-home-sauna-brands
**H1:** "Sun Home Saunas vs Other Brands (2026): How We Compare"

- Word count: **1,494** · Images: **~1** · H2 count: **4** — How Sun Home Compares at a Glance / Dedicated Brand-by-Brand Comparisons / How Each Brand Differentiates / How to Decide Between Brands. 6 H3s underneath (per-brand comparison subheads).
- Shares the same theme/CSS as page 4. `warranty` mentioned 12×. Structured as a comparison table + decision framework rather than a testimonial-driven page. No FAQ heading on this variant, no sticky purchase bar.
- Useful as the "vs" / comparison-table pattern reference for the same product category as Peak Saunas.

---

## 6. Jones Road Beauty — Listicle

**URL:** https://www.jonesroadbeauty.com/pages/5reasons
**H1:** "5 Reasons Why You Need To Try Jones Road Beauty"

- Word count: **395** · Images: **10** (figures from live DOM — the server-rendered HTML is empty; this page's content is injected by a Shopify page-builder app block at runtime, so `curl` alone under-reports it. Verified with a rendered-browser pass.)
- **Notable structural anti-pattern:** the entire page uses `<h1>` for every heading — the main headline, the subheadline, and all 5 numbered item headers — with **zero `<h2>`/`<h3>` anywhere**. Worth avoiding in the rebuild; use real heading hierarchy.
- **Above the fold:** no header/nav at all (standalone lander, no site chrome) → H1 headline (2-line) → subhead ("The new clean makeup brand founded by none other than beauty icon Bobbi Brown") → CTA button "FIND YOUR PRODUCTS". No hero image, no proof badges above the fold.
- **Item pattern:** numbered + all-caps benefit claim heading (e.g., "1. CREATED FOR THE PERFECT NO-MAKEUP MAKEUP LOOK") → ~55-word body → **micro-CTA button "FIND YOUR PRODUCTS" repeated after every single item** (6 times total including the hero). Item 4 carries the only proof element: an unattributed press pull-quote name-dropping Allure/Vogue/WSJ/Fast Company. No star ratings or review counts anywhere on the page.
- Closing: one longer testimonial blockquote, then final CTA "TAKE THE QUIZ".
- An exit/engagement popup (skin-type quiz + "Unlock Free Shipping on orders over $55") fires during the session — a retention/list-building layer worth noting even though it's not part of the static page.
- **Design:** heading font `RingsideWideWeb, Helvetica, Arial, sans-serif` (condensed all-caps display face used for the H1); theme also loads `Canela` serif for other UI. CTA buttons render black, square corners, white uppercase text.
- **Note:** the sibling page `/pages/what-the-foundation-5-reasons-why` returned HTTP 200 but rendered only an empty `<h1>` with no body copy — confirmed broken via rendered-browser check, excluded from the dataset.

---

## 7. Moon Pod — Listicle

**URL:** https://www.moonpod.co/pages/5-reasons
**H1:** "5 Reasons Stressful Times Demand this Anti-Gravity Beanbag"

- Word count: **573** · Images: **20** (live DOM figures).
- Same heading anti-pattern as Jones Road: **every text block is an `<h1>`** — headline (×2), the bare numerals "1"–"5" for each item, and the closing CTA heading — with zero `<h2>`/`<h3>`.
- **Above the fold:** logo → top nav with a "SHOP NOW" button → H1 headline → subhead → intro paragraph → CTA button "Shop Moon Pod" → diagonal-cut split lifestyle photos (person on laptop / person relaxing).
- **Item pattern:** bare numeral + bold benefit statement (not a styled heading, just bold body text) → ~65-word body. **No micro-CTA and no proof line per item** — the only CTAs on the page are the hero "Shop Moon Pod", a closing "Shop Moon Pod", and a final "SHOP NOW" (3 CTA instances total).
- Closing: "It's Time to Relax with a Moon Pod" + CTA, then **3 full customer testimonials** (name, location, title, quote) — no star ratings, no press logos anywhere on the page.
- No sticky purchase bar found in CSS; the top nav's "SHOP NOW" button scroll-persistence was not directly confirmed.
- **Design:** heading font **Montserrat**, body font **Nunito Sans** (both Google Fonts). A pill-radius button pattern (`border-radius:100px`) exists in the CSS for at least one button variant.

---

## 8. The Earthling Co. — Product-lander / Advertorial hybrid

**URL used:** https://theearthlingco.com/pages/lpd1-sbts-scset *(substitute — see note)*
**H1:** "Most shampoos contain detergent"

- Word count: **670** · Images: **49** · H2 count: **6** — Why Choose Earthling Shampoo Bars / Intentional by Nature / Discover why tens of thousands... / Our Customers Feel Good About Their Plastic-Free Hair Care Routine / Choose Your Shampoo & Conditioner Set / (closing paragraph heading).
- **Above the fold:** promo bar "Free Shipping on orders $45+" → full nav (Shampoo / Conditioner / Sets / Quiz / Shop+) with search/account/cart icons and a "buy now" nav pill → large wordmark logo → **star rating "4.8 stars based on 18,000+ reviews"** → problem/agitate headline "Most shampoos contain detergent" → body → customer photo testimonial with its own 5-star rating.
- **"Why Choose" section is 3 numbered, all-caps short items** (NO DETERGENT / CLEAN INGREDIENTS / HIGH QUALITY), each 1–2 sentences, no micro-CTA per item.
- **Proof:** star rating repeated 3×, **"As Featured In" press logos** (The Good Trade, Reader's Digest, Mane Addicts, PopSugar), "Climate Neutral Certified" badge, reviews widget showing 92,739 reviews at a 4.82 average, "Satisfaction Guaranteed" 30-day refund callout.
- **Offer:** free shipping $45+, 30-day guarantee; no discount/urgency copy observed.
- No FAQ section on this page.
- **Design:** button `#5e7262` (sage green) background, white text, `border-radius:6px`, height 60px (primary CTA) / 40px (secondary); body/heading font **Filson Pro** (Regular for body, Bold for headings), with **Latienne Pro Bold** serif used for select accent headings; `.container` max-width 1200px.
- **Note on fetchability:** the brief's target URL, `/pages/5-reasons-why-shampoo-bar-3`, now 302-redirects to the Earthling homepage's gamified discount popup ("Unlock FREE gifts and DISCOUNTS") — the original listicle is dead. Substituted with `/pages/lpd1-sbts-scset`, a live listicle/product-lander hybrid found via search, on the same brand and product line.

---

## 9. HubSpot — Product lander (design reference only)

**URL:** https://www.hubspot.com/products/crm
**H1:** "Free CRM Software for Startups & Small Businesses"

- Word count: **2,039** (live DOM; static HTML gave 1,740 — difference is lazy-loaded content) · Images: **37** · H2 count: **5**, H3 count: **42** (a heavily sub-structured, skimmable layout — feature grids and FAQ items each get their own H3).
- **Above the fold:** sticky global nav (logo + nav links + "Get free CRM" CTA) → breadcrumb (Home / Free HubSpot CRM) → eyebrow label "Free HubSpot CRM" → H1 → subhead → primary CTA button "Get free CRM" → "No credit card required" trust line → product screenshot mockup → social proof line "Trusted by over 306,000 customers in more than 135 countries" with a logo carousel.
- **CTAs:** 20 button-like elements; "Get free CRM" is repeated at multiple scroll depths, plus "Learn about premium CRM", "Demo premium CRM", "See the HubSpot setup guide".
- **FAQ: 6 questions** — "What is CRM software?", "What are popular free CRM software features?", "How much does CRM software cost?", "What are the main functions of a CRM system?", "Who uses CRM software?", "What should I look for when choosing a CRM system?"
- **Sticky bar:** sticky top global nav only (logo + nav + CTA), no bottom bar.
- **What to borrow:** the eyebrow + breadcrumb + H1 + trust-line stack above the fold is clean and low-friction; the H3-per-feature density keeps a long page skimmable; the FAQ block doubles as SEO content.
- Exact color/font tokens weren't attributable with confidence from the fetched CSS bundle (HubSpot's CMS ships per-module minified CSS); treat the orange/red CTA color as an observed-not-measured fact.

---

## 10. ClickUp — Product lander (design reference only)

**URL:** https://clickup.com/
**H1 (varies by fetch — see note):** "Software to replace all software" (curl) / "One Workspace built to think and work." (rendered browser)

- Word count: **1,057** (live DOM) / 1,729 (static curl) · Images: **295** (extremely dense — icons, UI screenshots, avatars). H2 count: **11**, including "60% of work is lost in context – and AI is lost without it", "Loved by 5+ million teams, backed by 100+ awards", "#1 most referenced company on G2 reports".
- **Above the fold:** promo utility bar ("NEW: Brain² — the best AI is your AI...") → sticky nav (logo, Brain AI/Product/Solutions/Learn/Pricing/Enterprise, Login, Sign Up) → eyebrow badge → large bold H1 + grey subhead in one block → dual CTA "Get started. It's FREE!" + "Free forever. No credit card." trust line → embedded live product UI screenshot/demo.
- **CTA:** 93 button-like elements site-wide (nav items and feature links inflate this — treat as upper bound, not "93 conversion CTAs"). Primary CTA text "Get started. It's FREE!" repeats at multiple points.
- **Sticky bar:** confirmed via computed style (`position: fixed`/`sticky`) — sticky top nav holding logo + full nav + Login/Sign Up CTA.
- **Proof:** "Loved by 5+ million teams, backed by 100+ awards", "#1 most referenced company on G2 reports", enterprise customer logos, embedded interactive product demo.
- 14 responsive breakpoints found (400/480/500/600/768/820/900/968/1000/1100/1160/1200/1400/1440px) — the most granular of any brand reviewed; worth noting as "design at many more screen widths than a typical DTC page."
- Exact CTA color/radius/height couldn't be attributed to specific CSS rules — ClickUp ships a hashed Next.js CSS-module bundle. Observed from screenshot: black pill button, white text, generous whitespace, large bold sans-serif headline.
- **Note:** headline text differs between the static curl fetch and the live rendered page — both are genuinely observed (likely an A/B test), not a fabrication; listed both above rather than picking one.

---

## Common structure (what most pages share)

1. **Headline block first, always** — H1 (or, on two pages, a misused non-semantic H1) + a one-line subhead, before any proof or CTA.
2. **A CTA appears above the fold on every DTC page** (Grüns, Resilia ×2, Jones Road, Moon Pod, Earthling), but **not** on the two editorial Sun Home guides or the two software landers, where the fold leads with credibility (byline / breadcrumb / trust stat) before the CTA.
3. **A sticky element exists on 8 of 10 pages** — but what it holds varies by category: DTC supplement/beauty pages put a purchase CTA in it (Grüns, Resilia); software pages put full navigation in it (HubSpot, ClickUp); Sun Home's sticky element is pure site nav, no purchase CTA.
4. **Reviews/testimonials appear low on the page, not above the fold**, on every page that has them (Grüns, Earthling, Moon Pod). Star-rating summary lines (not full reviews) are the one proof element that does appear above the fold (Grüns, Earthling).
5. **FAQ sections are common on supplement/wellness pages** (Resilia listicle: 10 Qs, Sun Home guide: 7 Qs, HubSpot: 6 Qs) and **absent on beauty/home-goods listicles** (Jones Road, Moon Pod, Earthling) — FAQ correlates with higher-consideration/ingestible products, not with page format.

## Listicle pattern (Grüns, Jones Road, Moon Pod, Earthling, Sun Home)

- Two distinct sub-styles observed:
  - **True numbered items** (Jones Road, Moon Pod, Earthling's "Why Choose" block, Sun Home's "10 Differentiators"): a number + short benefit-claim heading + a short body (30–150 words depending on brand) + optional micro-CTA.
  - **Thematic pillars, not numbered** (Grüns): single-word/short-phrase section headers (TASTE, CONVENIENCE, FIBER, BENEFITS) instead of "Reason 1/2/3."
- **Per-item micro-CTA is brand-specific, not universal**: Jones Road repeats "FIND YOUR PRODUCTS" after every item; Moon Pod and Earthling have zero per-item CTA and rely on the page-level CTA(s) instead.
- **Heading-hierarchy hygiene is inconsistent** — two of five listicle pages (Jones Road, Moon Pod) emit every heading as `<h1>` with no `<h2>`/`<h3>`. Do not copy this; use real hierarchy in the rebuild (H1 once, H2 per list item).
- Image cadence is heavy: roughly one image every 15–40 words of body copy across the DTC listicles (Grüns ≈120 images/4,975 words, Resilia listicle ≈48/1,434, Moon Pod ≈20/573, Earthling ≈49/670).

## Advertorial pattern (Resilia, Sun Home editorial)

- **Resilia** is the closest to a "classic" advertorial: quote-style headline, explicit **"Sponsored Content"** disclosure, byline with author photo/name/date, narrative arc (problem → hidden cause → consequence → solution → timeline → ingredients → testimonials → CTA), and a **sticky bottom CTA bar** carrying the offer throughout.
- **Sun Home** runs a different, higher-trust variant: no "sponsored" label, but a **transparent evidence-labeling system** (manufacturer-published / independently tested / editorial designation / documented absence) plus a public "Claim Ledger" and "Sources" list, and an honest "Who Should Not Choose Sun Home" section. This is a stronger trust pattern for a considered, expensive purchase (saunas) than Resilia's magazine-style narrative, and is the more directly applicable model for Peak Saunas given the shared category.
- Both use a credentialed byline (author name + title) rather than an anonymous voice.

## Design references (HubSpot, ClickUp — layout, spacing, CTA, typography to borrow)

- **HubSpot:** borrow the above-fold stack order — eyebrow label → breadcrumb → H1 → subhead → single primary CTA → frictionless trust line ("no credit card required") → social-proof stat with logo carousel. Borrow the H3-per-feature density (42 H3s on one page) for keeping a long page skimmable without walls of text. FAQ-as-SEO-content at the bottom (6 Qs) is a pattern worth copying for the listicle rebuild's own FAQ block.
- **ClickUp:** borrow the dual-CTA + trust-line pairing ("Get started. It's FREE!" + "Free forever. No credit card.") directly under the H1, and the embedded live-product-screenshot in place of a static hero photo. Borrow the very granular responsive breakpoint set (14 breakpoints) as a reminder to test the rebuilt template at more than the usual 2–3 widths. Both software pages keep the sticky element to pure navigation + one CTA — no bottom bar, no price/offer stuffed into it — which is a simpler pattern than the DTC sticky-ATC bars and may be worth testing as an alternative for a high-ticket item like a sauna.

## Pages that could not be fetched as originally specified

- **The Earthling Co.** — `theearthlingco.com/pages/5-reasons-why-shampoo-bar-3` (the brief's named target, and the ConvertFlow-referenced example) now redirects to the homepage's gamified discount popup. Substituted with a live listicle/product-lander page on the same brand, `theearthlingco.com/pages/lpd1-sbts-scset`, found via search; flagged inline above.
- **Jones Road Beauty** — `jonesroadbeauty.com/pages/what-the-foundation-5-reasons-why` returned HTTP 200 but rendered only an empty `<h1>` with no body copy (confirmed via rendered-browser check, not just curl). Dropped; used `/pages/5reasons` as the sole Jones Road page instead of the planned two.
- Several other pages (Jones Road `5reasons`, Moon Pod `5-reasons`, Earthling `lpd1-sbts-scset`) are built with Shopify page-builder apps that render content client-side — `curl` alone returned near-empty HTML for these. All figures for those three pages come from a rendered-browser pass (live DOM), not raw HTML, and are noted as such above.
