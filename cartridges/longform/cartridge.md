# Cartridge: longform  (v0.1.0)

Purpose: a classic single-goal landing page for high-intent traffic. Hero → problem → how it works → specs and proof → social proof → FAQ → sticky CTA.
Audience temperature: warm to hot.

## Structure (in order)
1. Header: the tenant's optional disclosure label (`disclosure_label`; none when unset), the tenant logo, sticky CTA bar (appears after the hero scrolls out).
2. Hero: headline from the ad angle, subhead, an optional 3-stat proof row (`hero.proof_stats`, see Rules) directly under the subhead, hero image, financing line if the ad used price. CTA is `cta_text`/`cta_url` (top-level, see Rules) -- exactly one of schema.json's `allowed_cta_texts`, e.g. "See pricing", or a "Shop the ..." option; do not add a separate cta object here.
3. Problem: H2 + 2–3 paragraphs on the reader's problem as the ad frames it, each paragraph 3–5 full sentences (roughly 60 to 100 words) -- not a one-line summary. Objections from the ad become the problem statements.
4. How it works: H2 + 3 steps or 3 feature blocks, each with an image slot and step text of 3–4 full sentences (roughly 60 to 90 words), not a single-sentence caption. Technical facts from facts_pack.specs only. At least 3 steps combined must carry a product-benefit claim_id (see Rules).
5. Specs and proof: table (6–10 rows) + 2–3 verified claims with citations, each proof point 2–3 full sentences (roughly 40 to 60 words).
6. Social proof: review summary (count, rating) from verified_claims; up to 3 short quotes only if they exist in facts_pack.reviews_summary verbatim. Never invent quotes.
7. FAQ: 5–7 questions. Questions come from ad objections and common buyer questions; answers from facts_pack, each a real 2–3 sentence answer (roughly 50 to 80 words), not a one-line reply. Rendered with FAQPage JSON-LD.
8. Final CTA block: headline, financing line, warranty line. Same `cta_text`/`cta_url` as the hero -- the renderer places it, do not repeat it here.
9. Footer: byline block (author, verifier, dates), disclosure paragraph, sources.

## Rules
- One CTA phrase per page: `cta_text` is a single phrase, chosen once -- it may repeat verbatim in the hero, the sticky bar, and the final block, but never as two different phrases, and never a second, differently-worded CTA/offer anywhere on the page.
- Anti-pattern: never render more than one distinct CTA or offer card on the page (the "Roman hub" anti-pattern -- multiple competing offers on one page). There is exactly one CTA destination (`cta_url`) and it is reused verbatim everywhere the CTA appears; a second offer card, discount box, or alternate CTA url anywhere on the page fails the gate.
- Proof row (`hero.proof_stats`, optional): 2–3 stats, each `{value, label, claim_ids}`, directly under the hero subhead, before the first body section -- verified claims only (e.g. live review rating/count, warranty term, free shipping). Every stat needs at least 1 claim_id; omit the row entirely rather than invent or pad a stat with nothing to cite. Layout: the row renders through a registered layout block; to choose a variant set top-level `"blocks": {"proof": "<block-id>"}` to one of schema.json's `block_slots.proof.blocks` ids -- omit `"blocks"` to render the default. Blocks are layout only; they never change what the copy may say.
- 800–1,400 words. Return top-level `cta_text` and `cta_url` exactly once -- the renderer reuses them verbatim in the hero, sticky bar, and final block. `cta_text` must be exactly one of schema.json's `allowed_cta_texts` with `{short_name}`/`{model_name}` filled in with the product's actual short_name/model name (e.g. "Shop the Fuji") -- never invent different wording (the gate rejects anything else and the run repairs/STOPs).
- At least 3 of how_it_works.steps' claim_ids must be product-benefit claims -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Every spec, number, and quote maps to facts_pack. Health statements cite studies.
- Wherever `financing_line` appears (hero or final_cta), its text must be exactly the allowed financing sentence given in the prompt's Financing rule -- verbatim, no monthly figure, APR, or lender name typed in by hand.
- Images: 4–6 from the asset library, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field.
- Voice: confident, specific, second person. No hype words, no fake urgency.
- Attributed lines (`attributed_to_customer: true`): only the ad speaker's own words from ad_brief.transcript_or_text -- prefer a direct quote in quotation marks, word for word; a paraphrase adds nothing she did not say (no "almost", "finally", "gave up", "never", "best", "only", no outcome she did not state). Frame it "In the ad, she says ..." or "As one shopper put it, ..." -- never "told us", and never "customer"/"buyer"/"owner" unless the tenant marks its ad speaker as a verified customer (see the global voice block). The gate checks every attributed line against the transcript.

## From the ad
Angle → hero. Objections → problem and FAQ. Features shown → how-it-works blocks.

## From facts_pack
Everything: specs, price, financing, warranty, shipping, verified claims, reviews summary, assets.

## Design-skills pack (optional)
This cartridge is the skill's type-B long-form landing page. Optional `tagline.lines` (two or more) renders after the hero through the `tagline-reveal` block -- a benefit statement, not a heading. Soft-warned when omitted. Copy still goes through the claims gate. The skill's raw visual tokens (fonts, Tailwind scale, hex palette, 680 px cap, JS word-reveals) do not apply; see `harness/design_skills/`.
