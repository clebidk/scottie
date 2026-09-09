# Guardrails — do/don't rules pulled from g Brain

Source pages read (see docs/knowledge-map.md for the full survey): `competitors/overview`, `kb/auto-fills/emf-testing-policy`, `kb/reviews-policy`, `policy/warranty`, `policy/shipping-and-delivery`, `policy/financing-and-payment`, `spec/all-models`, `spec/fuji`, `spec/everest`, `ad-generator/landing-pages/readme`, `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026-11733b47`. Every rule below is a direct quote or close paraphrase — cite the slug when reusing.

## Competitor naming (trademark)
> "do not use a competitor's name in *paid ad creative* — Google won't run 'Sunlighten' ads, and 'Sun Home' must be phrased 'Compare Peak vs. Sun'."
— `competitors/overview`

> "We do NOT carry Sunray brand products... If a customer asks about a Sunray model, inform them we only carry Peak Saunas brand products and redirect to our comparable offerings."
— `competitors/overview`

## EMF-led angles
> "Do NOT lead with an EMF/'near-zero EMF' angle."
— `competitors/overview`

> "Peak does not hold third-party or accredited-laboratory EMF certifications... No ISO/ANSI-accredited lab test report exists." "Do NOT give a 'pacemaker safe' assurance without Austin's approval."
— `kb/auto-fills/emf-testing-policy`

Treat the EMF figure (avg 3 mG, internal testing only) as supporting/secondary copy only — never a headline claim, never framed as third-party-verified.

## Third-party testing
> No accredited-lab EMF report exists — "Do NOT... claim third-party-tested" (implied by the absence: Peak "does not hold third-party or accredited-laboratory EMF certifications").
— `kb/auto-fills/emf-testing-policy`

## Review links
> "Do NOT use Google review links in any customer-facing context." Judge.me (https://judge.me/reviews/stores/peaksaunas.com) is the sole primary, customer-facing link. TrustPilot is secondary and "cannot be directly solicited. Mention only if customer asks."
— `kb/reviews-policy`

No specific review count/star rating is cleared for reuse — pull whatever is published live; do not hardcode a number (per `docs/knowledge-map.md`, the count is unresolved/conflicting across sources: 4,000+, 10,000+ members, and a template default of 10,000+/4.9★ are all different, uncorroborated figures).

## Discount codes
> "do NOT offer any discount beyond PEAK200 — route to the team (Becca) for anything else."
— `competitors/overview`

> "The only discount that exists is PEAK200 ($200 off)." "No cash/pay-in-full discount — the price is the same regardless of payment method. (Ignore any older note offering 3–4% off for cash.)"
— `policy/financing-and-payment`

Discount codes are known to churn (PEAK200 ↔ PEAK250 per `docs/knowledge-map.md`) — never hardcode a code into generated copy; treat it as pull-live like price.

## Financing providers
> "The only two providers are Affirm and Shop Pay Installments. We do NOT offer Klarna, Sezzle, Afterpay, Upgrade, or any other financing provider — never tell a customer we do."
— `policy/financing-and-payment`

**Live-site conflict found during this pass:** the live Fuji 2-Person product page (fetched 2026-09-09) shows a financing logo with alt text "Bread Pay" (twice), which is not one of the two approved providers and is exactly the kind of provider this rule forbids naming. Seeded as `gbrain-financing-lender-discrepancy` in `claims/seed-from-gbrain.json` with status `needs-caleb` — do not name any specific lender in generated copy until this is resolved.

## Discontinued models — must not be featured
`docs/knowledge-map.md`: "**Active (9):** indoor — Rainier, Shasta, Everest, Fuji (best-seller), Denali, Matterhorn; outdoor — Patagonia, El Capitan, Kilimanjaro. **Discontinued, don't feature:** Crown, Olympus, Aspen."

Caveat: this exact "don't feature" instruction is not restated verbatim inside the 11 gbrain pages read for this task — it's stated in `docs/knowledge-map.md` (itself gbrain-derived) and corroborated by staleness signals in `spec/all-models`, where Crown/Aspen/Olympus are the only three rows still pointing at a legacy `PULL_LIVE: python3 /root/.openclaw/workspace/scripts/get_shopify_price.py` script path instead of the current "see peaksaunas.com" placeholder every active model uses. Treat Crown/Olympus/Aspen as excluded from generated pages; flag to Caleb if a cartridge needs to reference them.

## Warranty phrasing
> "Always say 'limited lifetime warranty' — never just 'lifetime warranty'."
— `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026-11733b47` (Appendix)

> "'Lifetime' = the expected life of the component, defined as 7 years under normal residential use (state it this way — do NOT say '10-year')."
— `policy/warranty`

> "Labor of any kind — diagnosis, service, or installation. The warranty covers parts only; labor is never included on any component."
— `policy/warranty`

The Red Light Therapy panel was recently corrected off the lifetime tier: "Red light therapy (RLT) panel | 3 years (Austin, 2026-09-07 — NOT the lifetime tier)" — do not imply the RLT panel carries the same lifetime coverage as the structure/heating elements.
— `policy/warranty`

## Comparison tone (no disparagement)
> "compare on **facts**, never disparage."
— `competitors/overview`

> Brand guide Pillar 4 (The Comparison): "We win on the merits — we don't need to attack." Customer-quote standard cited as the bar: "In the comparison, not once did they have anything negative to say about the other brand. I made the decision right then to choose Peak." — Josh S.
— `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026-11733b47`

## Brand voice — never publish
From the brand guide's explicit "Content We Never Publish" list: discount urgency ("Sale ends Sunday!"), vague/unsubstantiated health claims that wouldn't pass FTC scrutiny, anything sounding like an influencer who doesn't use the product, stock photography, hype without specificity. Also: "No countdown timers on an $8K purchase. No 'you're running out of time on your health' panic copy," and "We don't lead with price. We don't use sale urgency as a hook."
— `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026-11733b47`

## Unauthorized / lower-price retailers
> "If a customer finds a lower price on another site, it's likely an unauthorized retailer... They can't honor our warranty. Always buy direct from peaksaunas.com."
— `competitors/overview`
