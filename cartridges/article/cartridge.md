# Cartridge: article  (v0.1.0)

Purpose: an editorial piece that a cold Meta reader accepts as a magazine article, then discovers {{ tenant.name }} at the end.
Audience temperature: cold. Reader does not know {{ tenant.name }} and did not plan to buy today.

## Structure (in order)
1. Header block: "Advertisement" label, headline (curiosity or contrarian, no product name), dek (1 sentence), byline block, dates.
2. Open (2–3 short paragraphs): the reader's situation, taken from the ad's hook. If the ad speaker is first person, tell it as a customer's story ("a customer told us...") -- never in the author's ({{ authors.author.name }}'s) own first person; see the global voice block.
3. Body: 3–5 H2 sections. Each answers one question the reader would ask next, with 2–3 full paragraphs of 3–4 sentences each (roughly 70 to 110 words per paragraph) -- not a one-paragraph, one-sentence answer. Education only. Never name {{ tenant.name }}, the product, or facts_pack.product.short_name anywhere in the body -- keep every section brand-free and product-name-free (warm-up window). Every body section must carry proof: at least one paragraph in each section needs a claim_id, or an attributed customer statement (attributed_to_customer: true, phrased as the customer's own words) -- a section of pure unsupported opinion is flagged in review (see Rules).
4. Evidence: at least 2 cited facts from facts_pack.verified_claims, cited inline as "(source name, year)" -- never the raw URL; the renderer adds the link in the Sources list.
5. Why the usual alternatives fall short: one H2 + 2–3 paragraphs (3–4 sentences each) on why the reader's obvious alternatives -- doing nothing, a generic/other brand, a DIY workaround -- don't actually solve the problem the open laid out. Objection-driven, still education, still no company or product name.
6. How it works: one H2 + 2–3 paragraphs (3–4 sentences each) walking through the mechanism or process that actually solves the problem (still generic, not yet naming any company or product) -- this is what sets up the turn to "what to look for" next.
7. Turn: one H2 that moves from the topic to "what to look for", listing 3–4 buyer criteria a careful shopper should insist on. Still no hard sell and still no company or product name in the heading, intro, or criteria text -- attach product-benefit claim_ids to criteria that the eventual recommendation will satisfy, but do not name {{ tenant.name }} or the product yet (see Rules).
8. Close: the first and only place that may name {{ tenant.name }} or the product (use facts_pack.product.short_name here, with its claim_id). 1 paragraph naming {{ tenant.name }}, 1 soft CTA link ("See the models" / "Read the specs"). Financing line only if the ad used a price angle -- set `page.financing_line` to exactly the allowed financing sentence given in the prompt's Financing rule, as its own sentence. Never paraphrase financing anywhere in the piece (open, body, or close) -- if you mention financing at all, use that exact sentence verbatim; do not describe it in your own words.
9. Footer: disclosure paragraph, sources list.

## Rules
- 1,000–1,600 words. Education ≥ 70% of body words.
- Warm-up window (numeric gate): the harness counts reading-order words and fails (or warns) if pricing, the CTA, {{ tenant.name }}, or any product / short_name appears before word {{ tenant.cartridges.article.warmup_window_words }}. In practice that means open, body_sections, alternatives_section, how_it_works_section, and turn_section stay brand-free and product-name-free; name the company and product only in close (close is past the window on a full-length piece). The reader earns the offer; the offer doesn't lead. This rule overrides any general "mention short_name on first section mention" voice guidance for this cartridge. Reference articles in the prompt are voice/structure only -- never copy their brand-placement timing.
- Exactly 1 CTA. No sticky bar. No countdowns, no discount language.
- Headline formula: a number+outcome (e.g. "5 Ways...", "3 Questions...") or a curiosity hook, 8 to 14 words, never the company name or a price. Name the specific audience directly when ad_brief.audience is non-empty (e.g. "7 Questions Busy Parents Should Ask Before Buying a Sauna") -- otherwise address the reader generally, never invent an audience the ad didn't name.
- No fabricated or credentialed author personas: every byline is {{ authors.author.name }} (renderer-injected, never written by you) -- never invent a different named "expert," doctor, researcher, or other credentialed persona to narrate or be quoted in the piece. A real customer's own attributed words are fine; a made-up authority is not.
- No claims outside verified_claims. Health statements cite the study and its population; never promise an outcome for {{ tenant.name }} hardware.
- Body sections give generic buyer education ("questions to ask", "what varies between brands") without stating a specific number, spec, or figure for saunas in general -- a number in prose always needs a claim_id, and facts_pack has no generic-industry claims to cite, only this one product's. Save any actual number for a spec you can cite from facts_pack (with its claim_id copied into the sentence).
- Competitor statements only as the speaker's own experience unless sourced.
- The turn_section's "what to look for" criteria must include at least 1 product-benefit claim_id -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Images: 2–3 from the asset library, lifestyle over product, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field. No before/after, no clinical settings.
- Voice: plain, specific, second person or first person. No exclamation marks. No "game-changer", "unlock", "elevate", "journey".

## From the ad
Hook and angle → headline and open. Objections raised in the ad → body sections. Speaker's story → open, attributed to "a customer" (or facts_pack.speaker_name), never told in the author's own first person.

## From facts_pack
Specs, price, financing, warranty, verified studies, reviews summary.

## Design-skills pack
The landing-page skill's type-A/B skeleton does **not** apply here. This cartridge is the editorial warm-up (brand and CTA delayed to the close). A mid-page tagline-reveal would name the offer too early; do not add one. The pack's copy tells (no filler, no leftover AI cliches, no dead '#' links) still apply -- they are page-level hard gates on every cartridge.
