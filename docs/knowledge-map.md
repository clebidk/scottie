# g Brain Knowledge Map — for Peak Saunas Ad-to-Landing-Page Generator

Read-only survey of what the g Brain MCP holds that generated pages can ground on. Customer/order/ticket/conversation/person pages exist in force but are excluded per instructions — never source public copy from them.

## 1. Product catalog
`spec/*` (17 pages) is authoritative for hard specs; `spec/all-models` is the master table. `product/*` (12 pages) are "hub" pages — verified specs plus support/sales-derived FAQs and objections, good for color, secondary to `spec/*` for numbers.

**Active (9):** indoor — Rainier, Shasta, Everest, Fuji (best-seller), Denali, Matterhorn; outdoor — Patagonia, El Capitan, Kilimanjaro. **Discontinued, don't feature:** Crown, Olympus, Aspen. Indoor Kilimanjaro/El Capitan are support-only. All active models: full-spectrum IR, medical-grade RLT built in, EMF <3mG seated, Lifetime warranty (=7yr residential, labor never covered), Cedar or Hemlock, Affirm/Shop Pay financing (0% APR), HSA/FSA via TrueMed, always-free shipping in a PeakGuard crate from Ontario CA (ships 2–4 days, arrives 5–14).
**No static prices anywhere** — every field says "PULL_LIVE"/"see peaksaunas.com"; fetch live at generation time, never hardcode. Discount codes churn (PEAK200↔PEAK250) — same rule.
Best slugs: `spec/all-models`, `spec/fuji`, `product/fuji`, `policy/warranty`, `policy/shipping-and-delivery`, `policy/financing-and-payment`.

## 2. Brand
No finalized brand book. `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026` (draft) has mission, 5 personality traits, "NOT a wellness-lifestyle brand" positioning, founder origin story. `media/books/scientific-advertising-personalized` gives the most concrete style-lock: charcoal #3B3933, Harmonia Sans/DM Sans, "longevity-clinician register," Jeff as signed-voice narrator, 7 brand tokens. A May 2026 meeting shows visual identity (colors/fonts/logo) still being commissioned — treat as provisional. No logo assets found. Gap: no single source-of-truth doc.

## 3. Verified claims and studies
`competitors/overview` is the claims allowlist: only medical-grade red light, free shipping, 360° full spectrum, US-owned, Lifetime warranty are pre-cleared — explicitly avoid an EMF-led angle. `kb/auto-fills/emf-testing-policy` (canonical): internal-only EMF testing, avg 3mG, **no third-party/accredited-lab certification exists** — never claim third-party-tested. Recent atoms show customers repeatedly challenging the EMF claim for lack of public proof (unresolved). Two ambiguous atoms reference a company "admitting" EMF claims don't match tests / a "doctored" video — unclear if about Peak or a competitor; don't use either way without confirming.
**Reviews figure — no single number.** Conflicting: "4,000+ verified customer reviews" (dated fact, June 2026), aspirational "10,000+ members"/order-milestone copy, and a landing-page template default "10,000+ customers, 4.9★" that's an unsourced placeholder. `kb/reviews-policy`: Judge.me is the sole customer-facing review link — pull count live; never use Google review links externally.

## 4. Asset library
No indexed library page. Real assets sit in ad-hoc Drive links pasted into Discord (`chat/discord-paid-ads-*`) — a "MASTER Content Folder," and PMAX batches (50+ shots: Kilimanjaro, Fuji, El Capitan, Patagonia, Matterhorn, interiors). JPEGs Mafia (vendor, §6) drops weekly creative to its own folder. `index/sources/drive/2026-06` indexes many relevant Drive doc titles by filename only (not fully ingested). Gap: no current, canonical Drive index.

## 5. Competitors
`competitors/overview` plus `competitors/clearlight|sunlighten|jnh-lifestyles|heavenly-heat|relaxe|good-health-saunas`. Guardrail: never use "Sunlighten" in paid creative (trademark); "Sun Home" must read "Compare Peak vs. Sun." Sunray is explicitly not a Peak brand. `outline/customer-support/peak-saunas-training-hub-v3` (Module 14) has a customer-facing competitor script.

