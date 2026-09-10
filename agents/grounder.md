---
name: grounder
purpose: Merge the ad brief with the tenant's verified claims and live product data into a facts pack.
inputs:
  - ad_brief.json
  - claims/verified.json
  - claims/products.json
  - live product price
  - product page content
outputs:
  - facts_pack.json
model: claude-sonnet-5
---

## What it does
Reads ad_brief.json plus the tenant's claims store, fetches the current
product price, and pulls claims implied by the live product page. Combines
all of it into one facts pack the writer and claims gate both read.

## Rules
- facts_pack.json must contain: product, specs, warranty, shipping, returns,
  reviews_summary, verified_claims, assets, speaker_name,
  digit_exempt_terms.
- verified_claims is the union of claims/verified.json and any claim the
  live product page independently supports — never invent a claim that
  isn't backed by one of those sources.
- Price and specs come from the live fetch, not from memory or the brief.
- Does not decide whether the ad's claims pass; it only assembles the
  ground truth the claims gate checks against.
- Any EMF-related content in the claims store or product page is excluded
  from verified_claims.

## Done when
facts_pack.json exists, matches the key list above, and every entry in
verified_claims cites either claims/verified.json or the live product page.
