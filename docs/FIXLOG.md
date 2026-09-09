# Fix log

## Cycle 2 queue (from operator review of run 20260909-1627, 2026-09-09)
1. Byline copy is lifted verbatim from the buyer's guide ("competitor price... this ranking... corrections that favor a competitor"). Replace with page-neutral text: Written by / Expert contributor line, Published/Updated, and a 2-sentence About the author that fits a landing page. Keep the markup and classes.
2. First-person integrity: when ad_brief.speaker_pov is first_person and the page author is Austin, the writer must NOT put the speaker story in the author's voice ("I ran into this..."). Attribute it: "One customer told us..." or a quoted line credited to the ad speaker only if her name and consent are on file (config.speaker_name, default null -> anonymous "a customer"). Add to the global voice block, to each rubric.md, and to REVIEW.md as a checklist item.
3. Product page: an empty "Reviews" heading renders when no review data exists; hide the block when empty.
4. Article: verify the disclosure paragraph is inside the rendered page body (page text capture did not show it).
5. Compare-at price: config-gated in cycle 1; confirm it is off by default in all three templates.

## Cycle 2 additions (Caleb, 2026-09-09: "stay away from ever mentioning EMF")
6. EMF is absolute. The gate must scan the whole rendered HTML: body text, headings, alt text, title, meta description, JSON-LD, image filenames, and link text. Any match on "emf" or "electromagnetic" (case-insensitive) fails the page.
7. Ingest: ad claims or features that mention EMF are dropped from ad_brief with a log line "dropped EMF claim: <text>" instead of stopping the run. The page simply never covers that angle.
8. Product URLs: the Shopify handle for Fuji contains "near-zero-emf". Pages link to the product URL as-is for now (the handle is Caleb's call on the Shopify side); log a warning per run listing any URL that contains "emf" so it stays visible in REVIEW.md.