## 6. Campaigns
No `campaign`-type pages resolve via list_pages despite the brief's 504-page count — flag as an anomaly for ops. Real campaign detail is scattered across `chat/discord-paid-ads-*` and `outline/private/*-brief-*`. `companies/jpegs-mafia` documents that vendor's weekly-drop process (5 hardcoded headlines only). Could not find distinct "Hidden Costs / Price Comparison / Product Features" campaign pages — only a referenced video ("The Hidden Costs of Owning a Sauna") and scattered price-comparison ad copy. "v2 Videos whitelist" wasn't found under that name — likely lives outside gbrain.

## 7. Existing landing pages / advertorials
`ad-generator/landing-pages/readme` documents a working Shopify page generator (trust-badge template values are placeholders, not verified). Two live examples surfaced in chat logs: `peaksaunas.com/pages/br-expert-sauna-comparison` (authority advertorial vs. Clearlight/Sunlighten) and `.../br-5-reasons-sauna-owners-switch` (listicle). A `2026-02-17` daily log references a fuller "Advertorial Network" (2 templates, 3 look-independent domains) — no performance data found.

## 8. Programmatic access
Base URL `https://gbrain.peaksaunasteam.com/mcp`. Primary auth: OAuth 2.1 + PKCE (dynamic client registration); a static `gbrain_at_`-prefixed bearer token is also accepted as `Authorization: Bearer`. A `gbrain` CLI exposes `get/put <slug>`, `timeline-add`, and an `auth` subgroup for minting/rotating tokens. **Location, not value:** stored as the header on the `gbrain-scoped` entry in `~/.claude.json`, plus a bootstrap/admin token in a prod systemd env drop-in — logs flag that drop-in as world-readable and due for rotation. Loop in the ops-box owner before a server script depends on it.

## Gaps to ask Caleb
1. Which review count/rating is safe to publish?
2. Is the EMF "claims don't match tests" pair about Peak or a competitor?
3. Who maintains a live Drive asset index?
4. Why don't campaign-type pages list despite the stated count — ingest gap?
5. Is there a finalized brand identity newer than the May 2026 draft?
6. Confirm live pricing/discount-pull method before the generator goes live.
7. Who rotates the flagged gbrain tokens before a server script relies on them?

## Slug appendix
`spec/all-models` · `spec/fuji` · `spec/denali` · `spec/el-capitan` · `spec/everest` · `spec/kilimanjaro` · `spec/matterhorn` · `spec/patagonia` · `spec/rainier` · `spec/shasta` · `spec/aspen` (discontinued) · `spec/crown` (discontinued) · `spec/olympus` (discontinued) · `spec/mini` · `product/fuji` · `product/denali` · `product/el-capitan` · `product/everest` · `product/kilimanjaro` · `product/matterhorn` · `product/patagonia` · `product/rainier` · `product/shasta` · `policy/warranty` · `policy/shipping-and-delivery` · `policy/financing-and-payment` · `kb/reviews-policy` · `kb/auto-fills/emf-testing-policy` · `competitors/overview` · `competitors/clearlight` · `competitors/sunlighten` · `competitors/jnh-lifestyles` · `competitors/heavenly-heat` · `competitors/relaxe` · `competitors/good-health-saunas` · `companies/peak-saunas` · `companies/jpegs-mafia` · `outline/sales/peak-saunas-brand-guide-draft-v1-march-25-2026-11733b47` · `media/books/scientific-advertising-personalized` · `outline/customer-support/peak-saunas-training-hub-v3-52ad71aa` · `ad-generator/landing-pages/readme` · `outline/private/youtube-ads-strategy-peak-saunas-apr-30-2026-1abe22a0` · `outline/private/google-ads-creative-brief-may-12-2026-2c480f9a` · `index/sources/drive/2026-06`
