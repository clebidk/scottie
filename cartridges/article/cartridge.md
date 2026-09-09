# Cartridge: article  (v0.1.0)

Purpose: an editorial piece that a cold Meta reader accepts as a magazine article, then discovers Peak at the end.
Audience temperature: cold. Reader does not know Peak and did not plan to buy a sauna today.

## Structure (in order)
1. Header block: "Advertisement" label, headline (curiosity or contrarian, no product name), dek (1 sentence), byline block, dates.
2. Open (2–3 short paragraphs): the reader's situation, taken from the ad's hook. If the ad speaker is first person, tell it as a customer's story ("a customer told us...") -- never in the author's (Austin's) own first person; see the global voice block.
3. Body: 3–5 H2 sections. Each answers one question the reader would ask next, with 2–3 full paragraphs of 3–4 sentences each (roughly 70 to 110 words per paragraph) -- not a one-paragraph, one-sentence answer. Education only. Product may be mentioned by name once in the body, as an example, no link.
4. Evidence: at least 2 cited facts from facts_pack.verified_claims, cited inline as "(source name, year)" -- never the raw URL; the renderer adds the link in the Sources list.
5. Turn: one H2 that moves from the topic to "what to look for", listing 3–4 criteria that Peak meets. Still no hard sell. At least 1 criterion must carry a product-benefit claim_id (see Rules).
6. Close: 1 paragraph naming Peak, 1 soft CTA link ("See the models" / "Read the specs"). Financing line only if the ad used a price angle.
7. Footer: disclosure paragraph, sources list.

## Rules
- 1,000–1,600 words. Education ≥ 70% of body words.
- Exactly 1 CTA. No sticky bar. No countdowns, no discount language.
- Headline never contains "Peak" or a price.
- No claims outside verified_claims. Health statements cite the study and its population; never promise an outcome for Peak hardware.
- Body sections give generic buyer education ("questions to ask", "what varies between brands") without stating a specific number, spec, or figure for saunas in general -- a number in prose always needs a claim_id, and facts_pack has no generic-industry claims to cite, only this one product's. Save any actual number for a spec you can cite from facts_pack (with its claim_id copied into the sentence).
- Competitor statements only as the speaker's own experience unless sourced.
- The turn_section's "what to look for" criteria must include at least 1 product-benefit claim_id -- what the sauna does or is built with (e.g. medical-grade red light therapy, full-spectrum near/mid/far infrared, US-owned company, free shipping, limited lifetime warranty) from facts_pack.verified_claims -- not the price, shipping-policy, warranty-terms, or returns-policy claim_ids. The run STOPs if this minimum isn't met.
- Images: 2–3 from the asset library, lifestyle over product, referenced by asset_id only -- the renderer derives alt text, never write your own "alt" field. No before/after, no clinical settings.
- Voice: plain, specific, second person or first person. No exclamation marks. No "game-changer", "unlock", "elevate", "journey".

## From the ad
Hook and angle → headline and open. Objections raised in the ad → body sections. Speaker's story → open, attributed to "a customer" (or facts_pack.speaker_name), never told in the author's own first person.

## From facts_pack
Specs, price, financing, warranty, verified studies, reviews summary.
