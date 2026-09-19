# Fix log

> Paths in entries before 2026-09-10 predate the 2026-09-10 restructure: `adv/`
> is now `harness/`, and `claims/`, `brand/`, `fixtures/`, `out/`, `runs/` are now
> under `tenants/peak-saunas/`. See `docs/RESTRUCTURE-2026-09-10.md`.

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

## Cycle 3 (operator review of run 20260909-1822-hidden-costs-v2, 2026-09-09)

1. **Sources list dedup + labels.** `render.source_label()` and the new `render.build_sources_list()` collapse the Sources list to one line per distinct source URL (several claims -- specs, price -- shared the Fuji product URL and used to print 7+ near-duplicate lines) and show a short label ("Peak Saunas – Fuji product page", "Peak Saunas – Warranty", "Peak Saunas – Shipping policy", "Peak Saunas – Refund policy", "Judge.me reviews for Peak Saunas") instead of the claim's full text. `adv/templates/base.html`'s Sources `<li>` no longer prints `s.text` at all -- only the label as link text, URL only in `href`. Claim texts still appear in REVIEW.md's own "Claims used" section, unchanged. `adv/reviews.py`'s `reviews-live` claim is now sourced to Judge.me itself (`JUDGEME_STORE_URL`) instead of the product page, so it gets its own line instead of colliding with the product page's.
2. **Price formatting.** New `adv/prices.py:format_price()` renders "$8,250" (comma thousands separator, no ".00" unless cents are non-zero, e.g. "$8,250.50"). Used in `build_live_price_claims`; the 11 static `price-*` entries in `claims/verified.json` were reformatted the same way. `write.py`'s global voice block now tells the writer to copy a dollar figure verbatim from the claim's own text rather than reformatting it.
3. **Product substance.** `claims/seed-from-gbrain.json`'s five cleared allowlist claims (medical-grade red light therapy, full-spectrum infrared, US-owned, free shipping, limited lifetime warranty) were already merged into `claims/verified.json` with sources, but `ground.py`'s `facts_for()` never surfaced them into `facts_pack` -- `BENEFIT_ALLOWLIST_IDS` fixes that gap. New gate `claims.find_benefit_claim_shortfall()` (wired into `gate_page_json`) requires >= 3 product-benefit claim_ids (category spec/benefit/trust, excluding price/shipping-policy/warranty-terms/returns-policy by id) in product-page's `proof_bullets` and longform's `how_it_works.steps`, and >= 1 in article's `turn_section.criteria`; the run STOPs otherwise. Cartridge schemas/cartridge.md files document the new minimum.
4. **CTA consistency.** longform and product-page schemas now have a single top-level `cta_text`/`cta_url`, filled once by the writer and reused by the template in every CTA location (hero, sticky bar, final block for longform; hero + repeat for product-page) -- the old per-block `hero.cta`/`repeat_cta`/`final_cta.cta` objects are gone, so there's nothing left to drift. Article already had exactly one CTA and was left alone. `_NON_PROSE_KEYS` in both `claims.py` (EMF/forbidden-term scan) and `cli.py` (word count) picked up `cta_url` alongside `url`, since the real Fuji product URL contains "near-zero-emf" and would otherwise have tripped the forbidden-term gate now that it's a top-level key instead of nested under a literal `"url"` key.
5. **Image alt text.** `render.asset_alt()` derives alt text from the asset's `kind` + the product's `short_name` (e.g. "Peak Fuji 2-Person Infrared Sauna – product photo") -- Drive asset titles are raw camera filenames (`_MG_3988.JPG`), not descriptions, so they're not used. `render_page()` overwrites every asset's `alt` before templating; every cartridge template now reads `asset.alt` only, never `img.alt`. Schemas no longer have an `alt` property on any image object -- the writer picks `asset_id` only.
6. **Disclosure text.** Dropped "and financing estimates" (no estimates exist). New text in `adv/templates/base.html`: "This page is an advertisement published by Peak Saunas, which sells the products described. Every specific claim on this page is sourced; see Sources below. Prices were current as of the publish date above and may have changed since."
7. **Word budget (soft check).** `cli.write_review_md()` adds a `WARNING:` line under a cartridge's word count in REVIEW.md when product-page's main content falls outside 250-500 words -- never a run failure.
8. **`adv review`.** New `cli.cmd_review()` writes one self-contained `<cartridge>-review.html` per cartridge into the run dir, with every `src="assets/...\"` image inlined as a `data:` URI (regex-based, no HTML parser) -- for sending to Caleb without a folder of loose images.
9. **`make review`.** New Makefile target: `make review RUN=<run-id> [OUT=<path>]` runs `adv review` on prod via ssh, then rsyncs back only the generated `*-review.html` files to `OUT` (default: this operator's Mac scratchpad, `.../scratchpad/review`).

## Cycle 4 (reliability -- two of three cycle-3 real runs STOPPED on writer non-compliance; the one that passed shipped under-length pages and the literal cartridge-rules CTA example)

1. **Writer repair loop.** `cli.write_and_gate_page()` wraps `write.write_page()`: after writing, it runs every page-level gate check (`cli.check_page_gates()` = `claims.gate_page_json()`'s checks + the two new hard checks below) and, on failure, calls the writer again with the *same* system prompt plus a `cli.build_revision_note()` "REVISION REQUIRED" block listing each failure's path/issue/text verbatim. Up to `MAX_REPAIR_ATTEMPTS = 2` repairs (3 attempts total); every attempt is a real `write_page()` call and counts against the run's existing budget caps (12 calls / 150k tokens -- unchanged). Only once every attempt has failed does it raise `ClaimsGateFailure(f"page_json:{cartridge_name}", ...)` (same STOP path as before, `unmatched_claims.json` + exit 2) -- with `.attempts` attached (the failure list per attempt) for logging. The ad-claims gate (`gate_ad_brief_claims`, unverified claims in the ad itself) is untouched: still an immediate STOP, no retry. Every attempt (pass or fail) is logged via `log.event()`.
2. **Word range, enforced.** `write.parse_word_range()` parses each cartridge's own "N-M words" line out of its `cartridge.md` (regex, matches the em dash or hyphen), once; `cli.find_word_range_violation()` uses the same parsed range plus the existing `count_words()` to produce a gate-shaped problem (`"Body is X words; required N-M. Expand/trim sections..."`) that feeds the repair loop above. `write_page()` also receives the parsed range and states it in the system prompt as a hard instruction. Fix cycle 3 item 7's soft `WARNING:` line in `write_review_md()` (product-page only, same 250-500 bounds) is now redundant with the hard gate and was removed.
3. **CTA allowlist.** Each cartridge's `schema.json` gets a top-level `allowed_cta_texts` (article: fixed strings; longform/product-page: `{short_name}` templates). `write.resolve_allowed_cta_texts()` substitutes the product's real `short_name` once per run; the resolved, concrete list is both stated to the writer as a hard instruction and checked by `cli.find_cta_violation()` (`cli.get_cta_text()` reads `page.cta.text` for article, `page.cta_text` for the other two) -- anything else feeds the repair loop. `cartridges/product-page/cartridge.md` and `cartridges/longform/cartridge.md`'s CTA lines no longer say the ambiguous "Buy / Shop <model>"; they point at the schema's concrete `allowed_cta_texts`.
4. **Vocabulary, consolidated.** New `adv/vocab.py`: `ALWAYS_FORBIDDEN_TERMS` (now also `electromagnetic`, the hype words `game-changer`/`unlock`/`elevate`/`journey`/`revolutionary`/`unleash`, and `!` for exclamation marks -- previously prompt-only guidance, never gated), `FORBIDDEN_LENDER_NAMES`, and `TRIGGER_WORDS` (`reviews` etc. -- needs a claim_id, unchanged behavior). `claims.py` imports these instead of defining its own copies; `write.py`'s `GLOBAL_VOICE_BLOCK` generates the corresponding prompt sentences from the same lists, so a word added to `vocab.py` is enforced by the gate and told to the writer in the same edit.
5. **Run summary.** `write_review_md()` appends a "Gate history" table (cartridge / attempts / failures per attempt / result) built from the repair loop's per-cartridge attempt lists. `log.RunLog.result()` appends a final `runs/<id>.log` line, `run_result: PASS|STOP attempts=<n> repairs=<m>`, summed across whatever cartridges were processed before the run ended (0/0 for a STOP before any cartridge write, e.g. the ad-claims gate).
6. **Tests.** New `tests/test_repair_loop.py`: a fake client returning one failing page then a passing page (2 calls, PASS, revision note contains the failure); `MAX_REPAIR_ATTEMPTS + 1` failing responses (STOP, stage `page_json:article`, never a 4th call); `parse_word_range()` against the three real `cartridge.md` files; `find_word_range_violation()` in/out of range and with no range; `resolve_allowed_cta_texts()`/`get_cta_text()`/`find_cta_violation()` for both the nested (article) and flat (product-page/longform) CTA shapes. `tests/test_render.py`'s shared `ARTICLE_PAGE`/`LONGFORM_PAGE`/`PRODUCT_PAGE_PAGE` fixtures (reused end-to-end by `tests/test_cli_run.py`'s `adv run` dry runs) were padded with plain, claim-id-free filler paragraphs to real cartridge-length word counts and given an allowlisted CTA, since they now go through the hard word-range/CTA gates too.

## Cycle 5 (operator review of run 20260909-2021-hidden-costs-v2, 2026-09-09)

1. **Writer prompt.** A leaked claim id was printed twice in visible prose: "the Peak Fuji 2-Person Infrared Sauna (spec-fuji-capacity), which is priced at $8,250". `write.py`'s `GLOBAL_VOICE_BLOCK` now states outright, next to the existing URL-citation rule: "Claim ids never appear in any text field. Cite in prose only as (source name, year). Put ids only in claim_ids." Root cause: the short_name paragraph literally spelled out the id format (`"spec-<product>-capacity"`) right next to the instruction to mention the short_name -- the exact context every leak occurred in. That paragraph, and the general claim_ids paragraph above it, each got a concrete WRONG/RIGHT example matching the two real leaked sentences from verification (`(spec-fuji-capacity)`, `(price-fuji)`, `(spec-fuji-infrared-wavelength-range)`) instead of just an abstract rule.
2. **Page.json gate.** New `claims.find_leaked_claim_ids()` scans every prose string in page.json (same structural-field exemptions as `find_forbidden_terms`) for an id-shaped token (`\b[a-z]+(?:-[a-z0-9]+){1,}\b`) that either equals one of this run's own valid claim ids or starts with one of the id-family prefixes actually used in `claims/verified.json` (`KNOWN_CLAIM_ID_PREFIXES`: price-, spec-, reviews-, warranty-, shipping-, returns-, founder-, gbrain-, benefit-, trust-) -- catching both a leaked real id and a hallucinated one in the same family. Wired into `gate_page_json`, so a hit ("claim id leaked into copy: <id> in <field>") feeds `write_and_gate_page`'s existing repair loop like any other gate failure.
3. **Post-render backstop.** New `claims.find_leaked_claim_ids_visible_text()` runs the same detection over the rendered page's visible text (tags/scripts/styles stripped), mirroring the existing EMF post-render check -- `render.render_page()` now scans for both and STOPs (`html_visible_text:<cartridge>`) on either.
4. **Last line of defense.** New `claims.strip_leaked_claim_ids()`: before the post-render scan runs, `render_page()` quietly removes any `(<known-claim-id>)` parenthetical straight out of the rendered HTML and logs a warning (`stripped leaked claim id from copy: ...`) instead of failing an already-written run over it. This only fires if both gates above missed something.
5. **Revision note.** `cli.build_revision_note()` now calls out a leaked-claim-id failure by name ("Delete the id from that sentence ... do not move the same id onto another sentence anywhere else on the page") -- a first verification run's second real run repeatedly moved the same parenthetical id onto a *different* sentence on each repair attempt instead of removing it, and STOPped after exhausting `MAX_REPAIR_ATTEMPTS`. Combined with item 1's prompt fix, a subsequent third run passed clean on attempt 1 for every cartridge (see Verify below).
6. **Tests.** `tests/test_claims.py`: `find_leaked_claim_ids` catches a parenthesized id in prose and ignores one that's only in `claim_ids`; `gate_page_json` STOPs on the leaked-id case and passes once the id moves to `claim_ids` only; a hallucinated id-shaped token in a known prefix family is also caught; `find_leaked_claim_ids_visible_text` and `strip_leaked_claim_ids` (removes a known leaked id, leaves an unrelated `(Peak Saunas, 2026)` citation alone) are covered directly. `tests/test_render.py`: `render_page()` strips a leaked `(price-fuji)` out of rendered copy and logs it, without raising. `tests/test_repair_loop.py`: `build_revision_note()` includes the "Delete the id" guidance only when a failure is a leaked-claim-id issue.

### Verify (real `adv run` against `fixtures/hidden-costs-v2.mov`, server, real Claude calls)
- **Run 1** (`20260909-2038-hidden-costs-v2`): PASS, attempts=6 repairs=3. Article's first attempt hit exactly the reported bug ("... Peak Fuji 2-Person Infrared Sauna (spec-fuji-capacity) ..."), gate caught it, repair on attempt 2 fixed it clean. (product-page/longform repairs were unrelated pre-existing gates: "unlock" hype word, missing claim_id on "reviews".)
- **Run 2** (`20260909-2041-hidden-costs-v2`), *before* items 1/5 above: STOPped -- article kept reprinting a leaked id in a different sentence on each of 3 attempts, exhausting `MAX_REPAIR_ATTEMPTS`. This is what led to items 1 and 5.
- **Run 3** (after items 1/5 landed and were deployed): see report for the final PASS/PASS verification pair required by this cycle.

## Cycle 6 (repair-loop convergence -- both `20260909-2052-hidden-costs-v2` and `20260909-2055-hidden-costs-v2` STOPped after exhausting `MAX_REPAIR_ATTEMPTS`: attempt 1 hit the hype word "unlock"; attempt 2's rewrite fixed it but tripped the digit/claim_id gate on the product's own short_name, "Peak Fuji 2-Person Infrared Sauna" (the "2" read as an unsourced number); attempt 3's rewrite fixed that but reintroduced "unlock" -- three real model calls per run, both runs still failed)

1. **Deterministic pre-repair pass, no model call.** `cli.apply_deterministic_fixes()` runs inside `write_and_gate_page` right after every gate check (including the very first, pre-repair one) and before ever building a REVISION REQUIRED prompt: a forbidden hype word (`cli._HYPE_SYNONYMS`: unlock/unlocks/unlocking -> get/gets/getting, elevate/elevates -> improve/improves, journey -> process, game-changer/game changer -> big improvement) is substituted case-preservingly, whole-word only (`cli.apply_hype_synonyms`); an exclamation mark becomes a period; a claim id leaked into a parenthetical is removed via the existing `claims.strip_leaked_claim_ids`. The page is then re-gated (`cli.check_page_gates`) -- only failures that survive this pass ever reach the writer. EMF, a banned name, and anything needing new content (a missing claim_id, word count, CTA) are left alone -- there's no safe deterministic rewrite for those. `write_and_gate_page` now returns `(page, attempts, deterministic_fix_counts)`; logs `deterministic fix applied: <n> field(s)` whenever the pass changes anything.
2. **Digit-rule exemptions.** `claims._strip_digit_exempt_tokens()` removes every occurrence of `facts_pack["digit_exempt_terms"]` (new field, computed once in `ground.LocalFactsSource.facts_for()` from every product's `short_name`/`title`/`name` across the whole catalog, not just the one this run is about) and any generic `\b[1-6]-Person\b` capacity token, from a text field *before* `claims._trigger_reason()`'s bare-digit check runs -- a dollar amount or percentage still requires a claim_id even inside the product-name sentence; only the bare-digit branch is affected. `validate_page_claim_ids()` and `gate_page_json()` both take/thread this through; a caller (or fixture) with no `digit_exempt_terms` key gets the pre-cycle-6 behavior unchanged.
3. **Repair-prompt memory.** `write_and_gate_page` now keeps `failures_seen` -- every failure across every attempt in this cartridge so far, deduplicated by `(path, issue)` -- and passes that (not just the current attempt's failures) to `cli.build_revision_note()`. The forbidden-word list (`vocab.ALWAYS_FORBIDDEN_TERMS`, one per line) is quoted verbatim via the new `vocab.forbidden_words_block()` both at the very top of `write.write_page()`'s system prompt and near the top of every REVISION REQUIRED block; the block now ends with "Fix all of these. Do not introduce any new violation. Before answering, re-read the forbidden word list and remove every occurrence."
4. **Financing sentence.** `vocab.ALLOWED_FINANCING_SENTENCE_NO_LENDER = "Financing is available at checkout."` is the single source of truth for both `write.py`'s prompt (replacing the old, looser "write only 'Financing available'" instruction) and the new gate `claims.find_financing_violations()`, wired into `gate_page_json`: while no lender is configured, any text field containing "financ" must equal that sentence exactly, or the run STOPs (feeding the repair loop like any other gate failure). No-op once a real lender is configured. `cartridges/product-page/cartridge.md`'s two "Financing available" mentions were updated to the same exact sentence so the cartridge prompt and the global voice block never disagree.
5. **Logging.** Every attempt already logged its full failure list with field paths (`write.<cartridge>: gate FAIL on attempt N: [...]`, unchanged); added `deterministic fix applied: <n> field(s)` whenever item 1's pass changes anything. `write_review_md()`'s Gate history table gained a "Deterministic fixes" column (per-attempt counts, parallel to "Failures per attempt").
6. **Tests.** `tests/test_claims.py`: the two required digit-exemption cases ("Peak Fuji 2-Person Infrared Sauna is built from cedar" passes with no claim_ids; "reaches 150°F" fails without one), a generic-capacity-only case, a case showing a real dollar amount inside the product-name sentence still needs a claim_id, and the `gate_page_json`/`facts_pack["digit_exempt_terms"]` integration; `find_financing_violations` (exact sentence passes, any other phrasing fails, no-op with a lender configured, `gate_page_json` STOPs on it). `tests/test_repair_loop.py`: `apply_hype_synonyms` (case-preserving, whole-word-only) and `apply_deterministic_fixes` (resolves a hype word, resolves a leaked-claim-id parenthetical, leaves EMF alone) as direct unit tests, plus `write_and_gate_page` integration tests proving a hype-word or leaked-id failure resolves in a single model call (`deterministic_fixes == [1]`), a 3-attempt case where attempt 3's REVISION REQUIRED block still names attempt 1's original CTA failure alongside attempt 2's new EMF failure (deduplicated, cumulative memory), and the forbidden-word-list/closing-instruction content of `build_revision_note`. `tests/test_write.py`: the forbidden-word list sits before `## JSON schema for page.json` in the system prompt; the exact financing sentence is present. All existing tests updated for `write_and_gate_page`'s new 3-tuple return and the Gate history table's new column; `tests/test_render.py`'s `PRODUCT_PAGE_PAGE`/`LONGFORM_PAGE` fixtures' `financing_line.text` updated to the new exact sentence so the full-pipeline dry run in `tests/test_cli_run.py` still passes the new gate (render.py's own hardcoded "Financing available" fallback markup, independent of page.json, is unchanged and out of scope for this cycle).

### Verify (real `adv run` against `fixtures/hidden-costs-v2.mov`, server, real Claude calls)
- **Run 1** (`20260909-2118-hidden-costs-v2`): STOPped -- `find_financing_violations` (item 4) flagged an FAQ **question** field, `"Is financing available?"`, as financing phrasing, even though its answer was the correct exact sentence. Item 4's check scanned every prose string for the substring "financ" with no exemption for a question -- fixed by skipping any string ending in `?` (a question is asking about financing, not stating financing terms). Added `test_find_financing_violations_ignores_a_question_about_financing` regression test.
- **Run 2** (`20260909-2122-hidden-costs-v2`): STOPped -- article cartridge exhausted `MAX_REPAIR_ATTEMPTS`, dominated (2-3 of 3 failures on every attempt) by `find_financing_violations` flagging ordinary buyer-education prose ("financing terms and sticker price are two separate questions") that named no figure or lender at all. This ad's whole angle is financing/price transparency, so the word "financing" appears constantly in commentary that asserts nothing specific and needed no claim_id under any other gate -- the question-only exemption from run 1 wasn't enough. Narrowed the check: a string only counts as a financing violation if it both mentions "financ" AND states an actual figure/lender (`claims._states_financing_terms`: contains a digit, `$`, `%`, or a known lender name) -- otherwise it's topical discussion, not an offer. `write.py`'s financing prompt paragraph updated to match (discussing financing as a topic is fine; only *stating* an offer is restricted to the one sentence). Added `test_find_financing_violations_ignores_topical_mentions_with_no_stated_terms`; updated `test_gate_page_json_stops_on_financing_violation`'s fixture to include a real figure so it still demonstrates a genuine violation.
- **Run 3** (`20260909-2127-hidden-costs-v2`): **PASS**, attempts=4 repairs=1 (article undershot the word range on attempt 1, expanded cleanly on attempt 2). No forbidden-word/digit/financing cycling.
- **Run 4** (`20260909-2131-hidden-costs-v2`): STOPped -- article exhausted `MAX_REPAIR_ATTEMPTS`. Attempts 1-2 undershot the word range (ordinary, pre-existing behavior); expanding it on attempt 3 added a paragraph about being skeptical of manufacturer health claims that used "study"/"studies" with nothing in facts_pack.verified_claims to cite (`claims.TRIGGER_WORDS` requires a claim_id for that word same as "reviews" -- the exact pattern already described in cycle 5's log 20260909-2055, never fixed then). Two changes, mirroring the existing "reviews" precedent exactly: (1) `write.py`'s prompt gained the same "always needs a claim_id, rephrase without the word" guidance for study/studies/clinical/medical/proven/rated that "reviews" already had; (2) `cli.apply_deterministic_fixes` (item 1's pre-repair pass) gained a small `_TRIGGER_WORD_SYNONYMS` map (`study`/`studies` -> `research`, which is not itself a trigger word) so if the word still slips through, the pass rewrites it -- no model call -- instead of escalating to a repair. Deliberately narrow: only the one word pair with an observed failure and a safe, meaning-preserving synonym; the other trigger words either already had prompt guidance (reviews) or have no safe drop-in replacement, so they still go through a real repair call. Added `test_apply_deterministic_fixes_resolves_a_safe_trigger_word_failure` and `test_apply_deterministic_fixes_leaves_trigger_words_with_no_safe_synonym_alone`.
- **Run 5** (`20260909-2137-hidden-costs-v2`): STOPped -- longform's FAQ answer combined an unrelated price statement with the required financing sentence appended verbatim: `"It's priced at $8,250, and that's the listed price on the product page. Financing is available at checkout."` `find_financing_violations` required the ENTIRE stripped field to equal the allowed sentence, so any field that legitimately combined it with other content always failed. Fixed: strip the one allowed sentence out (if present, verbatim) before deciding whether financing is still mentioned in what's left (a second, different financing statement) -- a field doesn't have to be nothing but the sentence, just contain no OTHER financing statement. Added `test_find_financing_violations_allows_the_exact_sentence_combined_with_unrelated_content`.
- **Run 6** (`20260909-2143-hidden-costs-v2`): crashed (not a gate STOP) -- `ValueError: write.article failed after retry: Extra data: line 1 column 1219 (char 1219)`. Both of `write.write_page`'s own bad-JSON retry attempts got a response where the model appended trailing content after an otherwise complete page.json object; `jsonutil.extract_json` called `json.loads` on the whole response and rejected it outright over text nobody reads. Pre-existing gap, unrelated to this cycle's other fixes -- surfaced by chance during this verification. Fixed: on a `json.JSONDecodeError` whose message is specifically "Extra data", fall back to `json.JSONDecoder().raw_decode()`, which parses just the first complete JSON value and ignores everything after it; any other JSON error (genuinely malformed, not just trailing content) still raises so `write_page`'s existing retry-with-a-fresh-call path is unaffected. New `tests/test_jsonutil.py`.
- **Run 7** (`20260909-2146-hidden-costs-v2`): STOPped -- `find_financing_violations`'s `_states_financing_terms` did a raw digit search that never applied item 2's digit-exemption stripping, so a sentence mentioning "financing information" alongside the product's own short_name elsewhere ("...financing information are all posted on the same page... The Peak Fuji 2-Person Infrared Sauna...") was flagged purely because of the "2" in the product name, with nothing to do with financing. Fixed: `_states_financing_terms` now takes `digit_exempt_terms` and strips them (via the same `_strip_digit_exempt_tokens` fix cycle 6 item 2 already has) before its digit check; `find_financing_violations`/`gate_page_json` thread `facts_pack["digit_exempt_terms"]` through. Added `test_find_financing_violations_ignores_a_coincidental_digit_from_the_product_name`.
- **Run 8** (`20260909-2151-hidden-costs-v2`): STOPped -- article exhausted `MAX_REPAIR_ATTEMPTS` on a fourth distinct shape of the same false-positive: "...Sauna is priced at $8,250, listed on the product page along with its specs and financing option..." was flagged because `_states_financing_terms` saw *some* digit/`$` anywhere in the sentence (here, the real, correctly-cited price) and the word "financ" anywhere in the same sentence ("financing option") -- with no actual connection between the two. After four rounds of narrowing a text-content/proximity heuristic and hitting a new false positive each time, re-scoped item 4 entirely: `find_financing_violations` no longer scans prose for the substring "financ" at all. It now only checks the schema's dedicated `financing_line` field (`hero.financing_line` / `final_cta.financing_line` in product-page and longform -- the field `GLOBAL_VOICE_BLOCK`'s financing paragraph and every cartridge.md's financing-line rule were actually about all along) and requires its text to equal `ALLOWED_FINANCING_SENTENCE_NO_LENDER` exactly. A lender name invented anywhere else is still caught by the pre-existing `find_forbidden_terms`; an invented number anywhere else still needs a claim_id under item 2's rule. `_states_financing_terms`/`_FINANCING_TERMS_RE`/the `digit_exempt_terms` threading into the financing check are all removed as unused. Rewrote the financing test block in `tests/test_claims.py` to match (dropped six tests for behavior that no longer exists; added tests for the nested-field and bare-string `financing_line` shapes).
- **Run 9/PASS 1** (`20260909-2156-hidden-costs-v2`): **PASS**, attempts=4 repairs=1 (deterministic fix resolved 2 fields on longform with no model call; product-page repair was the pre-existing "bare year outside a quote" rule, unrelated to this cycle).
- **Run 10/PASS 2** (`20260909-2159-hidden-costs-v2`): **PASS**, attempts=4 repairs=1 (article undershot the word range, expanded cleanly).
- **Run 11** (`20260909-2202-hidden-costs-v2`): crashed (not a gate STOP) -- `ValueError: write.article failed after retry: missing required key: turn_section`. `write.write_page`'s own bad-JSON/schema retry loop (separate from the repair loop) resent the *exact same* messages on retry with zero feedback about what went wrong on the previous attempt -- attempt 1 got back an empty response ("Expecting value: line 1 column 1"), and attempt 2, blindly re-rolling the identical prompt, came back missing a whole required top-level key instead. Pre-existing gap, unrelated to this cycle's other fixes. Fixed: the retry now appends the bad response as an assistant turn plus a real correction message naming the specific parse/validation error, instead of silently resending the same one-shot prompt. Added `test_write_page_retry_feeds_the_parse_error_back_as_a_correction`.
See report for the three consecutive PASS runs required by this cycle.

## Cycle 7 (operator review of run out/20260909-2212-hidden-costs-v2)

1. **Warranty wording, fixed sentence.** claims/verified.json's `warranty-terms`/`gbrain-warranty-component-coverage` claims show per-component coverage (heating elements & cabinetry 7yr; control system & RLT panel 3yr; chromotherapy/audio/WiFi/accessories 1yr) -- not a blanket lifetime on every component. The product-page run wrote "Limited lifetime warranty on the cabin, heating elements, and electronics" -- false for electronics (3yr or 1yr, not lifetime). New `vocab.ALLOWED_WARRANTY_SENTENCE` ("Limited lifetime warranty; full terms by component are published on the warranty page."), `vocab.ALLOWED_WARRANTY_SPEC_LABEL`/`ALLOWED_WARRANTY_SPEC_VALUE` (the spec-table form: label "Warranty", value "Limited lifetime warranty (terms by component)") are the single source of truth for both `write.py`'s `GLOBAL_VOICE_BLOCK` (replacing the old, weaker "always say 'limited lifetime warranty'" line) and new gate `claims.find_warranty_violations`, wired into `gate_page_json`. Scoped to *any* text field that mentions "warrant" (not one dedicated field like the financing gate) -- warranty copy can legitimately land in a proof bullet, trust-strip field, or specs-table row. A field passes if it's exactly one of the two fixed forms, the bare spec-table label ("Warranty"), or a verbatim quote of a verified warranty claim's own text (substring match, so an attributed quote still passes); anything else feeds the existing writer repair loop like any other gate failure.
2. **Implied claims.** The product-page run also wrote "US-owned, so the person you'd reach is domestic, not a call center reading a script" -- being US-owned does not verify support is domestic; that's a second, unverified fact inferred from a verified one. `GLOBAL_VOICE_BLOCK` gained a paragraph stating the rule generally (ownership doesn't imply support location; free shipping doesn't imply delivery speed; a rating doesn't imply "best") with concrete examples. New `vocab.IMPLIED_CLAIM_FORBIDDEN_TERMS` ("call center", "domestic support", "us-based support", "american-made", "made in the usa") is folded into `claims.find_forbidden_terms` (now takes an optional `verified_claims` arg) the same way `FORBIDDEN_LENDER_NAMES` is folded in for financing -- forbidden unless a verified claim's own text actually carries the phrase, so a future genuinely-verified claim about support location isn't blocked forever. `gate_page_json` now passes `facts_pack["verified_claims"]` through.
3. **CTA: model-name-only option.** `cartridges/product-page/schema.json` and `cartridges/longform/schema.json`'s `allowed_cta_texts` each gained `"Shop the {model_name}"` (e.g. "Shop the Fuji") alongside the existing `{short_name}` templates, unchanged. `write.resolve_allowed_cta_texts()` gained an optional `model_name` parameter (defaults to `None`, so an existing caller/template with no `{model_name}` placeholder is unaffected -- `str.format` only substitutes a placeholder actually present in the template string); `cli.write_and_gate_page` now passes `facts_pack["product"]["name"]` (products.json's own `name` field, e.g. "Fuji" -- already exactly the bare model name, no extraction needed) as `model_name`. Both cartridge.md's CTA Rules lines updated to mention the new placeholder.
4. **Tests.** `tests/test_claims.py`: `find_warranty_violations` (allows the exact sentence, the spec-table value, a verbatim quote of the verified claim, and a bare "Warranty" label; flags the exact false per-component summary from the operator review) and `gate_page_json` STOPs on a warranty violation; `find_forbidden_terms` with the new `verified_claims` arg (catches the exact "call center" sentence from the review and the other implied-claim terms; allows one once a verified claim states it; stays forbidden by default with no `verified_claims` arg passed). `tests/test_repair_loop.py`: `resolve_allowed_cta_texts` substitutes `{model_name}` and defaults it to `None` without breaking a `{short_name}`-only template; `find_cta_violation` passes for a resolved "Shop the Fuji". `tests/test_write.py`: the exact warranty sentence is in the system prompt (same pattern as the financing sentence test); the real `cartridges/product-page/schema.json` and `cartridges/longform/schema.json` both carry `"Shop the {model_name}"` alongside the pre-existing `{short_name}` entries. `tests/test_render.py`'s shared `PRODUCT_PAGE_PAGE`/`LONGFORM_PAGE` fixtures (reused end-to-end by `tests/test_cli_run.py`) had their placeholder `"warranty text"`/`"Backed by a written warranty."` strings updated to the new fixed sentence, since those now go through the hard warranty gate too.

### Verify (real `adv run` against `fixtures/hidden-costs-v2.mov`, server, real Claude calls)
- **Run 1** (`20260909-2233-hidden-costs-v2`): STOPped -- article exhausted `MAX_REPAIR_ATTEMPTS` on item 1's new warranty gate, in two false-positive shapes item 1's design didn't anticipate: (a) ordinary buyer-education prose merely discussing "warranty" as a policy topic with no specific coverage claim ("the return or warranty terms", "what the warranty actually covers component by component"); (b) the allowed sentence combined with unrelated surrounding content in the same field ("Clear policies on shipping, warranty, and returns... Limited lifetime warranty; full terms by component are published on the warranty page." -- the allowed sentence *is* in there, verbatim, but `is_allowed` required the whole field to equal it exactly). Both are the same two false-positive shapes the financing gate already hit in cycle 6 (items 2 and 5). Fixed `claims.find_warranty_violations`: strip the allowed sentence out of the field before judging what's left (mirrors cycle 6 item 5's financing fix); a violation now requires "lifetime" to still be present alongside "warrant" after that strip -- the operator-reported bug's actual shape was always a blanket *lifetime* coverage claim, so a topical mention with no lifetime/blanket-coverage assertion no longer trips the gate. Added `test_find_warranty_violations_ignores_topical_mentions_with_no_lifetime_claim` and `test_find_warranty_violations_allows_the_exact_sentence_combined_with_unrelated_content`.
- **Run 2** (`20260909-2238-hidden-costs-v2`): STOPped -- one violation left: `"A warranty that's actually written down. Peak Saunas offers a limited lifetime warranty; full terms by component are published on the warranty page."` The allowed sentence is in there verbatim except "Limited" is naturally re-cased to "limited" mid-sentence (it isn't the first word of its own sentence) -- the removal in Run 1's fix was case-sensitive, so it didn't strip, and the leftover "lifetime" tripped the gate. Fixed: `_ALLOWED_WARRANTY_SENTENCE_RE` (case-insensitive) replaces the case-sensitive `str.replace`; the top-level exact-match checks are also now case-insensitive. Same wording, same meaning either way. Added `test_find_warranty_violations_allows_the_sentence_re_cased_mid_sentence`.
- **Run 3** (`20260909-2241-hidden-costs-v2`): STOPped -- article passed clean; longform exhausted `MAX_REPAIR_ATTEMPTS` on two near-verbatim variants of the allowed sentence, identically reproduced on every attempt (the model doesn't treat either as wrong): `"...a limited lifetime warranty, with full terms by component published on the warranty page."` (drops "are", "with" instead of ";") and `"...a limited lifetime warranty; full terms by component are published on the warranty page, so you can check..."` (comma instead of a period, continuing the sentence). Neither states anything false -- the specific phrase that actually matters ("full terms by component ... published on the warranty page") is intact in both; only a connector word or the closing punctuation drifted while the model wove the sentence into a bigger one. A case-insensitive exact-string match doesn't recognize either. Fixed: new `_WARRANTY_SENTENCE_CORE_RE` recognizes the semantic core (that specific disclaimer phrase, "are" optional, connector/punctuation before it flexible) in place of the exact-string regex -- it still requires the disclaimer phrase itself, so it does not match the original false claim (no "full terms by component ... published" at all) or a claim stating specific coverage with no disclaimer. Added `test_find_warranty_violations_allows_minor_connector_drift_around_the_core_disclaimer`, `test_find_warranty_violations_allows_the_sentence_continued_with_a_comma`, and `test_find_warranty_violations_still_flags_a_claim_with_no_disclaimer_phrase_at_all` (guards the core-pattern tolerance against swallowing the original bug).
- **Run 4** (`20260909-2248-hidden-costs-v2`): STOPped -- longform passed clean; article exhausted `MAX_REPAIR_ATTEMPTS` on `"A warranty document you can actually read component by component, not just a one-line lifetime promise."` -- both "warranty" and "lifetime" appear, but never adjacent; this is commentary contrasting a vague "lifetime promise" against reading real per-component terms, not a claim that Peak's own warranty is blanket lifetime coverage. The bare `"lifetime" not in remainder` check (added in Run 1's fix) was too blunt -- the actual assertion that needs catching is specifically the two words together ("lifetime warranty"), not either word alone anywhere in the sentence. Fixed: `_LIFETIME_WARRANTY_BIGRAM_RE` (`r"lifetime\s+warranty"`) replaces the bare substring check. Added `test_find_warranty_violations_ignores_lifetime_and_warranty_used_separately`. This is the fixture-sweep run recorded as `hidden-costs-v2` in docs/SWEEP-2026-09-09.md; see that file for the full 7-fixture results.

## Cycle 8 (product detection and per-model grounding)

1. **Whisper initial prompt.** `adv/ingest.py`'s `video_to_transcript` passes `--prompt` (confirmed via `whisper-cli --help` on the server -- `-p` is `--processors`, a different flag) with a fixed `WHISPER_INITIAL_PROMPT`: "Peak Saunas. Sauna, infrared sauna, red light therapy. Models: Fuji, Everest, Rainier, Shasta, Denali, Matterhorn, Patagonia, El Capitan, Kilimanjaro, Mini." `fixtures/product-features-v2.mov` had whisper mishearing "Sauna" as "Sonna" throughout; re-transcribed with the prompt, "Sauna" is now spelled correctly (see Verify below for the three fixtures' first sentences and the updated committed `.transcript.txt` files).
2. **Product picker: real model names only, word-boundary, first-mentioned wins.** `ground.LocalFactsSource.pick_product_with_warning()` (new; `pick_product()` is now a thin backward-compatible wrapper around it, same signature/behavior for every existing caller) matches only a product's own `name` field against the ad's transcript/hook/promise/angle, case-insensitively, on a `\b...\b` word/phrase boundary -- so "el cap", "1-person", "2-person", "sauna mini" alone never match (they're not model names), while "sauna mini" still matches because it contains the real word "mini". If several models are named, whichever appears first in the haystack (by match position) wins. Discontinued models (`active: false`, see item 4) are excluded from matching even if literally named. When nothing is named, falls back to the default product and returns a warning string (`"product not named in ad; defaulted to Fuji"`) instead of `None`; `cli.cmd_run` now calls `pick_product_with_warning`, logs the warning via `log.event`, and threads it into `write_review_md` (new `product_warning` param) which prints it as a bolded `**WARNING: ...**` line right under the Product line in REVIEW.md. Root cause of the original bug: the old `pick_product` did an unanchored substring check (`p["name"].lower() in haystack`), which happened to work for "Mini" by luck (the substring is short and word-bounded in practice) but had no defined behavior for aliases or overlapping names, and had no signal at all for "nothing was actually named" -- it silently fell through to the default with no record of why.
3. **Per-model g Brain grounding.** `claims/seed-from-gbrain.json`'s `gbrain-<model>-*` pattern (dimensions, red light therapy, wood, electrical) previously existed only for Fuji and Everest. Read `spec/all-models` plus the ten active models' individual `spec/<model>` pages from g Brain (never any customer/order/email/support_ticket/conversation/slack_log/person page) and added 29 new `spec-<model>-*` claims directly to `claims/verified.json` (category `spec`, source `gbrain:spec/<model>; <product url>`, `approved_by: "gbrain-spec"`, `date: "2026-09-09"`) for exactly the fields each model's spec page actually states -- electrical requirement, exterior/crate dimensions, red light therapy panel count, heating panel count/type, and (for the three outdoor models missing it) wood/exterior material. Skipped, never inferred: Mini has no dimensions or heater-panel-count on its spec page at all (`spec/mini`'s Dimensions & Weight section doesn't exist, and `spec/all-models`' row for Mini is `—` across every dimension column) -- so `spec-mini-dimensions` was NOT created; the ad's "31 by 32 inches" claim stays genuinely unverifiable (see Verify). No spec page for any of the 11 models mentions app control despite several product titles saying "Smart WiFi App Control" -- so no `spec-<model>-app-control` claim exists anywhere; the ad's "app on my phone" claim is also genuinely unverifiable. No new claim mentions EMF (asserted by a hard assert in the seeding script before writing).
4. **Grounding gap: per-model g Brain claims never reached the writer.** Separately from adding the claims, found that `ground.py`'s `facts_for()` only ever surfaced a model's `spec-*` claims if `claims/products.json`'s own `specs` array happened to list that exact `claim_id` (the Shopify-derived fields only) -- so even the pre-existing `gbrain-fuji-*`/`gbrain-everest-*` claims (dimensions, red light, wood, power) were never in `facts_pack.verified_claims` and the writer had nothing to cite from them, a latent bug predating this cycle. Fixed: `facts_for()` now also pulls in every verified claim whose id starts with `spec-<model>-` or `gbrain-<model>-` for the run's chosen model, regardless of whether `products.json` lists it as a spec-table row. `facts_pack` stays under the ~4k-token budget (existing `test_facts_pack_stays_small` still passes).
5. **`claims/products.json`: `active` flag.** Every product got an explicit `active` field -- `false` for Crown (discontinued), `true` for the ten current models. Olympus and Aspen (also discontinued per `docs/knowledge-map.md`) aren't in the live Shopify products cache at all, so there was nothing to flag for them. `pick_product_with_warning` filters to `active` products (defaulting missing `active` to `true` for forward compat) before either name-matching or falling back to the default.
6. **Tests.** `tests/test_ingest.py`: `video_to_transcript` passes `--prompt` with the exact `WHISPER_INITIAL_PROMPT` string, which is asserted to contain every model name and "Peak Saunas"/"Sauna". `tests/test_ground.py`: single named model matches case-insensitively; alias-only text ("el cap", "1-person", "sauna mini" alone) does not match and defaults with the warning; "sauna mini" still matches because "mini" is a real word inside it; several named models -> first-mentioned wins; nothing named -> default + warning; a discontinued model named in the ad is still ignored; an explicit `--product` always wins and never warns; the old `pick_product` wrapper still returns a bare product. Also: Mini's new `spec-mini-electrical`/`spec-mini-red-light` claims reach `facts_pack.verified_claims`; Fuji/Everest's pre-existing `gbrain-*-dimensions`/`gbrain-fuji-power` claims now reach it too (previously didn't); no claim in Mini's or Fuji's facts_pack ever mentions EMF. `tests/test_cli_review.py`: `write_review_md` prints the `**WARNING: ...**` line when `product_warning` is set and omits it entirely when `None`.
7. **Problem 3 (reported, not fixed -- gate unchanged).** `fixtures/price-comparison-v2.mov`'s "infrared sauna is on sale right now for $5,450" does not match Fuji's live price ($8,250) or any other model's price -- **except the Mini, whose current price in `claims/products.json` is exactly $5,450.00** (compare-at $10,832, i.e. genuinely "on sale"). The transcript itself never names a model ("I think I'm going to buy the peak sauna" -- generic), so the word-boundary picker in item 2 correctly still defaults this ad to Fuji with a warning; it does not use price as a matching signal. Flagging for Caleb: the $5,450 figure strongly suggests this ad is actually about the Mini, not a stale/unrelated price -- worth confirming before the next run of this creative, either by having the speaker name the model on camera or by Caleb manually passing `--product mini`.
8. **Live price refresh was silently erasing `active`.** Found while verifying item 5 live on the server: `adv/prices.py`'s `merge_products()` (runs at the start of every `adv run`, refreshing `claims/products.json` from the live Shopify feed) only ever re-attached the old entry's `default` and `short_name` fields onto the freshly-rebuilt product dict -- `active` wasn't on that list, so the very first real `adv run` after item 5 landed wiped every model's `active` flag back out, and the discontinued-model exclusion in item 2 would have silently stopped working on the next run. Fixed: `merge_products()` now also re-attaches `active` when the old entry has it. Added `test_merge_products_preserves_active_flag_through_a_live_refresh`; confirmed live on the server -- ran `adv run` again after the fix and `git status` showed no diff on `claims/products.json` this time.

### Verify (server, real Claude/whisper calls)
- Re-transcribed all three video fixtures with the new `--prompt` (`adv ingest`). `product-features-v2.mov` now reads "Peak **Sauna** Mini" / "different **saunas**" throughout (was "Sonna"/"Sonnas"); the other two were unaffected (no misheard brand words to begin with) and confirmed unchanged in substance. Committed `.transcript.txt` updated for all three (see `fixtures/*.transcript.txt` diff, `e8345ac`).
- `.venv/bin/pip install -e . -q` re-run on the server after each push; `.venv/bin/python -m pytest -q`: **224 passed** on both the Mac clone and the server (209 pre-existing + 15 new this cycle).
- `adv run fixtures/product-features-v2.mov` (`20260909-2319-product-features-v2`): **STOP** at `ad_claims`, exactly 1 unmatched item -- `"It's only 31 by 32 inches"` (overlap 0.0). This is a genuine gap, not a bug: `spec/mini`'s own Dimensions & Weight section is empty and `spec/all-models`' Mini row is `—` across every dimension column (item 3 above), so no dimensions claim exists to match, correctly. Confirmed directly against the real ad_brief.json this run produced (using the deployed `LocalFactsSource.pick_product_with_warning`, since the ad_claims STOP fires before product-picking runs) that the picker now selects **Mini** with no warning -- the whisper fix plus the word-boundary picker together fixed the original "grounded on Fuji" bug; every other electrical/red-light/audio claim in this ad now matches cleanly against Mini's own spec claims (previously only dimensions, electrical, app-control, and Bluetooth were all unmatched against Fuji's specs).
- `adv run fixtures/hidden-costs-v2.mov` (`20260909-2320-hidden-costs-v2`): **PASS**, no model named in this ad (as expected -- it's about pricing transparency, not a specific SKU) -- REVIEW.md correctly shows `**WARNING: product not named in ad; defaulted to Fuji**` under the Product line. No regression from cycle 7's fixes.
- Problem 3: see item 7 above -- reported, gate unchanged, no code change for this.

### Corrected transcripts (first sentence each)
- `hidden-costs-v2.mov`: "There's nothing worse than trying to buy something online and not being able to find the price anywhere."
- `product-features-v2.mov`: "I just ordered the Peak Sauna Mini and I could not be more excited."
- `price-comparison-v2.mov`: "I keep hearing all the benefits of infrared saunas, so I really want to get into it."

## Cycle 9 (product-page facts the verified list lacked; price-based product inference; hedged market observations; matching tolerance)

**Finding.** `fixtures/product-features-v2.mov`'s own facts are all true and all stated on the Mini's Shopify product page (`runs/products-cache.json`, `body_html`) -- runs from the Peak Saunas app, plugs into a standard 120V household outlet with no electrician, two HiFi Bluetooth speakers, Canadian hemlock, a medical-grade red light panel as standard, ships free in a protective crate -- but none of them were in `claims/verified.json`, so the `ad_claims` gate STOPped on them.

1. **PDP claim seeding (new `adv/pdp_claims.py`).** At the start of every `adv run`, right after the live price refresh (`cli.cmd_run`, same raw Shopify feed data -- `prices.refresh_price_data` now also returns the raw `live_products` list, which still carries `body_html`; `merge_products()`'s curated entries never did), `pdp_claims.seed_pdp_claims()` walks every *active* product and extracts a fixed set of facts from its `body_html`, one conservative phrase match per fact, only if the page states it: app control (`"peak saunas app"`), electrical (`"120v"` / `"standard household outlet"` / `"no electrician"`), speakers (`"bluetooth speaker"`), wood (`"hemlock"` / `"cedar"`), red light (`"medical-grade red light"`), crate shipping (`"protective crate"` / `"crate"`), capacity (a `\b[1-6]-person\b` token), and assembly (`"clasp-together"` / `"assembly"`). Each claim's `text` is the *shortest* sentence in the page (tags stripped, entities unescaped) that contains the fact's phrase, not just the first -- Shopify body copy typically states a fact once in a long multi-fact intro sentence and again in its own short, single-topic paragraph later on, and the short one reads far better as a claim's source text. `id` is `pdp-<model>-<fact>` (e.g. `pdp-mini-electrical`), `category` is `"spec"`, `source` is the product's own URL, `approved_by` is `"site"`, `date` is today. Asserts no claim ever mentions EMF (skips a matched sentence outright if it does). These claims are written fresh every run to `runs/pdp-claims-cache.json` (regenerated alongside `runs/products-cache.json`, never read back -- there's nothing to cache, `body_html` is already fetched) and merged into the run's in-memory verified-claims universe only: `LocalFactsSource.all_verified_claims()` gained an `extra_claims` param (appended after the existing live-price substitution) for the `ad_claims` gate, and `LocalFactsSource.facts_for()` gained a `pdp_claims` param, filtered to the chosen product's own `pdp-<model>-` namespace, for `facts_pack.verified_claims`. **Never written to `claims/verified.json`** -- it stays hand-curated, per the finding.
2. **Product inference by price.** `ground.LocalFactsSource.pick_product_with_warning()`: when no model is named (word-boundary name match still tried first, unchanged), a new step checks whether the ad brief (hook/promise/angle/`claims_made` -- deliberately not `speaker_experience`, see item 3) quotes a dollar amount within $1 of exactly one active product's current price; if so, that product is picked instead of falling through to the default, and `f"product inferred from quoted price {format_price(amount)} = {product['name']}"` is logged (via the existing `product_warning` -> `log.event("run", ...)` path in `cli.cmd_run`, unchanged) and shown in REVIEW.md the same way the "defaulted" warning already is. An amount that matches zero or more than one active product is not treated as a signal (ambiguous) and falls through to the ordinary default unchanged.
3. **Hedged first-person market observations, in ingest.** `ingest.AD_BRIEF_SYSTEM` gained a rule: a statement the speaker frames as their own research, estimate, or hedge ("I've been seeing...", "around $X", "say $X", "I did the math", "I realized...") goes into `speaker_experience`, never `claims_made`, even when it carries a number -- it's the speaker's own approximation, not an independently checkable fact. A plain factual assertion with no hedge (e.g. "infrared sauna is on sale right now for $5,450") still belongs in `claims_made`. `write.py`'s existing first-person-attribution rule (fix cycle 2 item 2, `GLOBAL_VOICE_BLOCK`) already turns anything landed in `speaker_experience` into "one customer told us..."/"she estimated..." prose when `speaker_pov` is `first_person` -- no writer change needed, only the ingest-side classification.
4. **Ad-claim matching tolerance.** Both options this item offered were implemented, since a live verification run (below) showed they fix two distinct real cases, not just the one named example: (a) `claims.normalize()` gained a small, hand-picked synonym fold (`protected` -> `protective`, `delivery` -> `shipping`) applied to both sides of every comparison, so "crate-protected delivery" (the on-screen still copy) overlaps cleanly with `gbrain-shipping-free-crate-origin`'s own wording ("... a PeakGuard custom protective wooden crate ... Free shipping ...") instead of STOPping at 0.333 overlap (below the 0.6 threshold) purely over word choice -- "crate" alone matched before, "protected"/"delivery" didn't share a token with "protective"/"shipping" until now; (b) `claims.match_claim()`'s overlap threshold drops to 0.5 (from 0.6) only when the ad claim has <=3 content tokens -- found necessary live against `fixtures/product-features-v2.mov`'s real ad_brief.json: "It has Bluetooth capabilities" (2 content tokens: bluetooth, capabilities) only overlaps 0.5 against the now-seeded `pdp-mini-speakers` claim's "Two HiFi Bluetooth speakers..." (shares "bluetooth", not "capabilities"/"speakers") -- a short claim has too few tokens for the rest of the sentence to make up one word-choice mismatch the way a longer claim's surrounding words can. The numeric-token rule is untouched in both cases; the synonym list stays two hand-picked pairs, not a general thesaurus.
5. **Tests.** `tests/test_pdp_claims.py` (new): `extract_pdp_claims()` against `tests/fixtures/mini-body-html-sample.html` (a real, saved `body_html` snippet -- not the full cache) finds all 8 facts with the page's own sentence text, correct shape/provenance, never EMF, only creates a fact the page actually states, empty without `body_html`; `seed_pdp_claims()` skips inactive products and products missing from the raw feed; `save_pdp_claims_cache()` writes JSON. `tests/test_ground.py`: `facts_for()` merges PDP claims scoped to the chosen product only; `all_verified_claims(extra_claims=...)`; price inference picks the Mini from `fixtures/price-comparison-v2.mov`'s `$5,450`, ignores amounts matching no product, and a named model still wins over a quoted price. `tests/test_claims.py`: "Crate-protected delivery" now matches `gbrain-shipping-free-crate-origin` at 1.0 overlap (with a companion test proving it was 1/3 before the synonym fold, and that the fold doesn't create false matches elsewhere); a short claim matches at the 0.5 bar, a longer claim with the same single-word overlap still needs 0.6, and a 1-token claim doesn't spuriously match an unrelated verified claim at the lower bar. `tests/test_ingest.py`: the hedge-phrase rule and the `$5,450` example are in `AD_BRIEF_SYSTEM`; a contract test locks in the intended classification of the real `price-comparison-v2.mov` transcript (`claims_made` == only the un-hedged `$5,450` item) -- this proves the target shape, not real model behavior, which is verified live below. `tests/test_cli_run.py`'s network-free dry run fake updated for `refresh_price_data`'s new 3-tuple return (raw `live_products`, `[]` in the fake -- no real Shopify data in a dry run, so no PDP claims, which is expected and untested there).

### Verify (real `adv run` against all four fixtures, server, real Claude/whisper calls, foreground, one at a time)

- **`adv run fixtures/product-features-v2.mov`** (`20260909-2353-product-features-v2`): **STOP** at `ad_claims`, exactly 1 unmatched item -- `"It's only 31 by 32 inches"` (overlap 0.0), the expected, genuine gap (item 3 above never claimed dimensions; the Mini's spec pages still don't state them). The Mini was picked correctly (word-boundary name match, unchanged from cycle 8). Before item 4's second half (the short-claim threshold) was added, the same ad_brief shape STOPped on 4 items instead of 1 -- `"It has Bluetooth capabilities"` (0.5, below the general 0.6 bar) and `"It can be started from a phone app"` (0.333) were the two new-this-cycle near-misses that motivated adding it; `"It includes a free lifetime warranty"` (0.5) also cleared on that first run but is a pre-existing, separate content-accuracy problem (see "Not fixed" below), confirmed unrelated to this cycle's changes (it independently matches `gbrain-allowlist-lifetime-warranty` at 0.667, above even the general 0.6 threshold). `electrical`/`red-light`/`delivery` all matched cleanly against the new `pdp-mini-*` claims on both runs -- those three were previously unmatched (SWEEP-2026-09-09.md item 3). Tokens 1,187 in / 683 out, est. cost $0.0138.
- **`adv run fixtures/price-comparison-v2.mov`** (`20260909-2354-price-comparison-v2`): **STOP** at `ad_claims`, 1 unmatched item -- `"Infrared sauna is on sale right now for $5,450"` (overlap 0.167). Not the expected PASS -- see "Not fixed" below; the product-inference fix (item 2) is verified correct in isolation (`tests/test_ground.py`) but this run never reaches product-picking, because `gate_ad_brief_claims` runs first and STOPs before it. Tokens 1,227 in / 847 out, est. cost $0.0164.
- **`adv run fixtures/still-unforgettable-4x5.png`** (`20260909-2355-still-unforgettable-4x5`): **STOP** at `ad_claims`, 2 unmatched items -- down from the 6 SWEEP-2026-09-09.md item 7 recorded, though not entirely apples-to-apples: this run's real vision call extracted only 4 `claims_made` this time (`"Financing available from est. $257/mo through Bread Pay"`, `"Product features full-body medical-grade red light"`, `"Product includes crate-protected delivery"`, `"Product includes access to Peak Wellness Club"`) -- the two competitor claims ("3 hours to use", "leaves skin dull") SWEEP recorded weren't extracted at all this run (real model non-determinism on a vision call, not a fix cycle 9 result -- flagging so this isn't overclaimed). Of the 4 extracted, 2 now cleanly match thanks to this cycle: `"full-body medical-grade red light"` (was 0.571, now matches the new `pdp-fuji-red-light`/`pdp-mini-red-light` claims) and `"crate-protected delivery"` (was 0.333, now matches `gbrain-shipping-free-crate-origin` at 1.0 via item 4's synonym fold). The 2 still unmatched are genuine, pre-existing gaps SWEEP already flagged as Caleb's call, unaffected by this cycle: `"Financing available from est. $257/mo through Bread Pay"` (0.0 -- no Bread Pay claim exists) and `"Product includes access to Peak Wellness Club"` (0.333 -- "Peak Wellness Club" is not a real claim in `claims/verified.json`). Tokens 5,535 in / 829 out, est. cost $0.0290.
- **`adv run fixtures/hidden-costs-v2.mov`** (`20260909-2356-hidden-costs-v2`): **PASS** (regression check) -- no product named (as expected, correctly defaults to Fuji with the warning), attempts=4 repairs=1 (product-page: 1 deterministic fix, no model call; article/longform clean on attempt 1). `pdp-fuji-*` claims (app-control, electrical, speakers, wood, red-light, crate-shipping, capacity, assembly) all appear in "Claims used" alongside the pre-existing `gbrain-fuji-*`/`spec-fuji-*` set -- PDP seeding works for a second product, not just Mini, and introduced no regression. Word counts: article 1,007, product-page 312, longform 971. Tokens 137,589 total (6 calls, budget cap 150,000/12), est. cost **$0.5789**. `adv review` run on this PASS -- wrote `article-review.html`, `longform-review.html`, `product-page-review.html`.
- **Tests**: 244 passed on both the Mac clone and the server after the first commit (`466fc96`); 247 passed on both after the second (`16c8410`, the short-claim threshold addition made live-necessary by the product-features-v2 run above).

### Not fixed / flagging for Caleb

- **`price-comparison-v2.mov` still STOPs, not PASS.** The claim itself (`"Infrared sauna is on sale right now for $5,450"`) fails the `ad_claims` gate at 0.167 overlap against the live `price-mini` claim (`"The Peak Saunas Mini is priced at $5,450."`) -- the numeric token matches, but the two sentences share almost no other vocabulary ("sale" appears nowhere in the price claim's text, and `show_compare_at_price` is off by default so the genuinely-lower list price never enters the claim either). This gate runs *before* product-picking, so fix cycle 9 item 2 (price inference) never gets a chance to run in the real pipeline -- it's verified correct in isolation only. Item 4's fixes don't reach this case either: the claim has 6 content tokens, above the short-claim cutoff, and no synonym pair covers "on sale" vs. "priced at." This wasn't one of this cycle's four named fixes and needed a call outside that scope, so it's reported rather than patched blind: either (a) `build_live_price_claims()` states the sale framing in the claim text itself when `compare_at_price` is set (Mini's is: $10,832 list vs. $5,450 current -- genuinely on sale), or (b) another matching-tolerance adjustment, is Caleb's call.
- **The free-lifetime-warranty overclaim (SWEEP-2026-09-09.md item 3) is still live and ungated at the `ad_claims` stage** -- confirmed independent of this cycle's changes (0.667 overlap against `gbrain-allowlist-lifetime-warranty`, clears the unmodified 0.6 bar on its own). Caleb's original call stands: correct the on-camera language, not the claims store.

## Cycle 10 (product-picking order; locked-topic ad-claim overclaims; ad_overclaim_policy)

**Problem A.** Cycle 9's own "Not fixed" note: the `ad_claims` gate ran before the product was picked, so price-based product inference (cycle 9 item 2) never got a chance to run in the real pipeline -- `price-comparison-v2.mov` STOPped on `"Infrared sauna is on sale right now for $5,450"` even though $5,450 is exactly the Mini's price.

**Problem B.** A false ad claim ("free lifetime warranty if it doesn't work") could clear the ordinary 0.6 word-overlap bar against a superficially similar verified claim (`gbrain-allowlist-lifetime-warranty`'s "Limited Lifetime warranty.", 0.667 overlap) and get reported as **MATCHED** in `REVIEW.md` -- the page itself stayed honest (locked to the fixed warranty sentence), but the ad's overclaim went unflagged. Same risk class for review-stat claims ("4.9/5, 9,000 reviews"), financing figures, and a price claim with a *different* number than the product's real price.

1. **Reorder `adv run`: product-picking, then the facts pack, then the ad-claims gate.** `adv/cli.py`'s `cmd_run`: `pick_product_with_warning` (unchanged itself) and `facts_source.facts_for(...)` now run right after `ad_brief` is built, *before* `gate_ad_brief_claims` -- previously the gate ran first. The writer-repair loop's own gate (`gate_page_json`, inside `write_and_gate_page`) is unaffected -- it already ran after the writer, and still does.
2. **Numeric anchor for price claims (`adv/claims.py`).** New `evaluate_price_claim(ad_claim_text, product_price)`: any dollar amount in the ad claim within $1 of `product_price` -- this run's already-picked product's current price, available now because of item 1's reorder -- matches, *regardless of surrounding wording* ("on sale right now for" vs. the verified claim's "is priced at"). No match -> unmatched with the message `"quoted price $X matches no current product price"` (not the generic AD OVERCLAIM format -- there's no single verified claim to point at, since the check is against the product's price, not a text comparison). This replaces word-overlap matching for any ad claim carrying a dollar amount; the old numeric-token subset check in `match_claim` is untouched for every other claim shape.
3. **Locked topics never match by word overlap (`adv/claims.py`).** New `classify_locked_topic(ad_claim_text)` -> `"warranty"` (`\bwarrant\w*\b`/`\bguarantee\w*\b`), `"reviews"` (a rating pattern like "4.9 out of 5"/"4.9 stars", or a review-count pattern like "9,000 reviews"), `"financing"` (a `$X/mo` figure or one of `vocab.FORBIDDEN_LENDER_NAMES`), or `"price"` (any other dollar amount) -- checked in that order, `None` otherwise (falls through to the unchanged `match_claim` word-overlap path). Each locked topic has its own evaluator: `evaluate_warranty_claim` (ok only if the ad claim IS one of `vocab.ALLOWED_WARRANTY_SENTENCE`/`ALLOWED_WARRANTY_SPEC_VALUE`/`ALLOWED_WARRANTY_SPEC_LABEL`, or verbatim contains a verified `warrant*`-id claim's own text -- same rule `find_warranty_violations` already enforces on page copy, applied here to the ad's spoken claim); `evaluate_reviews_claim` (ok only if the ad's stated rating/count equals the live `reviews-live` claim within rounding -- rating to one decimal, count within 1%; no live claim = no match, never a placeholder); `evaluate_financing_claim` (always an overclaim today -- no lender-quote source exists anywhere in this codebase yet, so any financing figure/lender name is compared against the one true fact, `vocab.ALLOWED_FINANCING_SENTENCE_NO_LENDER`, and always loses until Caleb wires up a real quote); `evaluate_price_claim` (item 2). A failure on warranty/reviews/financing is reported as `AD OVERCLAIM: "<ad text>" — verified fact: "<claim text>"`; a price failure keeps item 2's specific message. `gate_ad_brief_claims`'s signature grew `product=None, reviews_claim=None, financing_lender=None, policy="stop", log=None` and now returns `(matched, overclaims)` instead of a bare `matched` list (every existing caller/test updated).
4. **`ad_overclaim_policy` config flag.** `claims/config.json` and `ground.DEFAULT_CONFIG` both gained `"ad_overclaim_policy": "stop"` (default). In `gate_ad_brief_claims`: a plain (non-locked) unmatched claim always raises `ClaimsGateFailure` under either policy. A locked-topic overclaim also raises under `"stop"` (folded into the same failure's `items`, alongside any plain-unmatched items). Under `"warn"`, locked-topic overclaims are returned instead (`overclaims`, non-empty) and the run continues: `cli.cmd_run` writes them into `REVIEW.md` under a bold **AD OVERCLAIMS — page corrected, ad needs fixing** heading (`write_review_md`'s new `ad_overclaims` param), logs each one (`gate_ad_brief_claims`'s own `log.event("ad_claims", ...)` call, both policies), and threads them into every cartridge's writer call (`write_and_gate_page`/`write_page`'s new `ad_overclaims` param) as a `## DO NOT REPEAT these ad statements; use the verified fact instead` system-prompt block, one line per item with its claim and verified fact. Documented both settings in `README.md`'s new "Config" section.
5. **Tests.** `tests/test_claims.py`: `test_false_warranty_claim_is_ad_overclaim_never_matched` ("free lifetime warranty if it doesn't work" -> `AD OVERCLAIM`, topic `warranty`, never `matched`); `test_exact_allowed_warranty_sentence_matches`; `test_false_review_stats_claim_is_ad_overclaim` / `test_review_stats_claim_matching_live_figures_passes` / `test_review_stats_claim_with_no_live_data_is_ad_overclaim` ("4.9 out of 5 with 9,000 reviews" against a live "4.6 / 8,200" claim -> `AD OVERCLAIM`; matching figures -> matched); `test_financing_claim_is_always_an_overclaim_with_no_configured_lender`; `test_price_claim_matches_current_product_price_regardless_of_wording` / `test_price_claim_matching_no_product_price_is_unmatched_with_specific_message`; `test_warn_policy_does_not_stop_on_locked_topic_overclaim` / `test_warn_policy_still_stops_on_non_locked_unmatched_claim` / `test_stop_policy_folds_overclaims_and_unmatched_into_one_failure`; every pre-existing `gate_ad_brief_claims` call site updated for the new `(matched, overclaims)` return and, for price claims, a `product=FUJI_PRODUCT` kwarg (word-overlap price matching no longer exists to test). `tests/test_cli_run.py`: `test_run_reaches_price_based_product_inference_in_the_real_pipeline_order` -- a fake-client dry run of `cmd_run` with no model named and only `"$5,450"` in `claims_made`, `--product` unset, PASSes and grounds on the Mini (`facts_pack.product.slug`), with `REVIEW.md` showing the price-inference warning -- proof item 1's reorder actually reaches product-picking in the real code path, not just in `test_ground.py`'s isolated unit test of `pick_product_with_warning` (cycle 9).

### Verify (server, real Claude calls, foreground, one at a time)

- **Tests**: 259 passed on both the Mac clone and the server (247 pre-existing + 12 new this cycle).
- **`adv run fixtures/price-comparison-v2.mov`, default policy "stop".** The item this cycle was actually about is fixed and holds across every attempt below: the run log shows `run: product inferred from quoted price $5,450 = Mini` followed immediately by `gate_result: PASS 1 ad claim(s) matched` -- the ad-claims gate now runs after product-picking and matches the "$5,450" claim purely on the numeric anchor against the Mini's real price, every single time. **A full end-to-end PASS was not reached, though, in 5 real attempts (~$2.2 combined cost)** -- every attempt cleared `ad_claims` cleanly and then STOPped (or, on the 5th, hit the 150k token budget) at the unrelated `page_json` writer-repair-loop stage: a digit/claim_id violation on the ad's own hedged "$200 a month" cost-comparison language reproduced in page prose (attempts 1, 2, 4, 5), a warranty-wording near-miss (attempt 2), and a word-count shortfall (implicitly, via repairs). This is a pre-existing gap, not a Cycle 10 regression or in scope for this cycle's four fixes: it's the writer-repair loop (fix cycles 4/6) not converging within `MAX_REPAIR_ATTEMPTS`/the token budget on this fixture's unusually numeric-heavy cost-comparison content, now visible for the first time only because the ad-claims gate no longer blocks earlier. Logged as a new "Not fixed" item below rather than patched blind -- out of this cycle's assigned scope (product-picking order + locked-topic ad claims only). Per-attempt detail: attempt 1 STOP page_json:product-page (attempts=3 repairs=2, $0.3047); attempt 2 STOP page_json:longform (attempts=3 repairs=2, $0.3704); attempt 3 STOP page_json:article (attempts=5 repairs=3, $0.5735); attempt 4 STOP page_json:longform (attempts=4 repairs=2, $0.4948); attempt 5 budget exceeded at 176,854 tokens (article converged on attempt 3, product-page on attempt 1, but the run ran out of budget before longform).
- **`adv run fixtures/product-features-v2.mov`, default policy "stop".** STOP at `ad_claims`, 3 unmatched items: `"It's only 31 by 32 inches"` (overlap 0.0, genuine pre-existing gap, unchanged from Cycle 8/9), `"It can be started from an app on your phone"` (overlap 0.333, genuine pre-existing gap -- no spec page states app control, per Cycle 8 item 3), and, the actual fix cycle 10 result: `"It has a free lifetime warranty"` reported as `AD OVERCLAIM: "It has a free lifetime warranty" — verified fact: "Peak Saunas warranty covers, from date of delivery: heating elements 7 years; ..."` -- **never MATCHED**, unlike Cycle 9's verification of this same ad, where the equivalent claim cleared 0.667 word overlap against `gbrain-allowlist-lifetime-warranty` and was reported as MATCHED (the bug Problem B is about).
- **Same fixture, policy toggled to "warn" (server-only edit to `claims/config.json`, never committed).** STOP at `ad_claims`, 2 unmatched items -- `"It's only 31 by 32 inches"` and `"It can be started from a phone app"` (both non-locked, still stop under either policy) -- with the warranty claim no longer among the STOP items (it's a locked-topic overclaim, allowed through under "warn"). Confirms fix 4: `ad_overclaim_policy` correctly separates "locked-topic overclaim" (policy-gated) from "ordinary unmatched claim" (always stops). Restored `claims/config.json` to `"ad_overclaim_policy": "stop"` via `git checkout --` immediately after; `git status` confirmed clean (also reverted an unrelated `claims/products.json` diff each real run leaves behind -- the routine live-price-refresh `"generated"` date bump, pre-existing behavior, not part of this cycle).
- **`adv run fixtures/hidden-costs-v2.mov` regression check, default policy "stop".** First attempt STOPped on an unrelated pre-existing word-count shortfall (`page_json:article`, 965/1000-1600 words); second attempt **PASS** -- `run_result: PASS attempts=4 repairs=1`. Word counts: longform 942, product-page 347, article 1,089. Tokens 117,945 total (5 calls), estimated cost **$0.4923**. `adv review` run on this PASS -- wrote `article-review.html`, `longform-review.html`, `product-page-review.html`. No regression from this cycle's changes: `gate_result: PASS 0 ad claim(s) matched` (no ad claims in this fixture, unaffected by the reorder), `run: product not named in ad; defaulted to Fuji` (unchanged Cycle 8 behavior).

### Not fixed / flagging for Caleb

- **`price-comparison-v2.mov` still doesn't reach a full PASS, though for a different reason than Cycle 9's.** The bug this cycle was assigned (ad-claims gate blocking price inference) is conclusively fixed -- confirmed on every one of 5 real attempts above. What's left is a pre-existing, out-of-scope difficulty: this ad's cost-comparison angle ("$200 a month" membership vs. "$5,450" sauna) pushes the writer toward numeric cost-comparison prose in page copy, which repeatedly trips the unrelated digit/claim_id page-level gate (fix cycle 4/6's `_trigger_reason`) -- the writer keeps citing its own version of "$200 a month" / "$2,400 a year" without a claim_id, or takes 3+ repair attempts to stop doing so, sometimes exhausting the 150,000-token budget across 3 cartridges before finishing. Two options for Caleb, both outside this cycle's scope: (a) raise the per-run token budget (or lower `MAX_REPAIR_ATTEMPTS`'s cost by trimming exemplars further) so this fixture's slower convergence has room to finish; (b) a real verified claim for "a studio membership averages about $200/month" (sourced, so the writer can cite it with a claim_id instead of restating the ad's own hedge) would remove the *reason* the writer keeps writing an uncited number in the first place.

## Cycle 11 (attributed_to_customer for speaker cost math; live-price refresh no longer dirties the tree; budget-aware repair skip)

**Problem A.** `price-comparison-v2.mov` passes the ad-claims gate (fix cycle 10) but fails in the writer repair loop and exhausts budget (Cycle 10's "Not fixed" note). The ad's substance is the speaker's own cost math (memberships around $200 a month, $2,400 a year, break-even after 28 months) -- her own hedge/estimate, in `ad_brief.speaker_experience`, not a checkable claim. The writer reproduces it, the digit rule demands a `claim_id`, none exists, and the repair loop thrashes.

1. **Schema.** Every cartridge's plain narrative-paragraph nodes (product-page `angle_section.paragraphs`; article `open`, `body_sections.paragraphs`, `close.paragraphs`; longform `problem.paragraphs`) gained an optional `"attributed_to_customer": {"type": "boolean"}` property. Deliberately NOT added to any heading, `proof_bullets`, `specs_table`, `trust_strip`, `turn_section.criteria`, `how_it_works.steps`, `faq`, or a `hero`/`final_cta` financing/price line -- those stay verified-facts-only.
2. **Writer prompt (`adv/write.py` `GLOBAL_VOICE_BLOCK`).** A new paragraph: a number that comes only from the ad speaker's own statements may appear ONLY inside a plain narrative paragraph, phrased explicitly as her own estimate, with that paragraph's own `attributed_to_customer` set true -- never as fact in the brand's voice, never carrying a `claim_id`, and never in a heading/proof bullet/spec row/FAQ answer.
3. **Gate (`adv/claims.py`).** `gate_page_json` gained an `ad_brief=None` param; when given, `speaker_numbers(ad_brief)` extracts every normalized number (comma-stripped, `$`/`%` kept) from `ad_brief.speaker_experience` + `transcript_or_text`, threaded into `validate_page_claim_ids`/`_trigger_reason` as `speaker_number_set`. For a node marked `attributed_to_customer`, `_trigger_reason` compares the sentence's own numbers against that set instead of the plain digit/$/% check -- every number matched is exempt from the `claim_id` requirement; any number NOT in the speaker's own words still requires one, named in the failure message. The trigger-WORD check (medical/clinical/proven/rated/reviews/study/emf) is unaffected either way. `_ATTRIBUTABLE_PATH_RE` enumerates the exact page.json paths the flag is honored on (the same five schema locations from item 1); `attributed_to_customer` anywhere else is rejected outright by `validate_page_claim_ids` as its own gate failure, regardless of phrasing -- headings/proof/specs/FAQ can never be marked attributed, enforced in code, not just prompt guidance. New `find_missing_attribution` requires every attributed item's own text to visibly read as attributed ("customer", or "she"/"he"/"they" plus "told us"/"estimated"/"said") -- called from `gate_page_json` (pre-render, feeds the repair loop) and, as a backstop, from `render.render_page` (post-render, same defense-in-depth pattern as the EMF/leaked-claim-id checks).
4. **Renderer (`adv/render.py`).** No template change needed -- the existing pipeline already renders every `.text` field as literal visible prose, so an attribution phrase the gate required is, by construction, visible on the page. Added the `find_missing_attribution` post-render call described above as a backstop.
5. **Tests (`tests/test_claims.py`).** The exemption (a `attributed_to_customer` paragraph whose numbers all appear in `speaker_experience` needs no `claim_id`); a number NOT in the speaker's own words still fails, message naming it; `attributed_to_customer` on a `turn_section.criteria` (proof) node rejected outright even with correct attribution phrasing; `find_missing_attribution` unit tests (flags a marked item with no visible attribution, passes with "customer", passes with pronoun+verb, ignores unmarked items); `gate_page_json` end-to-end with the real price-comparison-v2.mov scenario (does not raise) and a regression guard that the same page still STOPs when no `ad_brief` is passed (every pre-cycle-11 caller).

**Problem B.** Every real `adv run` refreshes prices/images into `claims/products.json` on disk (`prices.refresh_price_data`) and leaves the git tree dirty every cycle (Cycle 10's verify notes reverting this "routine... pre-existing behavior" each time).

**Fix.** `adv/prices.py`: `refresh_price_data` no longer writes `products_path` (`claims/products.json`) at all -- removed the `products_path.write_text(...)` call. `claims/products.json` is read-only now, hand-curated data that changes only when Caleb edits it. The live refresh's only write anywhere is the pre-existing raw-feed cache at `cache_path` (`runs/products-cache.json`, inside `get_live_products`/`save_cache`, unchanged). The in-memory merge (`merge_products(old_products, live_products)`) still runs every call exactly as before -- `old_products` freshly reloaded from `claims/products.json`, `live_products` either fetched fresh or reused from the still-TTL-fresh raw cache -- so this run's facts_pack/price claims are unaffected; only the disk write is gone. **Tests (`tests/test_prices.py`):** `refresh_price_data` leaves `claims/products.json` byte-identical after a live refresh with a changed price, while the in-memory result and the returned price claim reflect the new price, and the only file written is the raw-feed cache; a second call (simulating the next `adv run`) still reflects the price change, proven not to re-fetch (a `fetch_page` that raises if called) since the cache is still within its 60-minute TTL.

**Problem C (budget).** The price-comparison-v2.mov run hit the 150,000-token cap mid-repair (Cycle 10 verify: 5 real attempts, ~$2.2 combined, the 5th hitting `BudgetExceeded` outright instead of a clean STOP).

**Fix (`adv/cli.py` `write_and_gate_page`).** Every attempt now logs the remaining token budget before deciding what to do (`write.<cartridge>: attempt N: budget remaining X tokens (Y/Z used)`). A running list of this cartridge's own `write_page` call costs (`call_token_costs`, tokens actually spent per call, initial write + repairs) feeds an average; before attempt 2+ (a repair, never the initial write), if the remaining budget is below that average, the repair is skipped -- logged `repair skipped: budget (remaining X tokens < average call cost Y tokens)` -- and the loop raises `ClaimsGateFailure` (with `.budget_skipped = True`) using the last attempt's own failures, instead of calling `write_page` again and risking a hard `BudgetExceeded` mid-call. `cli.cmd_run`'s existing `except ClaimsGateFailure` handler (STOP, exit 2, a specific reason in `unmatched_claims.json`) now covers this path; `except BudgetExceeded` (exit 3, generic "budget exceeded") is reserved for a cap hit somewhere budget-skip doesn't reach (the initial write, ingest, etc.), unchanged. **Tests (`tests/test_repair_loop.py`):** a repair is skipped (not attempted) once remaining budget can't afford the average call cost, `budget_skipped` set, both log lines present; a larger budget still repairs and recovers exactly as before this fix; a skipped repair raises `ClaimsGateFailure`, never a bare `BudgetExceeded`.

**Test count:** 274 passed (259 pre-existing + 15 new: 10 in test_claims.py, 2 in test_prices.py, 3 in test_repair_loop.py).

### Verify (server, real Claude/whisper calls, foreground, one at a time)

- **Tests**: 274 passed on both the Mac clone and the server.
- **`adv run fixtures/price-comparison-v2.mov`** (`20260910-0101-price-comparison-v2`): `run: product inferred from quoted price $5,450 = Mini` / `gate_result: PASS 1 ad claim(s) matched` (Cycle 10's fix still holds). `longform` and `product-page` both **PASS on attempt 1**, no repair -- the attributed_to_customer path never even needed to fire for either. `article` **STOP** after 3 attempts: attempts 1-2 failed on an unrelated, pre-existing word-count shortfall (962/995 words, required 1000-1600 -- nothing to do with this cycle); attempt 3 (the model's last shot, `MAX_REPAIR_ATTEMPTS=2`) produced two new, clearly-diagnosed failures instead of the old generic "contains a dollar amount"/thrash: (a) a *second*, un-flagged `$2,400` restated in `body_sections[1].paragraphs[1]` with no `attributed_to_customer` set at all -- the model didn't apply the new flag consistently across the whole page; (b) `open[1]` WAS marked `attributed_to_customer` and its numbers ($200/mo, $2,400/yr) matched the speaker's own words (no digit/claim_id complaint) but its phrasing ("She decided...", "According to her...") didn't contain the required attribution verb ("told us"/"estimated"/"said") -- item 3's attribution-visibility check caught exactly this and named it precisely. **run_result: STOP attempts=5 repairs=2**, 6 model calls, 139,650/150,000 tokens used (93%), 175s elapsed (well under the 300s wall clock), estimated cost $0.5948 -- no `BudgetExceeded`, no thrash, a clean STOP with two specific, fixable reasons in `unmatched_claims.json`. Budget-remaining logged on every attempt throughout (confirmed in `runs/20260910-0101-price-comparison-v2.log`); the budget-skip path itself didn't need to fire this run (budget never got tight enough), which is itself evidence this fixture no longer risks exhausting the cap the way Cycle 10 saw (5 attempts, ~$2.2, one hitting the cap outright).
- **`git status` on the server after the run above: clean.** `claims/products.json` untouched byte-for-byte despite the run's live price refresh (product Mini, $5,450); `runs/products-cache.json` shows a fresh write at the run's own timestamp. Confirms Problem B's fix directly, live, on the fixture that most exercises the price-refresh path.

### Not fixed / flagging for Caleb

- **`price-comparison-v2.mov`'s `article` cartridge didn't reach a full PASS on this verify run**, though both Problem A and Problem C are conclusively working as designed (see above) -- what's left is real model behavior, not a gate/loop bug: the model needs more than one real attempt to (a) apply `attributed_to_customer` consistently to every sentence carrying the speaker's own numbers, not just the first, and (b) phrase an attributed sentence with one of the three exact attribution verbs this cycle's gate checks for, not just a third-person pronoun. Two of `article`'s three total attempts were consumed by an unrelated, pre-existing word-count shortfall, leaving only one real attempt to work through the new attribution requirement -- not a sign the requirement itself is unreachable. The sweep below re-runs this same fixture; if it still doesn't converge there, worth considering either loosening the attribution-verb list (a real-world sentence has more ways to read as attributed than three verbs) or examples in the writer prompt showing a correctly-attributed multi-sentence paragraph.

## Cycle 12 (prompt size/budget; incidental numerals; warn-policy broadened + alternative-claim classification; semantic matcher; g Brain unverified-feature sweep)

**Problem 1 (prompt size and budget).** The average article writer call ran ~36,800 tokens because `write.load_exemplars` sent each cartridge exemplar whole -- `cartridges/article/exemplars/best-sauna-brands-2026.md` alone is 5,600+ words. `Budget`'s 150,000-token / 12-call caps left little room for a numeric-heavy fixture (Cycle 10/11's `price-comparison-v2.mov`) to afford a second repair on every cartridge.

1. **Exemplar trimming (`adv/write.py`).** `load_exemplars` now trims every `.md`/`.txt` exemplar's text to its first `EXEMPLAR_MAX_WORDS` (700) words via a new `_truncate_words` helper, before wrapping it as `{"reference_article": ...}`. The pre-existing `limit=2` (at most 2 exemplars) is unchanged -- both article exemplars already hit that cap. A `.json` (page.json-shaped) exemplar is never trimmed this way (there are none in this repo today; truncating structured JSON by word count would produce invalid JSON) -- the 2-exemplar cap is the control for that case. Still never sent on a repair call (`write_page`'s `exemplars = load_exemplars(...) if not revision_note else []` -- unchanged, already the case before this cycle).
2. **Budget caps (`adv/budget.py`).** `Budget.__init__` defaults raised: `tokens` 150,000 -> 220,000, `calls` 12 -> 14. `wall_s` stays 300 -- measured on the server this cycle (see Verify below) rather than raised blind to 420 as a fallback.
3. **Prompt-size logging (`adv/write.py` `write_page`).** Before every `client.messages.create` call, logs `write.<cartridge>: prompt size: ~<N> tokens (estimate, <M> chars)` -- a rough chars/4 estimate of `system` + every string `messages` entry, logged before the call returns real `usage.input_tokens` (already logged via `log.call`), so a prompt-bloat regression shows up in the log immediately rather than only after the fact.
4. **Tests.** `tests/test_write.py`: exemplar trimming to <=700 words on the real article cartridge and on synthetic short/long fixture files (trimmed to exactly 700 words, word-for-word), the 2-exemplar cap still holds with 3 files present, and the prompt-size log line appears on every `write_page` call. `tests/test_budget.py`: `Budget()` defaults now 220,000 tokens / 14 calls / 300s wall clock.

**Problem 2 (incidental numerals).** The writer wrote "driving to a studio at 7 a.m." in an uncited narrative sentence and tripped the plain digit/claim_id rule (fix cycle 4) -- true, but there's no verified claim for an ad-speaker's illustrative time of day, so every such sentence either forced an unnecessary repair call or got rewritten away from natural phrasing.

1. **Writer rule (`adv/write.py` `GLOBAL_VOICE_BLOCK`).** New paragraph: outside a claim-cited sentence, write numbers as words -- "seven in the morning" not "7 a.m.", "two hours" not "2 hours", "five-figure" not "5-figure" -- never a numeral for a time, a count, or an age without that sentence's own `claim_ids` citing a verified claim for it.
2. **Deterministic pre-repair (`adv/cli.py`).** New `convert_incidental_numerals(text)`: numerals 1-12 immediately followed by `a.m.`/`am`/`p.m.`/`pm`/`o'clock` become "seven in the morning" / "three in the afternoon" / "nine o'clock" (`_TIME_NUMERAL_RE`); a standalone numeral 1-12 elsewhere becomes its word form (`_STANDALONE_NUMERAL_RE`), with capitalization preserved at a sentence start. Excludes a price/percentage/thousands-grouped/decimal figure and a product capacity token (`"2-Person"`) by construction (lookbehind/lookahead on `,digit`/`.digit`/`%`/`-Person`) -- those still need a real repair call. Wired into `apply_deterministic_fixes`'s existing branch dispatch: a failure whose issue is exactly `"(contains a number)"` (not `"(contains a dollar amount)"` or `"(contains a percentage)"`, and not the `attributed_to_customer` variant) routes to this substitution, same `.text`-path-resolution pattern as the existing trigger-word branch. Because `_trigger_reason` only raises "contains a number" for a text field with no `claim_ids` at all, this only ever touches non-cited narrative text, never a claim-cited sentence -- by construction, not by checking the flag again.
3. **Tests.** `tests/test_repair_loop.py`: `convert_incidental_numerals` unit tests (am/pm/o'clock, standalone counts, sentence-start capitalization, numbers outside 1-12 left alone, prices/percentages/thousands left alone, the capacity token left alone); `apply_deterministic_fixes` resolves a `"(contains a number)"` failure via this path and leaves a dollar/percentage failure alone; `write_and_gate_page` resolves a real "7 a.m." failure via the deterministic pass with zero repair calls.

**Problem 3 (policy semantics + alternative-claim classification).** Today's `"warn"` policy (fix cycle 10) only exempted a *locked-topic* (warranty/reviews/financing/price) overclaim from stopping the run -- a plain unmatched ad claim on any other topic still stopped the run under either policy, so `"warn"` rarely helped in practice (Cycle 11's own sweep: 6 of 7 fixtures carried a plain unmatched claim alongside any locked-topic overclaim, and the plain one always stopped the run first). Separately, a red-X comparative-still ad claim about the competing/comparison option ("the other option leaves you drained") was never a checkable claim about Peak's own product at all, but had no distinct handling.

1. **Broadened `"warn"` (`adv/claims.py` `gate_ad_brief_claims`).** Under `"warn"`, EVERY unmatched-or-overclaimed claim -- plain or locked-topic -- is now dropped from what the writer may use instead of stopping the run: returned in a new `not_repeated` list (replaces the old `overclaims` return position), each item normalized via new `_not_repeated_item` to carry a `message` (and `verified_fact` when a locked-topic evaluator found one; `None` for a plain unmatched claim, which has no single fact to point to). `cli.cmd_run` writes `not_repeated` into `REVIEW.md` under a bold **AD CLAIMS NOT REPEATED ON PAGE — ad needs fixing** heading (`write_review_md`'s `ad_not_repeated` param, renamed from cycle 10's `ad_overclaims`) and threads it into every cartridge's writer call as the existing `## DO NOT REPEAT` system-prompt block (`write_and_gate_page`/`write_page`'s `ad_not_repeated` param, same rename). Under `"stop"` (default), behavior is unchanged from cycle 10: any unmatched-or-overclaimed claim, plain or locked-topic, still stops the run (`stop_items = plain_unmatched + overclaims`, raised as before).
2. **Alternative-claim classification (`adv/claims.py`).** New `classify_ad_claim_about(text)` -> `"alternative"` or `None`, via `_ALTERNATIVE_SUBJECT_RE` matching the claim's own grammatical subject ("comparison option", "competing product(s)/model(s)", "competitor('s) product(s)/model(s)", "the other option/brand", "other brands") -- keyed on the real fixtures' actual phrasing (`docs/SWEEP-2026-09-10.md` fixtures 5-7: "Comparison option ...", "Competing products ...", "Competitor products ..."), not on the specific adjectives an ad happens to use. Deliberately narrower than "mentions a competitor" -- a claim like "Competitor saunas leak dangerous levels of EMF radiation" (a specific factual assertion about a named rival, pre-dating this fix) stays on the ordinary unmatched-claim path. `gate_ad_brief_claims` checks this first, before locked-topic classification or word-overlap matching: an "alternative" claim is collected into a new `alternative_claims` return list, never matched against anything, and never contributes to a stop under either policy. `cli.write_review_md` lists it under **Ad statements about alternatives (not repeated)**, purely for visibility -- not passed to the writer as a "DO NOT REPEAT" item (no single verified fact exists to contrast it against; the pre-existing guardrail in `GLOBAL_VOICE_BLOCK` -- "Competitor statements are only ever the speaker's own experience, never a sourced fact about a competitor" -- already covers it).
3. **`gate_ad_brief_claims` return shape.** `(matched, not_repeated, alternative_claims)` -- every caller/test updated (was `(matched, overclaims)`).
4. **Tests.** `tests/test_claims.py`: warn no longer stops on a locked-topic overclaim (unchanged assertion, renamed variable) or on a plain unmatched claim (new -- the behavior that actually changed); stop still folds both kinds into one failure (unchanged); an alternative claim never matches and never stops the run under either policy, alone or mixed with a real plain-unmatched claim; `classify_ad_claim_about` recognizes the real fixture phrasings and leaves an ordinary spec claim alone.

**Problem 4 (semantic matcher).** Token-overlap alone misses a claim that's true but phrased nothing like the verified claim's own words -- "4-in-1: near, mid, far infrared + red light" shares almost no tokens with either the full-spectrum or red-light allowlist claim's text, and neither claim states the digit "4" (it's a combination count across two claims, not a single sourceable fact) or "1".

1. **`adv/semantic_match.py` (new).** `semantic_match_claims(claims_made, verified_claims, client, model, budget=None, log=None)`: ONE real Claude call (`claude-sonnet-5`, `max_tokens=1500`, `thinking: disabled`) per run, given every ad claim and up to `MAX_VERIFIED_CLAIMS_FOR_PROMPT` (120) verified claims trimmed to `{id, text}` only. System prompt instructs: map only on equivalent meaning; any number in the ad claim must also appear in the mapped verified claim's text; a comparative/superlative word ("best", "only", "#1") always maps to null; never map a warranty/review-count/financing/price claim (those stay with the locked-topic evaluators). Returns `{ad_claim_text: verified_id_or_None}`; charges the run's `Budget` like any other real call; logs each mapping (`ad_claims.semantic_match: '<claim>' -> '<id-or-None>'`). Any failure (bad JSON, non-dict response, an API/budget exception) is caught, logged, and turns into `{}` -- pure fallback to word-overlap matching, never a hard failure.
2. **Numeric-guard enforcement stays in code (`adv/claims.py` `match_claim`).** A `semantic_mapping` param, consulted before word-overlap: a proposed id is accepted ONLY if the SAME numeric-token guard `match_claim` already enforces for overlap matching also passes (every numeric token in the ad claim is a subset of the mapped verified claim's own numeric tokens) -- the model's semantic judgment can never override this. New `_combo_idiom_numbers`/`_COMBO_COUNT_IDIOM_RE`: a marketing "N-in-1" combination-count idiom's digits (both the count and its own trailing "1") are excluded from the ad claim's numeric-guard tokens, the same way a product's own capacity token ("2-Person") was already excluded elsewhere in this file -- without this, "4-in-1: near, mid, far infrared + red light" could never pass the guard against either real target claim (`gbrain-allowlist-360-full-spectrum`: "360° full spectrum infrared heater placement."; `gbrain-allowlist-red-light`: "Medical-grade red light therapy (included standard)." -- neither contains "4" or "1"). A mapping to an unknown id, a null mapping, or no mapping at all falls straight through to the pre-existing overlap path, unchanged.
3. **Wired into `adv/cli.py` `cmd_run`.** Computed once per run, right before `gate_ad_brief_claims`, against the full verified-claims universe (post price/PDP-claim merge); passed through as `gate_ad_brief_claims`'s new `semantic_mapping` param. Never gated by `ad_overclaim_policy` -- it only ever widens what can match, on either policy.
4. **Tests (fake client).** `tests/test_semantic_match.py`: a successful mapping is parsed, charges the budget, and is logged; falls back to `{}` on invalid JSON, a non-dict response, and a raised exception; verified-claims trimming keeps only `id`/`text` up to the 120-item cap; `match_claim` accepts a semantic mapping when the numeric guard passes (including the real "4-in-1" -> full-spectrum and "medical-grade panel" -> red-light mappings this fix is meant to enable), rejects one when the ad claim's own number isn't in the verified claim's text, ignores a mapping to an unknown id, and falls through to overlap matching on a null mapping or with no mapping at all.

**Problem 5 (unverified features -- g Brain search).** Several ad stills' unmatched claims describe apparent product/program features (Peak Wellness Club, a live leaderboard, the Sauna Lounge, expert protocols, Longevity Lab) that don't exist in `claims/verified.json` -- worth checking whether they're real, current offerings g Brain knows about (and just never got a verified claim written) versus invented ad copy.

Searched g Brain (`search`/`get_page`, product/concept/kb/policy/spec/campaign/outline-doc page types only -- never customer/order/email/support_ticket/conversation/slack_log/person) for "Peak Wellness Club", "leaderboard", "sauna lounge", "expert studies", "actionable protocols", and "Longevity Lab". All four are real, current offerings -- added to `claims/pending.json` with status `"needs-caleb"` (never `verified.json`), the exact quoted sentence, and the slug as source:

- **`gbrain-peak-wellness-club-real-offering`** -- confirmed real, free-with-purchase platform (`outline/sales/peak-wellness-club-team-knowledge-base-e0aab96a`). Flagged a pricing discrepancy found along the way: two allowed-type pages consistently describe a "60-day free trial" then "$49/month," while a separate (non-allowed-type, not opened) gbrain record instead says "Currently FREE -- no trial, no cost" and explicitly forbids saying "60-day trial" or "$49/month." Caleb needs to resolve which is current before any pricing detail here reaches `verified.json`.
- **`gbrain-pwc-leaderboard-sauna-lounge-real-features`** -- both confirmed real (`outline/customer-support/how-to-use-the-circle-app-peak-wellness-club-89f46819`). Flagged that a separate allowed-type engagement audit (`outline/operations/pwc-sauna-lounge-engagement-audit-mar-21-2026-855a6fd4`, dated 2026-03-21) found the Sauna Lounge had near-zero real member activity (16 of 18 posts with zero engagement) -- the feature is real, but an ad implying a bustling community should be checked against current numbers.
- **`gbrain-pwc-expert-protocols-real-feature`** -- "Celebrity & Expert Protocols" confirmed real (same source as above); the "studies" half of "expert studies + actionable protocols" is NOT confirmed by any allowed-type page (a related research-studies library exists only on a page type outside the retrieval allowlist) -- flagged for Caleb to confirm "protocols" vs. "studies" framing.
- **`gbrain-longevity-lab-real-offering`** -- confirmed real, high-ticket, application-only, and explicitly never included with a sauna purchase (`outline/customer-support/peak-saunas-training-hub-v3-52ad71aa`). Not found as a literal ad claim in any of the seven fixtures this cycle -- flagged defensively per the assigned search list, not because a current ad references it.

**Test count:** 306 passed (274 pre-existing + 32 new: 4 in test_claims.py, 9 in test_repair_loop.py, 5 in test_write.py, 1 in test_budget.py, 13 in `tests/test_semantic_match.py` -- a new file).

### Verify (server, real Claude calls, foreground, one at a time)

- **Tests**: 306 passed on both the Mac clone and the server.
- **`adv run fixtures/price-comparison-v2.mov`** (`20260910-0146-price-comparison-v2`, default policy `"stop"`): **full PASS** -- `run_result: PASS attempts=5 repairs=2` (article attempt=2 repairs=1, product-page attempt=2 repairs=1, longform attempt=1 repairs=0). This is the first Cycle 10/11/12 verification run where this fixture reached a full PASS in one real attempt (Cycle 10: 5 real attempts, none passed; Cycle 11: STOP on `article` after 3 attempts, budget-skip fired). Elapsed **173.3s of the 300s wall-clock cap (58%)** -- direct evidence for item 1's budget decision (see `adv/budget.py`'s own comment): three cartridges with two real repairs total did not come close to needing more wall clock, so it stayed at 300s rather than being raised to 420s blind. Tokens **117,672/220,000 used (53%)**, 7/14 calls, estimated cost **$0.5201**. Per-call prompt sizes (item 1's new logging) ran **~10,600-12,700 tokens** each (`write.article: prompt size: ~12657 tokens`, etc.) -- down from Cycle 11's ~36,800-token average before exemplar trimming. Item 2 (incidental numerals) visibly working in the model's own output, unprompted by any repair: the product-page cartridge's attributed cost-math paragraph read "after about **twenty-eight months**" and "no driving to and from a studio" -- words, not "28 months" / a bare digit -- exactly the pattern the new `GLOBAL_VOICE_BLOCK` rule asks for. Item 4 (semantic matcher) fired and correctly declined to map the ad's price claim (`ad_claims.semantic_match: 'Infrared sauna is on sale right now for $5,450' -> None` -- correct: price claims are never supposed to get a semantic mapping, per the locked-topic carve-out in `SEMANTIC_MATCH_SYSTEM`) -- the existing numeric-anchor price evaluator (fix cycle 10) matched it instead, unaffected. No deterministic-fix-pass hits this run (the two real repairs were a word-count shortfall and a missing attribution phrase, neither of which item 2's numeral conversion or any other pre-repair pass targets) -- expected, not every run exercises every fix.
- **`adv run fixtures/product-features-v2.mov`** (`20260910-0149-product-features-v2`, default policy `"stop"`): **STOP at `ad_claims`**, same 2 items as Cycle 10's verification of this fixture (dimensions unmatched, warranty AD OVERCLAIM) -- confirms the `"stop"` path is unchanged by this cycle's broadened `"warn"` (item 3). Item 4's semantic matcher gave the clearest real evidence yet that it closes a genuine coverage gap: `'It plugs into a normal outlet so no need for an electrician' -> 'spec-mini-electrical'` -- a claim with essentially zero word overlap against that spec's own text, correctly matched on meaning -- and `'It has medical grade red light therapy' -> 'gbrain-allowlist-red-light'`; it correctly declined to map the warranty claim (`'It has a free lifetime warranty' -> None` -- locked-topic, left to the warranty evaluator, which then correctly flagged it as an AD OVERCLAIM) and the two claims with no real verified counterpart (`'It's only 31 by 32 inches' -> None`, `'It has free delivery' -> None`).
- **`git status` on the server after both runs: clean** (no committed file touched by either real run's live price refresh or gate work).

### Not fixed / flagging for Caleb

- **This cycle didn't get a real run to exercise `ad_overclaim_policy: "warn"`'s broadened behavior (item 3) or the alternative-claim classification (also item 3) against a live model** -- both real runs above used the default `"stop"` policy, which is unchanged from Cycle 10 and was the right thing to verify first (a regression there would be worse than a late look at `"warn"`). Sweep 3 (`docs/SWEEP-2026-09-10b.md`) exercises `"warn"` directly against all seven fixtures, including the ones with real alternative-claim and locked-topic-overclaim content (`still-levelup-4x5.png`, `still-infraredglow-4x5.png`, `still-unforgettable-4x5.png`) -- that sweep is this fix's real-world verification, not a gap.

### Mid-sweep correction: `_ALTERNATIVE_SUBJECT_RE` was missing "sauna"

Sweep 3's real run of `still-levelup-4x5.png` under `"warn"` surfaced a live bug in item 3's own alternative-claim classifier: the real ad claim `"Competing saunas lack red light therapy"` was NOT classified `"about: alternative"` (the noun list only had `product`/`model`, not `sauna`), so it fell through to ordinary word-overlap matching -- and word-overlap false-MATCHED it against Peak's own `warranty-terms` claim at 0.667 overlap (`ad claim "Competing saunas lack red light therapy" -> verified 'warranty-terms' (overlap 0.667)` in that run's `REVIEW.md`). That's the exact false-MATCHED-overclaim failure mode fix cycle 10 problem B was written to close, just for a competitor claim instead of a warranty one -- a real regression this cycle would otherwise have shipped. `_ALTERNATIVE_SUBJECT_RE` (`adv/claims.py`) now includes `sauna(s)` alongside `product(s)`/`model(s)` as a competitor-noun. This required updating four tests that used `"Competitor saunas leak dangerous levels of EMF radiation."` as a generic "plain unmatched claim" fixture (`tests/test_claims.py`'s `test_unmatched_ad_claim_stops`, `test_warn_policy_does_not_stop_on_plain_unmatched_claim_either`, `test_stop_policy_folds_overclaims_and_unmatched_into_one_failure`; `tests/test_cli_run.py`'s `test_run_stops_on_unmatched_claim`) -- with `"sauna"` now in the noun list, that exact string would itself classify as `"alternative"` and never stop the run, which isn't what those tests are checking. Replaced with `"Peak Saunas ships every order within two business days."`, a plain unsourced claim with no competitor-subject wording at all (`tests/test_cli_run.py`'s EMF-drop test, `test_run_drops_emf_claim_instead_of_stopping`, is unaffected -- it never reaches `classify_ad_claim_about` at all, since `ingest.drop_emf_claims` removes an EMF-mentioning claim before the ad-claims gate ever runs). New tests lock in both the fix (`"Competing saunas lack red light therapy"` / `"Competitor saunas have only basic manual controls"` -> `"alternative"`) and the still-narrower boundary (a claim with no `"competitor"`/`"competing"` grammatical subject, e.g. `"A review site rated Peak below every major competitor"`, stays a plain unmatched claim, not `"alternative"`). Test count: 308 (306 + 2 new).

**Second occurrence, same sweep, next fixture.** `still-infraredglow-4x5.png`'s real PASS run then surfaced the same failure mode a third time: `"Comparison product has no red light therapy"` word-overlap false-MATCHED `warranty-terms` at 0.6 overlap -- `_ALTERNATIVE_SUBJECT_RE` only ever paired `"comparison"` with `"option"`, never `"product"`. Rebuilt the regex to pair every subject word (`comparison`/`competing`/`competitor('s)`) with every noun (`option`/`product`/`model`/`sauna`) instead of an ad-hoc enumerated list of specific phrase combinations -- covers every real phrasing seen across both sweep fixtures in one pattern, rather than adding one more special case each time a new combination shows up. New test: `"Comparison product has no red light therapy"` / `"...has basic manual controls only"` -> `"alternative"`. Test count: 309 (308 + 1 new).

**Flagging for Caleb (added after both occurrences above):** `_ALTERNATIVE_SUBJECT_RE` is a hand-enumerated (subject word) x (noun) grid, and two real sweep runs each surfaced a phrasing this cycle's own first pass hadn't anticipated. The regex now covers every combination actually seen in real ad copy so far, but the next new phrasing (an ad calling the alternative "the leading brand" or "a rival sauna" with no `comparison`/`competing`/`competitor` word at all, say) would fall through the same way and risk the same false-MATCHED-overclaim failure mode via word overlap. `adv/semantic_match.py` (item 4) already solves an analogous problem for verified-claim matching with one real Claude call plus a hard guard in code -- worth considering the same shape here (a small classification call, or folding "is this about the alternative?" into the existing semantic-match call) instead of continuing to patch the noun list one sweep at a time. Also flagging a separate, pre-existing issue surfaced by this sweep: `find_warranty_violations` (fix cycle 6/7) flagged several honest, topical/hypothetical warranty-education sentences on `still-levelup-4x5.png` across 5 real attempts (e.g. "A sauna advertised with a lifetime warranty might only apply that term to the cabinetry...") -- out of this cycle's scope, spawned as a separate follow-up task rather than patched under sweep time pressure; see Sweep 3's own per-fixture notes in `docs/SWEEP-2026-09-10b.md`.

## Cycle 13 (warranty-wording deterministic pre-repair)

**Problem.** Sweep 2026-09-10b's `still-levelup-4x5.png` (`product-page`) and `still-unforgettable-4x5.png` (`longform`) both STOPped on the pre-existing (fix cycle 6/7) `claims.find_warranty_violations` gate: the writer's real repair attempt reproduced the same violation shape it was asked to fix -- a short label ("Limited lifetime warranty", "Backed by a limited lifetime warranty covering the cabin and heaters") or an honest-sounding paraphrase, never one of the three allowed forms (the fixed sentence, the fixed spec-table label/value pair, or a verbatim quote of the `warranty-terms` claim). Every real attempt spent a full model call re-writing the same field into the same non-compliant shape (see `runs/*still-levelup*.log`, `runs/*still-unforgettable*.log`, "gate FAIL ... warranty wording must be exactly"). Unlike a hype word or a leaked claim id, a warranty-wording violation's fix is always the same fixed sentence -- there was no deterministic pre-repair path for it before this cycle, so it always cost a real repair call, and the sweep showed that call doesn't reliably converge.

1. **Deterministic pre-repair (`adv/cli.py`).** New `_fix_warranty_violation(page, path, valid_claim_ids)`, wired into `apply_deterministic_fixes` as a new branch keyed on `"warranty wording must be exactly" in issue` (the exact prefix `claims.find_warranty_violations`'s own issue string always starts with) -- checked first, before the hype/leaked-id/trigger-word branches, since it needs its own field-shape handling rather than the generic path/substitute mechanism those use. For an ordinary text field (a proof point, a trust-strip line, a paragraph -- any node with a string field and a sibling `claim_ids` array), replaces the field with `vocab.ALLOWED_WARRANTY_SENTENCE` verbatim and sets `claim_ids` to `["warranty-terms"]` (new `_warranty_claim_id()` resolves the actual verified warranty claim id from this run's own `valid_claim_ids` -- `"warranty-terms"` if present, else the first id in that set containing "warranty", else `None`/no claim_ids write -- so this doesn't hardcode an id that could drift from claims/verified.json). For a spec-table row (detected by the failing field being named `"value"` with a sibling `"label"` key -- `find_warranty_violations` flags the row's `value` field specifically, never its `label`), sets the fixed pair (`label="Warranty"`, `value="Limited lifetime warranty (terms by component)"`) instead, plus `claim_id` if the row schema carries one. Runs before any model repair call, exactly like the existing hype-word/leaked-id/incidental-numeral deterministic fixes; `apply_deterministic_fixes` gained optional `log`/`cartridge_name` params (both default `None`, so every existing 3-arg test call is unaffected) so each field fixed this way logs `"deterministic fix applied: warranty sentence"` distinctly from the generic per-attempt `"deterministic fix applied: N field(s)"` summary line `write_and_gate_page` already logs.
2. **Writer prompt (`adv/write.py`).** `GLOBAL_VOICE_BLOCK`'s warranty paragraph gained a sentence: warranty may appear AT MOST ONCE on the whole page, as one proof point/bullet or one specs-table row (never both, never a second time anywhere else), and every occurrence must use the fixed sentence verbatim -- explicitly calling out that shortening it to a bare label ("Limited lifetime warranty") or paraphrasing it, even an honest paraphrase, is not allowed. This doesn't replace the deterministic fix (item 1 still catches a violation if the model writes one anyway) but should reduce how often item 1 needs to fire at all.
3. **Tests (`tests/test_repair_loop.py`).** `test_apply_deterministic_fixes_resolves_a_warranty_proof_point` -- the real sweep failure string ("Backed by a limited lifetime warranty covering the cabin and heaters.") is replaced with the exact allowed sentence and `claim_ids` set to `["warranty-terms"]`; re-running `find_warranty_violations` on the fixed page confirms zero violations left. `test_apply_deterministic_fixes_resolves_a_warranty_paragraph` -- same for a topical paragraph ("A sauna advertised with a lifetime warranty might only apply that term to the cabinetry."), the exact honest-education sentence flagged live on `still-levelup-4x5.png` per Cycle 12's "Flagging for Caleb" note. `test_apply_deterministic_fixes_resolves_a_warranty_spec_table_row` -- a bad `specs_table` row's `value` field (flagged at a path ending `.value`) gets the fixed label/value pair and `claim_id` set. `test_apply_deterministic_fixes_leaves_an_allowed_warranty_form_unchanged` -- the exact allowed sentence produces no gate failure in the first place, so nothing is touched. `test_write_and_gate_page_resolves_warranty_violation_via_deterministic_fix_without_a_repair_call` -- end-to-end through `write_and_gate_page` with a fake client returning exactly one response: the warranty violation resolves with zero repair calls (`len(client.messages.calls) == 1`), matching the existing pattern for the hype-word/leaked-id/incidental-numeral fixes. Test count: 314 (309 + 5 new).


### Verify (server, real Claude calls, foreground, one at a time)

`claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only (never committed) for the duration of this verification, then restored to `"stop"` afterward -- `git status`/`git diff --stat` confirmed clean (identical to the committed value) both after the policy change and after restoring it. `.venv/bin/pip install -e . -q` re-run first; `.venv/bin/python -m pytest -q`: **314 passed** on both the Mac clone and the server.

- **`adv run fixtures/still-levelup-4x5.png`** (`20260910-0306-still-levelup-4x5`): **PASS**, `run_result: PASS attempts=4 repairs=1`. `write.longform: deterministic fix applied: warranty sentence` fired once (the item 1 fix resolving the exact failure shape this cycle targets), zero model repairs caused by warranty wording. The one real repair (`article`, attempt 1) was an unrelated, pre-existing gap -- a "medical" trigger-word sentence with no claim_id ("ask for the specific claim behind the term medical-grade") -- not touched by this cycle. Confirmed live in the rendered page (`out/20260910-0306-still-levelup-4x5/longform/index.html`): the exact allowed sentence appears verbatim in the FAQ answer, a proof point, and the specs-table value; the specs-table row also carries the exact fixed label/value pair, `"Limited lifetime warranty (terms by component)"`. `adv review` written for all three cartridges.
- **`adv run fixtures/still-unforgettable-4x5.png`** (`20260910-0309-still-unforgettable-4x5`): **PASS**, `run_result: PASS attempts=4 repairs=1`. No field on this particular run's page mentioned "warrant" at all (confirmed: no warranty-sentence deterministic fix fired, no warranty gate failure in the log) -- the writer didn't reach for warranty copy this attempt, so item 1 wasn't exercised here, but the run that previously STOPped 4 of 5 times on this exact gate now converges to a clean PASS. The one real repair (`article`, attempt 1) was the same pre-existing "medical" trigger-word gap plus an unrelated word-count shortfall (980/1000-1600), both out of this cycle's scope. `adv review` written for all three cartridges.
- **`adv run fixtures/hidden-costs-v2.mov`** (`20260910-0312-hidden-costs-v2`, regression): **PASS**, `run_result: PASS attempts=5 repairs=2`, no ad claims in this ad (`gate_result: PASS 0 ad claim(s) matched`) and no warranty mention anywhere in the log -- unaffected by this cycle, matching Cycle 8/12's prior clean verifications of this fixture. `adv review` written for all three cartridges.
- **Deterministic warranty fixes this verification: 1** (still-levelup-4x5's `longform`). **Model repairs caused by warranty wording: 0** across all three runs -- item 1's design goal.
echo done
## Thursday queue (operator, 2026-09-10 03:10)
1. Semantic matcher variance: in run 20260910-0306-still-levelup the "4-in-1: near, mid, far IR + red light" claim was NOT matched, while the same claim matched in the infraredglow run. Fix: add an `aliases` list to allowlist claims (e.g. full-spectrum + red light: "4-in-1", "near, mid, far infrared plus red light", "medical-grade panel") checked deterministically before the model call; set the semantic call to temperature 0 and include aliases in its candidate text.
2. Warranty heuristic can rewrite honest warranty-education prose into the fixed sentence. Narrow the trigger to sentences that assert coverage (contains "cover", "covered", "backed", "guarantee", "years", "lifetime") and leave descriptive prose ("read the warranty terms before you buy") untouched.
3. From Caleb's swipe file, if approved: proof inside each reason (article rule), audience named in H1, failed-alternatives → mechanism stages in the article, consult-CTA variant per config, HSA/FSA via TrueMed trust line once verified.
4. Rubric from Caleb's draft-2 scores; run all seven fixtures under the policy Caleb picks; final Friday set.


## Cycle 14 — listicle cartridge + Shopify body builder

**Scope.** A fourth cartridge (numbered "N reasons" pre-sell, the format already shipped live at `/pages/5-reasons-to-love-peak-saunas`) and a Shopify body builder that turns any cartridge's rendered `index.html` into a paste-ready `body_html` snippet. No Shopify API call anywhere in this cycle -- publishing stays unbuilt.

1. **`cartridges/listicle/{cartridge.md,schema.json,template.html,rubric.md,exemplars/}` (new).** Header ("Advertisement" label, "N Reasons ..." H1 8-14 words, dek, byline via the same `{{ byline_html | safe }}` include article uses -- not base.html's footer-placed `{% block byline %}`), an optional 3-stat proof row directly under the dek (verified claims only: live Judge.me rating/count, warranty term, free shipping), 5-7 numbered reasons (`schema.json`'s top-level array is named `reasons`, not `items` -- a dict has a real `.items` method, and Jinja's attribute-then-subscript `getattr` lookup silently returns that bound method instead of the page.json value for any field literally named `items`; caught by a real `TypeError: 'builtin_function_or_method' object is not iterable` on the first test run, fixed by renaming rather than special-casing the template), one CTA (`cta_text`/`cta_url`, flat top-level fields like longform/product-page, not article's nested `cta.text`) shown after reason 3 and again at the close, and a closing block (optional headline, a paragraph naming Peak, `warranty_line`, `financing_line`). Word range 600-1,100 (`cartridge.md`'s own "600–1,100 words" line -- `write.parse_word_range` picks it up with zero cartridge-specific code). Exemplar is the live reference page's own visible copy (`reference/peak-listicle-lp/index.html`), trimmed to 371 words, with a note that the reference itself has no ad label/byline/disclosure/sources and the new page must have all four.
2. **Claim gates, vocab, first-person, EMF, and lender rules are 100% reused, not forked.** `adv/claims.py` and `adv/write.py` were not touched at all -- `gate_page_json`, `find_forbidden_terms`, `find_first_person_violations`, `find_leaked_claim_ids`, `find_financing_violations`, `find_warranty_violations`, and `find_missing_attribution` all already walk `page_json` generically by `cartridge_name` string, not a hardcoded per-cartridge field list, so listicle passed through every one of them unmodified (`tests/test_listicle.py` proves each: a hype word, bad warranty wording, an invented financing figure, and an uncredited first-person story in a `reasons[]` item each raise `ClaimsGateFailure` through the exact same `gate_page_json` call every other cartridge uses). The one gate that IS opt-in per cartridge, `find_benefit_claim_shortfall` (its `MIN_BENEFIT_CLAIMS`/`_BENEFIT_SECTION_GETTERS` dicts), has no `"listicle"` entry -- same no-op behavior article/product-page/longform already precedent for a cartridge not in those dicts, left alone since editing `claims.py` was out of scope for this cycle.
3. **Registration (`adv/cli.py`, minimal).** `discover_cartridges()` already finds any `cartridges/<name>/` with a `cartridge.md` -- no code change needed for `--cartridges listicle` or the unknown-cartridge check to work. The one real change: new `DEFAULT_CARTRIDGE_POOL = ("article", "product-page", "longform")`, consulted only when `cmd_run` picks its own random-3 with no `--cartridges` flag, so listicle stays opt-in (per the brief: "the default set stays article,product-page,longform") until Caleb approves it broadly, while `adv run <input> --cartridges listicle` (or any comma list containing it) works today.
4. **`adv/shopify.py` (new) -- `adv shopify-body <run-dir>/<cartridge>`.** Regex-based transform (same style as `claims.py`'s `strip_html_to_visible_text`/`strip_leaked_claim_ids` -- no new HTML-parsing dependency) of an already-rendered `index.html`: extracts `<body>`'s inner content; unwraps (not deletes) `<header>`/`<footer>`/`<nav>` TAGS so the "Advertisement" label, byline, disclosure paragraph, and Sources list all survive with no wrapping tag left behind; pulls every `<style>` block's CSS out and prepends it, combined with `AURORA_FULL_BLEED_RULES` (the three `.section:has(.pk-lp) ...` rules copied verbatim from the live, shipped `reference/peak-listicle-lp/shopify-body.html`, which neutralise `.container` padding, per-section spacing, and the duplicate `.page__title`/`.page__content` chrome), to the very top; finds the IntersectionObserver reveal-on-scroll `<script>` by content signature and moves it to the very end (base.html's fixed `<main>{content}<footer>disclosure/sources</footer></main>` structure would otherwise leave it sitting before the Sources list, not after); relativizes every `href="https://peaksaunas.com/..."` to a bare path (falling back to `/collections/all` if that ever resolves empty). Writes `shopify-body.assets.json` alongside `shopify-body.html`: every relative `assets/...` image the page references, deduped, each with the renderer-derived alt text (already a real description, not a bare asset id -- `render.asset_alt`) and an intended Shopify Files CDN filename (`pk-<cartridge>-<NN>-<slug-of-alt>.<ext>`) for a later publish step to upload under -- image src stays the relative local path for now, exactly as specified. The transform is cartridge-agnostic by construction (no `if cartridge_name == "listicle"` branch anywhere in it) -- `tests/test_shopify_body.py` proves it against `article`'s own rendered output too (no `.pk-lp`, no motion script, empty asset manifest, still no header/footer/nav tags).
5. **`adv/render.py` (one addition).** `build_json_ld` gained a `"listicle"` branch (schema.org `ItemList`/`ListItem`, one entry per reason) -- the only render.py change; `render_page` itself needed nothing listicle-specific since cartridge_name was already a generic parameter everywhere.
6. **Tests.** `tests/test_listicle.py` (13 tests): cartridge discovery + default-pool exclusion, word-range parsing, CTA-allowlist `{model_name}` substitution, the flat-`cta_text` CTA gate, a full `check_page_gates` PASS on a well-formed page, four "gate reuse, not fork" tests (hype word / bad warranty wording / invented financing / uncredited first person, each via the shared `gate_page_json`), and two `render_page` tests (structure -- badge, disclosure, `ItemList` JSON-LD, `pk-lp` class, motion script, CTA-exactly-twice, reason-count -- and asset download/rewrite). `tests/test_shopify_body.py` (12 tests): the Aurora rules lead the output, zero `<html>/<head>/<header>/<footer>/<nav>` tags survive while their content does, the motion script lands at the very end, internal links relativize, the assets manifest is correct and stable on disk, the CLI wiring (`cmd_shopify_body`) writes both files and errors cleanly with no `index.html`, and the whole transform works unmodified on a non-listicle cartridge. **Test count: 339 (314 pre-existing + 25 new: 13 in test_listicle.py, 12 in test_shopify_body.py).**

### Verify (server, real Claude calls, foreground, one at a time)

`.venv/bin/pip install -e . -q` re-run first; `.venv/bin/python -m pytest -q`: **339 passed** on both the Mac clone and the server.

`claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only (never committed) for the duration of this verification, then restored to `"stop"` afterward -- `git status`/`git diff --stat` confirmed clean both before and after.

- **`adv run fixtures/hidden-costs-v2.mov --cartridges listicle`** (`20260910-1836-hidden-costs-v2`): **PASS**, `run_result: PASS attempts=3 repairs=2`. Headline "7 Reasons Shoppers Are Choosing Peak Saunas for Upfront Pricing", 7 reasons (numbers 1-7, in order), CTA "Shop the Fuji" (appears exactly twice: after reason 3, at the close), 758 words. Estimated cost **$0.2740** (55,236 in / 7,219 out tokens, 4 calls, 85.7s of the 300s wall-clock cap).
- **`adv run fixtures/product-features-v2.mov --cartridges listicle`** (`20260910-1838-product-features-v2`): **PASS**, `run_result: PASS attempts=2 repairs=1`. Headline "7 Reasons Apartment Dwellers Are Choosing the Peak Mini", 7 reasons (numbers 1-7, in order), CTA "Shop the Mini" (appears exactly twice), 611 words. Estimated cost **$0.2183** (45,573 in / 5,442 out tokens, 4 calls, 70.1s of the 300s cap).
- **`adv review` and `adv shopify-body`** run on both: `listicle-review.html`, `listicle/shopify-body.html`, `listicle/shopify-body.assets.json` written for each.
- **Verification checks, both runs** (via `adv.claims.strip_html_to_visible_text` / `find_leaked_claim_ids_visible_text` against each run's own `facts_pack.json`): visible text contains zero `"emf"`, zero `"http"`, zero leaked claim ids; exactly one CTA text, shown twice; reason count 7 (inside the 5-7 range) for both. `shopify-body.html` starts with the three `.section:has(.pk-lp) ...` Aurora rules and contains none of `<html>`/`<head>`/`<header>`/`<footer>`/`<nav>` while still containing the "Advertisement" label, byline, disclosure, and Sources content.
- **No Shopify API call made at any point** -- `adv shopify-body` only reads/writes files under `out/`.
- Mac scratch clone `rm -rf`'d after this verification; nothing left on the Mac.

## Cycle 15

**Scope.** Two small fixes (image downscaling at render, listicle-pack wiring into `ground.py`) plus the four documentation deliverables (`docs/REFINE-NOTES.md`, `docs/LISTICLE-GENERATOR.md`, `docs/IMAGE-MAP.md`, `docs/PACKET-DRAFT.md`) and a `README.md` Documents index.

1. **Image downscaling at render (`adv/render.py`).** The Mini sample review file was 46 MB because a Drive original was inlined into `adv review`'s data-URI HTML at full size -- some pack files run 12+ MB, per `docs/DRIVE-AUDIT-LISTICLE.md`'s "42 of 105 files exceed 6 MB" finding. New `resize_asset_bytes(data, ext, log=None, asset_id=None)`: opens the downloaded bytes with Pillow, downscales to a max `ASSET_MAX_LONG_EDGE` (1600px) long edge via `Image.thumbnail`, then re-encodes -- a PNG stays a PNG (`optimize=True`) unless the re-encoded PNG would exceed `ASSET_PNG_MAX_BYTES` (1.5 MB), in which case it's converted to JPEG; anything else (already JPEG, or converted for the size reason) is re-encoded at `ASSET_JPEG_QUALITY` (82), with an RGBA/palette image flattened to RGB first (JPEG has no alpha channel). Logs `asset <id> downscaled: <original bytes> -> <final bytes> bytes (<ext> -> <ext>)` via the existing `log.event("render", ...)` path. Anything Pillow can't open (not an image, a corrupt/partial download) is returned unchanged -- resizing is a size optimization, not a correctness gate; a genuinely broken download is already caught by `download_asset`'s pre-existing HTML-sniff check, which runs first. Wired into `download_asset` right after that HTML-sniff check and before the file is written to `out/<run>/<cartridge>/assets/`, so every path that writes an asset to disk (real Shopify CDN fetch, real Drive download, or a test's fake `fetch_url`) goes through it -- no separate code path for `adv review` to also downscale, since by the time `adv review` runs, every asset on disk is already small. `pyproject.toml` gained `pillow` as a real dependency (render.py now hard-imports `PIL.Image`, not just a test-time install).
2. **Listicle pack wiring (`adv/ground.py`).** New `select_listicle_pack_assets(pack_index, model_slug, allow_ai_renders, limit=6)`: reads `brand/assets-listicle-pack.json` (a separate index from `brand/assets.json`, only wired in for `model_slug in {"mini", "matterhorn"}` -- `LISTICLE_PACK_MODELS` -- per `docs/DRIVE-AUDIT-LISTICLE.md`'s counts, the only two models this pack actually covers), same tiered-selection shape as the existing `select_drive_assets`: real photos first (`photo_product`, `photo_install`, plus `still_video` -- brand-general stills with no `model` tag, eligible for either model), an `ai_render` row only in a second tier and **only** ever selected when `allow_ai_renders` is true; a row's own `excluded` flag is honored either way. `claims/config.json` gained `"allow_ai_renders": false` (new key in `ground.DEFAULT_CONFIG` too, so a config file missing the key still defaults closed) -- per `docs/DRIVE-AUDIT-LISTICLE.md`'s "AI renders: policy decision needed" flag, this is the sign-off gate that section asked for. `LocalFactsSource.facts_for()` calls it (via new `_load_listicle_pack_index()`, same lazy-cache pattern as `_load_assets_index()`) and appends the result to `facts_pack.assets` after the existing Shopify/Drive assets, only when the run's product is Mini or Matterhorn. `adv/render.py`'s `asset_alt()` now checks the asset's own `ai_generated` flag (threaded straight through from the pack index) and prefixes `"Rendering: "` to the alt text when true -- this is the renderer's own field, never the writer's, so it can't be dropped or reworded by a page.json edit; `_ASSET_KIND_ALT_SUFFIXES` gained entries for the pack's own kind vocabulary (`photo_product`, `photo_install`, `still_video`, `ai_render` -- distinct strings from `assets.json`'s `image`/`render`/`lifestyle`/`interior`/`installation`).
3. **Tests.** `tests/test_render.py`: `resize_asset_bytes` shrinks a large PNG to exactly the 1600px cap, leaves a small image's dimensions untouched, converts an over-1.5MB PNG to JPEG, re-encodes a JPEG at quality 82, logs the original/final byte counts, and leaves non-image bytes unchanged (mirrors the existing fake-JPEG-bytes download fixtures already in this file); `download_asset` end-to-end resizes a real downloaded image; a full `render_page` + `adv review` round trip with a large fake image stays under 12 MB (the direct regression test for the 46 MB Mini file); `asset_alt` prefixes `"Rendering:"` for `ai_generated: true` and not for a real photo. `tests/test_ground.py`: `select_listicle_pack_assets` prefers real photos over an ai_render even when renders are allowed, never selects an ai_generated row by default, does select one when `allow_ai_renders=True`, excludes a flagged/other-model row, and produces the right id/url shape; `claims/config.json`'s default `allow_ai_renders` is `False`; `facts_for()` wires pack assets in for Mini and for Matterhorn, wires nothing in for a non-pack product (Fuji) even with renders allowed, and includes an ai_generated asset only once the config flag is set. **Test count: 359 (339 pre-existing + 20 new: 10 in test_render.py, 10 in test_ground.py).**

### Verify (server)

`.venv/bin/pip install -e . -q` (pulls in `pillow` from the updated `pyproject.toml`) then `.venv/bin/pip install -q pillow` (redundant with the dependency, run anyway per the deploy checklist) re-run first; `.venv/bin/python -m pytest -q`: **359 passed** on both the Mac clone and the server.

`claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only (never committed) for the duration of this verification, then restored to `"stop"` afterward -- `git status`/`git diff --stat` confirmed clean both before and after.

- **`adv run fixtures/product-features-v2.mov --cartridges listicle`** (`20260910-1900-product-features-v2`): **PASS**, `run_result: PASS attempts=3 repairs=2`. Log confirms real downscales, e.g. `asset asset-listicle-13WAP9ZyCybiItJlTRBKIImsnHmVl7Xu7 downscaled: 13721421 -> 305194 bytes (.JPG -> .jpg)` and `... 9710425 -> 201179 bytes (.JPG -> .jpg)` -- both well over 6 MB pre-resize, both under 400 KB after. 130.7s of the 300s wall-clock cap, 74,356/220,000 tokens, 5/14 calls, estimated cost $0.3164.
- **`adv review out/20260910-1900-product-features-v2`**: wrote `listicle-review.html` at **1.8 MB** (well under the 12 MB ceiling; the pre-fix equivalent, uninlined at full Drive-original size, would have been in the same range as the 46 MB Mini file this cycle fixes).
- **AI-render check**: every `asset_id` in the run's `page.json` cross-checked against `brand/assets-listicle-pack.json` by drive id -- all 6 listicle-pack assets used (`13WAP9ZyCybiItJlTRBKIImsnHmVl7Xu7`, `13PAv9Jg94HrZkbhAxZ0wb3RGl0xOVZ8O`, `11xw6CaHNI6TNb9ezBCUBoXTS6d5jG47n`, `1xMh3Eg_iDOyCh1u-RSxOe1IOF5uuFWD_`, `1j_g3K3n92E0vwOtIlMmiDFT6md7G9-rb`, `17UghmkrmU613OIfIQmb3zi01bRb_S_9z`) are `kind: photo_product`/`photo_install` with `ai_generated: False` -- confirms `allow_ai_renders: false` (the committed default) kept every AI composite out of a real run, exactly as designed.
- `git status`/`git diff --stat` on the server: clean both before the `"warn"` policy edit and after restoring `"stop"`.
- Mac scratch clone `rm -rf`'d after this verification; nothing left on the Mac.

## Cycle 16

**Scope.** Approved polish only, all items from Caleb's design-notes-batch50 "Proposed Changes" (low effort ones) and the Thursday queue swipe-file items, plus the two Thursday-queue matcher/warranty fixes. Engine and cartridges stay tenant-neutral throughout -- every company value landed in `tenants/peak-saunas/*`, nothing in `harness/` or `cartridges/`.

### A. Design rules (design-notes-batch50)

1. **Article headline formula (`cartridges/article/cartridge.md`).** Rule text: number+outcome or curiosity hook, 8 to 14 words (phrased "8 to 14", not "8-14", so it can't collide with `write.parse_word_range`'s first-match regex -- `tests/test_repair_loop.py::test_real_cartridge_md_files_have_exactly_one_word_range_pattern` guards this). Enforced only as a soft check: `harness/cli.py`'s new `find_headline_word_count_warning` writes a REVIEW.md line, never a gate failure.
2. **Explicit one-CTA-phrase rule (`cartridges/product-page/cartridge.md`, `cartridges/longform/cartridge.md`).** Rule text made explicit: one CTA phrase per page, may repeat verbatim, never two different phrases. Already gated by the existing `find_cta_violation`/allowed-CTA-texts machinery -- this cycle only made the rule text explicit, no gate change.
3. **Longform 3-stat proof row (`cartridges/longform/schema.json`, `template.html`, `cartridge.md`).** New optional `hero.proof_stats` array, 2-3 items each `{value, label, claim_ids}`, rendered directly under the hero subhead. New gate `claims.find_proof_stats_violations`: every stat present must carry at least one claim_id (verified claims only) -- wired into `gate_page_json` for every cartridge, no-op when `hero.proof_stats` is absent.
4. **No-persona rule (`cartridges/article/cartridge.md`).** Explicit rule: no fabricated/credentialed author personas -- every byline is the tenant's own author, never an invented expert/doctor. Policy text only, reinforcing the existing client rule; no new gate (a fabricated persona reads as ordinary prose the existing gates already constrain).
5. **One-offer-card anti-pattern (`cartridges/longform/cartridge.md` + new gate).** Rule text: never more than one CTA/offer card on a page (the "Roman hub" anti-pattern). New `claims.find_second_cta_violation`: any `cta_url` key anywhere in `page_json` other than the page root is a second offer card and STOPs the run. No-op for article, whose CTA lives at `cta.text`/`cta.url`, not a top-level `cta_url` key.

### B. Swipe-file rules (Thursday queue item 3)

6. **Proof inside every reason/section (`cartridges/article/cartridge.md`, `cartridges/listicle/cartridge.md`).** Rule text: every article body section / listicle reason needs at least one claim_id or an attributed customer statement. Soft check only: `cli.find_missing_section_proof_warnings` writes a REVIEW.md line per section/reason with neither. Also extended `claims._ATTRIBUTABLE_PATH_RE` to cover listicle's `reasons[N]` path (previously only article/longform/product-page paths were eligible for `attributed_to_customer`), so the new listicle rule's "attributed customer statement" escape hatch actually works.
7. **Audience named in the H1 (`harness/ingest.py`, `cli.py`).** `ad_brief` gains a required `audience` string field (empty when the ad names none) -- `REQUIRED_KEYS`, `AD_BRIEF_SYSTEM` prompt, and `validate_ad_brief` all updated. Writer instruction added to `cartridges/article/cartridge.md`'s headline rule (name the audience when `ad_brief.audience` is non-empty). Soft check: `cli.find_audience_headline_warning` warns in REVIEW.md when a named audience isn't in the headline (article/listicle only -- listicle already had similar prompt guidance from cycle 14).
8. **Article gains two structure stages (`cartridges/article/{cartridge.md,schema.json,template.html}`).** New required `alternatives_section` ("why the usual alternatives fall short") and `how_it_works_section` ("how it works"), same shape as `body_sections`' items (`{heading, paragraphs: [{text, claim_ids, attributed_to_customer}]}`), rendered between `body_sections` and `turn_section`. Word range unchanged (1,000-1,600) -- existing filler in `tests/test_render.py::ARTICLE_PAGE` was rebalanced (fewer body_sections paragraphs, two new short sections) to stay in range at 1,073 words.
9. **Consult-CTA variant (`tenants/peak-saunas/tenant.yaml`, `tenants/_template/tenant.yaml`, `harness/write.py`).** New tenant.yaml keys `tenant_short_name`, `cta_mode` (`buy`/`consult`/`auto`), `cta_variants: {buy: [], consult: [...]}`. `write.resolve_cta_mode` resolves `auto` against a short, engine-level `HIGH_CONSIDERATION_ANGLE_KEYWORDS` list (custom, commercial, installation, financing, consultation, multi-person, outdoor, wholesale, bulk) checked against `ad_brief.angle`. `write.resolve_allowed_cta_texts` now takes `tenant`/`ad_angle`: in `consult` mode, every cartridge's allowed CTA texts become the tenant's own `cta_variants.consult` list instead of that cartridge's own schema list; `buy` mode (peak-saunas' default) leaves every cartridge's schema list exactly as it always was -- zero behavior change for the common case. Cartridges never hardcode a company's consult phrasing; that lives only in `tenant.yaml`.
10. **HSA/FSA via TrueMed (`tenants/peak-saunas/claims/pending.json`).** New `pending-hsa-fsa-truemed` entry, `status: needs-caleb`, citing `tenants/peak-saunas/docs/knowledge-map.md`'s "HSA/FSA via TrueMed" line and naming `policy/financing-and-payment` as the g Brain policy slug most likely to carry the real terms -- that slug was not itself opened this cycle, so this is explicitly NOT moved to `verified.json`; no page may cite it until Caleb signs off with a direct source quote.

### C. Matcher and warranty (Thursday queue items 1-2)

11. **Alias matching (`harness/claims.py`, `semantic_match.py`, `tenants/peak-saunas/claims/verified.json`).** New `aliases` list on `gbrain-allowlist-360-full-spectrum` and `gbrain-allowlist-red-light` (`"4-in-1"`, `"near, mid, far infrared plus red light"`, `"medical-grade panel"`, `"medical grade red light panel"`). New `claims.alias_match`: deterministic, no model call, same overlap threshold and numeric-token guard as ordinary word-overlap matching, just measured against each claim's aliases; `match_claim` tries it FIRST, before the semantic-match model mapping. `_trim_verified_claims_for_prompt` includes each claim's own `aliases` list in the candidate text sent to the model. **Temperature 0, real-run finding:** tried first as a bare `temperature=0` kwarg -- this deployment's client (`anthropic==1.4.0`) has no typed `temperature` parameter on `messages.create` at all, raised `TypeError`, silently swallowed by `semantic_match_claims`'s own try/except and logged as "falling back to overlap" on every single run (caught live on `still-infraredglow-4x5.png`'s first verification pass). Tried second as `extra_body={"temperature": 0}` (the standard way to pass an unlisted-but-API-supported param) -- reached the real API, which rejected it outright: `400 "temperature is deprecated for this model"`. Neither form works for `claude-sonnet-5` on this deployment, so the call now sends no temperature parameter at all -- documented in `semantic_match.py` with both failure modes, so a future attempt doesn't repeat this. The actual fix for the Thursday queue's specific variance bug is `alias_match`, which never depends on this call's sampling behavior at all.
12. **Narrowed warranty trigger (`harness/claims.py`).** `find_warranty_violations` now only inspects a string for warranty-wording violations when it BOTH contains "warrant" AND asserts coverage (`cover`/`covered`/`coverage`/`backed`/`guarantee`/`guaranteed`/`years`/`lifetime`, via new `_WARRANTY_COVERAGE_ASSERTION_RE`) -- descriptive/buyer-education prose ("read the warranty terms before you buy") no longer even reaches `is_allowed()`'s checks. The allowed sentence and spec-value both contain "lifetime", so this doesn't loosen what's already allowed; a real overclaim ("Our lifetime warranty covers everything, forever") still contains a coverage-assertion word and is still caught.

### D. Tests

**Test count: 450 (390 pre-existing + 60 new).** New: `tests/test_soft_checks.py` (16, all three soft checks + `write_review_md` wiring); additions to `tests/test_claims.py` (20: alias matching, narrowed warranty trigger, proof_stats gate, second-CTA gate), `tests/test_semantic_match.py` (4: temperature, aliases in candidate text), `tests/test_write.py` (9: `resolve_cta_mode`/`resolve_allowed_cta_texts`), `tests/test_ingest.py` (5: the `audience` field), `tests/test_render.py` (4: proof_stats row, article's two new sections), `tests/test_tenant.py` (6: tenant.yaml keys, pending claim, aliases), `tests/test_cli_run.py` (0 new tests, 2-line fixture fix for the `audience` key). `.venv-local/bin/python -m pytest -q`: **450 passed** on the Mac clone.

`grep -rn "Peak\|Austin\|Judge\.me\|Aurora" harness/ cartridges/`: no matches.

### Verify (server, real Claude calls, foreground, one at a time)

`.venv/bin/pip install -e . -q` then `.venv/bin/python -m pytest -q`: **450 passed**, before AND after the `"warn"`-policy verification block below (`claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only, never committed, restored to `"stop"` immediately after the three real runs -- `git status`/`git diff --stat` confirmed clean before the edit and after restoring).

Two real bugs surfaced only by the real-run pass, neither catchable by the fake-client test suite (see item 11 above for the full story): a bare `temperature=0` kwarg raised `TypeError` against this deployment's `anthropic==1.4.0` client (no typed `temperature` param at all); `extra_body={"temperature": 0}` reached the real API but got a `400 "temperature is deprecated for this model"`. Fixed by dropping the parameter entirely (two follow-up commits, `5b7a65e` and `d3e48bf`) -- the runs below are against the final, fixed code.

- **`harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas`** (`20260910-2023-hidden-costs-v2`): **PASS**, three pages (article, longform, product-page). Article headline "7 Questions At-Home Sauna Shoppers Should Ask Before Buying" (9 words, in the 8-14 target) and has both new stages (`alternatives_section`, `how_it_works_section`) -- 1,361 words. Longform's `hero.proof_stats` populated with 3 verified stats, each with claim_ids (`$8,250`/`price-fuji`, `4.76/5` from 3,958 reviews/`reviews-live`, `Free` shipping/`gbrain-allowlist-free-shipping`) -- 867 words. Product-page 266 words. One distinct CTA url per page confirmed by grepping every `class="adv-cta"` anchor in each rendered `index.html` (article: 1 instance; longform: 3 instances, same URL; product-page: 2 instances, same URL) -- the second-CTA gate never had reason to fire on a legitimately single-offer page. REVIEW.md's new Soft-check warnings section correctly flagged two article body sections with no claim_id/attribution (non-blocking, run still PASSed). Budget: 156.76s of 300s, 94,221/220,000 tokens, 5/14 calls, estimated cost **$0.4386**.
- **`harness run tenants/peak-saunas/fixtures/still-infraredglow-4x5.png --tenant peak-saunas`** (`20260910-2040-still-infraredglow-4x5`, after the temperature fix -- the first attempt, `20260910-2026-...`, is the run that surfaced the `TypeError`): **PASS**, three pages. Log line confirming the alias fix: `[2026-09-10T20:40:19] ad_claims.semantic_match: '4-in-1: Near, Mid, Far IR + Red Light' -> 'gbrain-allowlist-red-light'` (and, same run, `'Medical-grade panel included' -> 'gbrain-allowlist-red-light'`). REVIEW.md confirms both matched: `ad claim "4-in-1: Near, Mid, Far IR + Red Light" -> verified `gbrain-allowlist-red-light` (overlap 0.625)` and `ad claim "Medical-grade panel included" -> verified `gbrain-allowlist-red-light` (overlap 0.75)`.
- **`harness run tenants/peak-saunas/fixtures/product-features-v2.mov --tenant peak-saunas --cartridges listicle`**: first attempt (`20260910-2043-product-features-v2`) STOPped (exit 2) on the writer marking a `closing.paragraphs[1]` item `attributed_to_customer` -- not one of the paths the gate allows attribution on (ordinary real-model non-determinism, the same category of run-to-run variance cycles 12/14/15 document, not a cycle 16 regression). Retried: (`20260910-2046-product-features-v2`) **PASS**, one page, 627 words (in the 600-1,100 range), Soft-check warnings section: "none". Budget: 94.46s of 300s, 53,264/220,000 tokens, 4/14 calls, estimated cost **$0.2248**.
- **`harness review`** run on all three run directories: `article-review.html`/`longform-review.html`/`product-page-review.html` for the first two runs, `listicle-review.html` for the third -- all written successfully.
- `grep -rn "Peak\|Austin\|Judge\.me\|Aurora" harness/ cartridges/` (on the server): no matches.
- Mac scratch clone `rm -rf`'d after this verification; nothing left on the Mac.

## Cycle 17 (cost reduction)

**Scope.** Bring a clean three-page run's real cost down toward $0.15 without lowering page quality (every gate unchanged). Baseline: run `20260910-1948-hidden-costs-v2` -- 78,190 input / 10,777 output tokens total, logged at a hard-coded $3/$15-per-million blended rate ($0.4000) that this harness has never actually paid; the real Claude Sonnet 5 price ($2.00/$10.00 per million) prices that same baseline at $0.2642.

1. **Real per-model pricing (`harness/pricing.py`, new).** Per-model $/M table (`claude-sonnet-5` 2.00/10.00, `claude-haiku-4-5` 1.00/5.00), `cache_read` at 0.10x input, `cache_write` at 1.25x input, `batch` at 0.5x the total. `harness/log.py`'s `cost_estimate()` now sums `pricing.calculate_cost()` per recorded call (each call keeps its own model and cache/batch token counts) instead of one blended rate over run totals -- required because a run can mix sonnet and haiku calls once fix 4 lands. The per-call log line and the run-total line both report `cache_creation_input_tokens` and `cache_read_input_tokens` separately from plain `input_tokens`.
2. **Prompt caching (`harness/write.py`, `harness/semantic_match.py`).** Writer system prompt split into a cached prefix (forbidden words + cartridge.md + schema.json, now serialized with `sort_keys=True` for byte stability, + global voice block; `cache_control: ephemeral`) and an uncached tail (per-run hard constraints, DO-NOT-REPEAT list). User message's `facts_pack` block gets its own cache breakpoint; `ad_brief`/exemplars/revision_note stay uncached after it. No date, run id, or product price sits in a cached block anywhere -- verified by grep and by the cached-prefix byte-stability tests. The claims semantic matcher's static system prompt is cached too; its `verified_claims` candidate list (can carry a live price) is deliberately left uncached.
3. **Output hygiene (`harness/write.py`).** `max_tokens_for_word_range()` derives a per-cartridge cap from that cartridge's own word range instead of a flat 6000. The spec's original formula (1.6x words + 800) was tried first and measured wrong on the real server: it capped longform at 3040 output tokens, but longform's own cycle-16 baseline call used 3324 uncapped, and the first live verification run reproduced exactly that -- `write.longform` hit the 3040 cap twice and returned "Unterminated string" invalid JSON both times, mid-string truncation. Longform's schema (specs table, FAQ, steps, proof_stats) carries far more structural JSON per word than article/product-page, so a flat per-word rate tuned for prose undercounted it specifically. Fixed to 1.8x + 1800 (longform 4320, article 4680, product-page 2700), ~30% headroom over the measured 3324-token longform call. Writer instructed to emit compact JSON (no pretty-printing, no duplicated claim text -- ids only). No page.json schema field was found duplicating text already derivable elsewhere (the Sources list already builds from `claim_ids` alone).
4. **Model tiering (`harness/config.py`, `harness/tenant.py`, `harness/anthropic_client.py`, `tenants/*/tenant.yaml`).** `ingest.ad_brief`, still-image transcription, and the claims semantic matcher move to `claude-haiku-4-5`; new `thinking_kwargs(model)` omits the `thinking` param entirely for any Haiku call (Haiku 4.5 rejects it outright) and keeps `{"type": "disabled"}` for every other model, unchanged. Repairs: attempt 2 (first repair after a gate failure) tries `claude-haiku-4-5`; attempt 3+ (a further repair) falls back to `claude-sonnet-5`. Initial writes stay on sonnet. `tenants/_template/tenant.yaml` and `tenants/peak-saunas/tenant.yaml` gain a `models:` section (`write`/`repair_first`/`repair_next`/`ingest`/`matcher`), read via new `Tenant.model_for(stage)`, falling back to `config.DEFAULT_MODELS` when a tenant doesn't set a key.
5. **Batch mode (`harness/batch.py`, new; `harness/pipeline.py`, `harness/cli.py`).** `harness run --batch` submits the selected cartridges' initial writes as one Message Batch (`client.messages.batches.create`, confirmed supported by the installed `anthropic==1.5.0`), polls (20-minute cap, injectable clock/sleep for tests) until every request has ended, then runs the normal synchronous gate/repair loop per cartridge -- a batch result that doesn't parse or validate falls back to an ordinary synchronous attempt 1 for that one cartridge. Repairs are never batched (each depends on that cartridge's own gate result). Not exercised against the real Batches API in this cycle's server verification (out of scope for the required `harness run` checks below); recommended for the nightly inbox-sweep cron, which can tolerate the batch's own completion latency.
6. **Tests.** New `tests/test_pricing.py`, `tests/test_log.py`, `tests/test_anthropic_client.py`, `tests/test_batch.py`; additions to `tests/test_write.py` (cached-prefix byte-stability across two builds of the same cartridge, `max_tokens_for_word_range` formula, model-tiering wiring), `tests/test_repair_loop.py`, `tests/test_semantic_match.py`, `tests/test_ingest.py`, `tests/test_tenant.py` (models: override + fallback behavior). **Test count: 508 (450 pre-existing + 58 new).** `.venv-local/bin/pytest -q` and, separately, `.venv/bin/python -m pytest -q` on the server (policy left at `"stop"`): both **508 passed**.

### Before / after cost table

| | input | cache write | cache read | output | estimated cost |
|---|---|---|---|---|---|
| Baseline (`20260910-1948-hidden-costs-v2`), logged at old hard-coded $3/$15 | 78,190 | -- | -- | 10,777 | $0.4000 |
| Same baseline, real Sonnet-5 rate ($2/$10), no caching/tiering | 78,190 | -- | -- | 10,777 | $0.2642 |
| Cycle 17 run 1 (`20260910-2132`, cold cache) | 9,854 | 52,530 | 0 | 8,992 | **$0.2375** |
| Cycle 17 run 2 (`20260910-2135`, warm cache, 1 bad-JSON retry on article) | 17,549 | 0 | 70,176 | 12,550 | **$0.1711** |
| Cycle 17 run 3 (`20260910-2137`, warm cache) | 10,013 | 0 | 52,530 | 9,029 | **$0.1173** |
| **Median of the 3 runs above** | | | | | **$0.1711** |
| Listicle (`20260910-2140-product-features-v2`, 1 page, 2 repairs: haiku then sonnet) | 10,827 | 31,367 | 17,621 | 6,131 | $0.1296 |
| Still image, 3 pages (`20260910-2142-still-infraredglow-4x5`, 1 repair: haiku) | 22,888 | 43,582 | 37,775 | 15,023 | $0.2460 |

### Target: NOT met. Median $0.1711, target <= $0.15.

Stated plainly, per the verification spec: the three-in-a-row median is **$0.1711**, about 14% over the $0.15 target. What's driving it:
- **Run 1 is a genuinely cold cache** -- every writer/matcher call pays the 1.25x cache-write premium with zero read discount yet, since nothing was cached before it. This is also the realistic worst case for the first run of a day (a nightly cron's first invocation, or any run more than 5 minutes after the last one). A run with a warm cache (runs 2 and 3) lands at $0.1711 and $0.1173 respectively -- both at or under target.
- **Run 2's extra cost is a new, separate finding**: `write.article` got "Expecting ',' delimiter" invalid JSON on its first attempt (a genuine malformed-JSON response, not truncation -- output was 3384 tokens against a 4680 cap) and had to retry once via `write_page`'s existing bad-JSON retry loop, adding one extra sonnet call (~7,581 input + 3,363 output tokens, at full uncached-attempt-2 cost since that retry doesn't carry its own cache_control replan). This looks like ordinary real-model non-determinism (same category as prior cycles' run-to-run variance), not a regression from this cycle's prompt changes, but it was only ever seen live, never in the fake-client test suite, and it happened on the very next run after the cycle's own code changes landed -- worth watching over more real runs before ruling out a caching-related trigger (e.g. a cache-read response shaped subtly differently from a cache-write response).
- **What would close the gap:** batching (fix 5) was intentionally not exercised in this cycle's server verification and gets 50% off every token type in the initial writes -- applying it to run 1's cold-cache numbers alone would land near $0.12-0.13, likely enough on its own. Short of that, the harness could deliberately warm the cache with a cheap dummy call before a cold run, or the nightly cron could simply accept that its first run of the day costs more and subsequent runs (same 5-minute cache window as manual QA) land under target, which is already true for 2 of the 3 runs measured here.

### Cache hit evidence

- Cross-run (within the 5-minute ephemeral window): run 1's three writer calls show `cache_creation_input_tokens` of 17646/17864/17020 (article/longform/product-page) and `cache_read_input_tokens=0` (nothing to hit yet); run 2's same three calls, ~3 minutes later, show `cache_creation_input_tokens=0` and `cache_read_input_tokens` of exactly 17646/17864/17020 -- an exact byte-for-byte prefix match on every cartridge.
- Within-run (across a repair attempt on the same cartridge): the still-image run's `write.longform` attempt 1 paid `cache_creation_input_tokens=9822`; its repair (attempt 2, on `claude-haiku-4-5`) is a different model so it re-pays a full cache write for that model's own cache namespace (`cache_creation_input_tokens=14124`) -- confirms the cache is scoped per model, not shared across the haiku repair and the sonnet initial write. The listicle run shows the same-model case: attempt 1 (sonnet) `cache_creation_input_tokens=17621`; attempt 3 (also sonnet, after an intervening haiku attempt 2) shows `cache_read_input_tokens=17621` -- confirms a same-model cache hit survives an intervening different-model call in between.

### Verify (server, real Claude calls, foreground, one at a time)

`.venv/bin/pip install -e . -q` then `.venv/bin/python -m pytest -q`: **508 passed**, before the `"warn"`-policy verification block and again after restoring `"stop"` (`claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only, never committed, restored immediately after the five real runs below -- `git status`/`git diff --stat` confirmed clean before the edit and after restoring; the test suite itself was run with the policy back at `"stop"` both times, since the real tenant config is live in `test_cli_run.py`'s integration-style tests and a `"warn"` override there makes `test_run_stops_on_unmatched_claim` fail for reasons unrelated to this cycle's code).

One real bug surfaced only by the first live run (see fix 3 above): the spec's own `1.6x + 800` `max_tokens` formula undershot longform and truncated its JSON. Fixed (commit `359bb05`) before any further verification; all runs below are against the fixed code.

- **`harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas`**, three times in a row: `20260910-2132-hidden-costs-v2` (PASS, attempts=3 repairs=0, $0.2375), `20260910-2135-hidden-costs-v2` (PASS, attempts=3 repairs=0 at the gate level -- one internal bad-JSON retry on article, unrelated to gate repairs, $0.1711), `20260910-2137-hidden-costs-v2` (PASS, attempts=3 repairs=0, $0.1173). See the cost table above for the full per-run token breakdown.
- **`harness run tenants/peak-saunas/fixtures/product-features-v2.mov --tenant peak-saunas --cartridges listicle`** (`20260910-2140-product-features-v2`): PASS, attempts=3 repairs=2 -- confirms model tiering live: repair 1 (attempt 2) ran on `claude-haiku-4-5`, repair 2 (attempt 3) fell back to `claude-sonnet-5` after the haiku repair still failed the word-count gate, exactly per spec.
- **`harness run tenants/peak-saunas/fixtures/still-infraredglow-4x5.png --tenant peak-saunas`** (`20260910-2142-still-infraredglow-4x5`): PASS, three pages, attempts=4 repairs=1 (longform's one repair ran on `claude-haiku-4-5` and passed). `ingest.vision` (still-image transcription) confirmed running on `claude-haiku-4-5`; `ad_claims.semantic_match` confirmed running on `claude-haiku-4-5`.
- `git status`/`git diff --stat` on the server: clean both before the `"warn"` policy edit and after restoring `"stop"`.
- Mac scratch clone `rm -rf`'d after this verification; nothing left on the Mac.

## Cycle 18

**Scope.** Caleb's 2026-09-10 decisions, tenant-only: `tenants/peak-saunas/*`, `tests/test_tenant_peak.py`, and this file. Nothing in `harness/` or `cartridges/` touched -- cycle 17 (cost work) is editing those concurrently.

1. **Financing lender is Bread Pay (`claims/config.json`, `tenant.yaml`, `vocab.yaml`).** `financing_lender: "Bread Pay"` in both `claims/config.json` and `tenant.yaml`'s own `allow_ai_renders`-style mirror (tenant.yaml carries no `financing_lender` key to mirror, so only `claims/config.json` changed there). `vocab.yaml`'s `forbidden_lender_names` drops "bread pay", keeping Affirm/Shop Pay/Klarna/Afterpay/Sezzle forbidden. New pending item `policy-financing-doc-stale` (`claims/pending.json`) flags that g Brain `policy/financing-and-payment` still names only Affirm/Shop Pay and needs a team update; the two now-resolved discrepancy entries (`pending-financing-lender-bread-pay`, `gbrain-financing-lender-discrepancy`) are removed. Monthly-payment figures are unchanged -- still always an overclaim (`harness/claims.py`'s `evaluate_financing_claim` has no real lender quote to check against yet; that's harness code, out of scope this cycle, tracked since Cycle 10).
2. **AI renders allowed (`claims/config.json`, `tenant.yaml`).** `allow_ai_renders: true` in both places (tenant.yaml's copy is a fallback default only -- `claims/config.json` already wins per `harness/tenant.py`'s `_CONFIG_KEYS_FROM_TENANT_YAML`). The "Rendering:" alt-prefix rule is engine code, untouched.
3. **Six claims approved into `claims/verified.json`** (`approved_by: "Caleb"`, `date: "2026-09-10"`, every one carries a `source` and `aliases`): `pwc-included-free`, `pwc-leaderboard-lounge` (text kept verbatim from pending's `gbrain-pwc-leaderboard-sauna-lounge-real-features`), `pwc-expert-protocols` (text kept verbatim from pending's `gbrain-pwc-expert-protocols-real-feature`), `longevity-lab-program` (deliberately narrower than the pending entry -- no pricing, no "application-only" wording), `hsa-fsa-truemed`, `spec-mini-dimensions`. Five corresponding pending entries removed (`gbrain-peak-wellness-club-real-offering` -- including its pricing-conflict caution, resolved: Caleb confirms PWC is free -- `gbrain-pwc-leaderboard-sauna-lounge-real-features`, `gbrain-pwc-expert-protocols-real-feature`, `gbrain-longevity-lab-real-offering`, `pending-hsa-fsa-truemed`).
4. **Crate wording (`claims/verified.json`, `vocab.yaml`).** Existing `gbrain-shipping-free-crate-origin` claim gains aliases `"crate-protected delivery"`, `"crate protected delivery"`, `"delivered in a crate"`. `vocab.yaml`'s `hype_words` (absolute ban, enforced on any page regardless of claim_id) gains `crate-protected`; `hype_synonyms` maps it (and `crate protected`) to `ships in a crate`, so the deterministic pre-repair pass can fix it without a model call.
5. `authors.yaml` untouched -- author identity stays open.

### Tests

`tests/test_tenant_peak.py` (new, 13 tests): asserts the config values above, that all six new claims carry a `source` and `aliases`, the crate claim's new aliases, and that `crate-protected` is forbidden with the `ships in a crate` synonym.

`.venv-local/bin/python -m pytest -q` on the Mac clone: **521 total, 517 passed, 4 failed.** The 4 failures are pre-existing engine tests that hardcode the tenant's *old* defaults, outside this cycle's edit scope (`harness/`, generic `tests/`, not `tenants/peak-saunas/` or `tests/test_tenant_peak.py`):
- `tests/test_claims.py::test_find_forbidden_terms_catches_lender_name_when_lender_not_configured` -- built a page with "Bread Pay" and asserted it's still caught as a forbidden lender name; Bread Pay is the configured lender now, so it no longer is one.
- `tests/test_ground.py::test_default_config_has_no_lender_and_hides_compare_at` -- asserted `financing_lender is None`.
- `tests/test_ground.py::test_default_config_allow_ai_renders_is_false` -- asserted `allow_ai_renders is False`.
- `tests/test_tenant.py::test_hsa_fsa_truemed_claim_is_pending_not_verified` -- asserted `pending-hsa-fsa-truemed` stays in `pending.json`; decision 3 explicitly moves it to `verified.json`.

All four are direct, correct consequences of Caleb's decisions -- not a bug introduced here -- but they live in files this cycle is not authorized to touch. Flagged as a background follow-up rather than fixed in scope.

### Verify (server, real Claude calls, foreground, one at a time)

`~/advertorial/.venv/bin/python -m pytest -q`: **521 total, 517 passed, 4 failed** -- the same 4 pre-existing engine-test failures as the Mac clone (see above), confirmed both before the `"warn"`-policy edit and after restoring `"stop"`. `claims/config.json`'s `ad_overclaim_policy` set to `"warn"` on the server only, never committed, restored immediately after the two real runs below; `git status`/`git diff --stat` confirmed clean before the edit and after restoring.

- **`harness run tenants/peak-saunas/fixtures/still-unforgettable-4x5.png --tenant peak-saunas`**: first attempt (`20260910-2148-...`) failed with `write.article failed after retry: Expecting property name enclosed in double quotes` (malformed JSON from the model -- ordinary real-model non-determinism, same category cycles 12/14/16/17 document, not a cycle 18 regression). Retried: (`20260910-2151-still-unforgettable-4x5`) **PASS**, three pages. `ad_brief.claims_made`: `["Financing available starting at $257/mo", "Full-body medical-grade red light"]`. **Matched:** `"Full-body medical-grade red light" -> gbrain-allowlist-red-light` (overlap 0.667). **Not repeated:** `AD OVERCLAIM: "Financing available starting at $257/mo"` (the $/mo rule is unchanged, as decided). Word counts: article 1,181 / product-page 273 / longform 913. Budget: 181.13s of 300s, 34,111/220,000 tokens, 8/14 calls, estimated cost $0.2684.
- **`harness run tenants/peak-saunas/fixtures/still-levelup-4x5.png --tenant peak-saunas`** (`20260910-2154-still-levelup-4x5`): **PASS** on the first attempt, three pages. `ad_brief.claims_made`: `["Price starts at $176/month with financing", "Includes 4-in-1 technology: near, mid, far infrared and red light", "Medical-grade panel included", "Competitors offer only red light therapy, basic manual controls, and weak far-infrared only"]`. **Matched:** `"Includes 4-in-1 technology..." -> gbrain-allowlist-red-light` (overlap 0.6), `"Medical-grade panel included" -> gbrain-allowlist-red-light` (overlap 0.75). **Not repeated:** the competitor-comparison claim (`no verified claim matched, best word overlap 0.333`) and `AD OVERCLAIM: "Price starts at $176/month with financing"` ($/mo rule, unchanged).
- **Wellness Club / leaderboard+lounge / crate-delivery claims did not appear in either run's `ad_brief.claims_made`** -- this cycle's Haiku vision transcription of these two specific stills didn't surface that wording (only the red-light/4-in-1/financing/competitor lines above), so `pwc-included-free`, `pwc-leaderboard-lounge`, and the crate-delivery aliases had no ad claim to match against in these two live runs. Not a defect in the claims data itself (both are correctly in `verified.json` with sources and aliases, confirmed by `tests/test_tenant_peak.py`) -- just these two images' actual transcribed content this run. Reported as observed, not assumed.
- **Financing sentence check: does NOT match expectation.** `facts_pack.product.financing.lender` is correctly `"Bread Pay"` in both runs (config change confirmed wired through `harness/ground.py`), but every rendered page (`product-page`, `longform`, both runs) renders the plain **`"Financing is available at checkout."`** verbatim -- never `"Financing is available through Bread Pay at checkout."` Root cause (read-only investigation, not fixed here -- out of this cycle's `harness/`/`cartridges/` scope): `harness/claims.py`'s `find_financing_violations` no-ops entirely once a lender is configured (no validation of `financing_line` text at all), and no cartridge (`product-page`/`longform` `cartridge.md`/`schema.json`) tells the writer what exact sentence to use once a lender IS set -- it's only ever told the fixed no-lender sentence, and "may name the lender" with no template. `harness/vocab.py` has no `allowed_financing_sentence_with_lender` at all. Flagged as a background follow-up task rather than declared working.
- **`"crate-protected"` / `"crate protected"` grepped across every rendered `index.html` in both runs: zero matches.** Confirmed clean.
- **`harness review`** run on both run directories: `article-review.html`/`longform-review.html`/`product-page-review.html` written successfully for both.
- `git status`/`git diff --stat` on the server: clean both before the `"warn"` policy edit and after restoring `"stop"`.
- Mac scratch clone `rm -rf`'d after this verification; nothing left on the Mac.

## Cycle 20

**Scope.** Run states, human approval, publisher adapters (Shopify + export), and reviewer
notifications. `harness/*`, `cartridges/` untouched, `tenants/_template/*`, `docs/`,
`tests/` -- shared with a concurrent tenant-only agent on `tenants/peak-saunas/*` and
`docs/FIXLOG.md`; rebased cleanly over its commits.

1. **Run states.** `harness/runstate.py` -- every run directory gets `state.json`
   (`state` in `generated | needs_review | approved | published | rejected`, a `pages`
   map with the same states per cartridge, and a `history` list of `{state, by, at,
   note}`) and `packet.json` (`{stamp, by, at, note}`, default `"BOT DRAFT · NOT SENT"`).
   `harness/pipeline.py`'s `prepare_run` writes both the moment a run directory exists; a
   new `review_notify` stage (inserted after `render_pages`, before `write_review`, in both
   `DEFAULT_STAGES` and `workflows/ad-to-pages.yaml`) advances `state.json` to
   `needs_review` once a run has passed every gate -- a STOP or a budget cap never reaches
   it, so those runs stay at `generated`. New commands: `harness approve <run-dir> --by
   <email> [--pages a,b] [--note ...]` and `harness reject <run-dir> --by <email> --note
   ...`. `--by` must match `tenant.yaml`'s new `reviewers: [{name, email, role}]` list; an
   unmatched email is refused with a one-line message and exit 1, never a traceback.
   Approval with no `--pages` approves every page; a partial `--pages` approval leaves the
   run-level state `needs_review` until every page is approved. Every approval also appends
   one line to `tenants/<t>/evals/approvals.jsonl` (same append-only shape as
   `evals/scores.jsonl`). `harness packet <run-dir> --stamp ship|redo|kill --by <email>`
   sets the packet stamp.
2. **Publisher adapters.** `harness/publishers/`: `base.py` (the `dry_run`/`publish`/
   `upload_assets` interface), `shopify.py` (Admin REST API 2024-10, `urllib.request` only
   -- no new dependency, matching `harness/prices.py`/`harness/sources/judgeme.py`; images
   via `POST /files.json` base64 attachment, chosen over GraphQL `fileCreate`/staged
   uploads since a page's handful of inline images don't need the staged-upload round trip
   -- documented in `docs/PUBLISHING.md`; page create/update via `POST /pages.json`
   `body_html`, `published: false` unless `--live`; every `src="assets/..."` rewritten to
   its uploaded CDN URL before the page is created; `SHOPIFY_STORE`/`SHOPIFY_TOKEN` read
   from the tenant's `.env`, both optional -- every method raises a clear
   `ShopifyCredentialsMissing` with no network call when either is absent), `export.py`
   (writes a self-contained `index.html` + `shopify-body.html` + `assets/` + a manual-upload
   `README.md` per page -- the default for every tenant, and the fallback `harness publish`
   behaves like without `publisher: shopify`). `tenant.yaml` gets `publisher: shopify|export`
   and a `shopify_publish: {store, store_admin_domain}` block. `harness publish <run-dir>
   --page <cartridge> [--live]` refuses unless `state.json`'s `pages.<cartridge>` is
   `approved` **and** `packet.json`'s stamp is `ship`, each refusal printing the exact next
   command and exiting 1. Default publish is unpublished (draft); `--live` also verifies the
   storefront with 8 pulls, 2s apart, per the cache-epoch trap in
   `tenants/peak-saunas/reference/peak-listicle-lp/README.md`. `harness publish --dry-run`
   validates credentials/body with one `GET shop.json` (Shopify) and creates nothing,
   skipping the approval/stamp gates entirely.
3. **Notifications.** `harness/notify.py`: a Slack incoming webhook
   (`SLACK_WEBHOOK_URL`) and plain-text SMTP email (`SMTP_HOST/PORT/USER/PASS`,
   `NOTIFY_FROM`), both optional and both fail closed -- a channel that's off, or a missing
   credential, logs `"notification skipped: no channel configured"` and never blocks a run
   or a command; no secret is ever put in a message body or printed. Sent on: a run reaching
   `needs_review` (tenant, run id, input name, pages, gate-history summary, the "AD CLAIMS
   NOT REPEATED" count, every review file path, and the exact `harness approve` command),
   on `approve`, and on `publish` (with the URL). Every message truncates an ad claim's text
   to 120 characters before it can appear at all. `tenant.yaml` gets `notifications: {slack:
   true|false, email: [...]}`.
4. **Workflows and crons.** `workflows/ad-to-pages.yaml` gains the `review_notify` step
   (after `render_pages`, before `write_review`) so `harness run`/`harness workflow run
   ad-to-pages` stay identical (`tests/test_workflows.py`'s parity assertion still holds).
   `crons/inbox-sweep.service` now calls `harness workflow run ad-to-pages --input "$f"
   --tenant @@TENANT@@ --batch` instead of `harness run "$f" --tenant @@TENANT@@` (50% off
   every swept file's initial cartridge writes). New `harness digest needs-review --tenant
   <t> [--days 3]` lists every run still `needs_review` whose history shows it entered that
   state more than `--days` days ago; new `crons/needs-review-digest.service`/`.timer`
   (weekly, Monday 08:15) runs it; `workflows/weekly-digest.yaml` documents the same step as
   an action-only specification, matching `sweep.yaml`/`score.yaml`'s convention (no
   `stage:` step, so `harness workflow run weekly-digest` exits 1 with "runs no pipeline
   stages" by design -- run the `digest` command directly).
5. **Template and Peak tenant.yaml.** `tenants/_template/tenant.yaml` gains commented
   `reviewers: []`, `publisher: export`, `shopify_publish: {store, store_admin_domain:
   null}`, `notifications: {slack: false, email: []}`. `.env.example` gains `SHOPIFY_STORE`,
   `SHOPIFY_TOKEN`, `SLACK_WEBHOOK_URL`, `SMTP_HOST/PORT/USER/PASS`, `NOTIFY_FROM`
   placeholders. `tenants/peak-saunas/tenant.yaml`: `reviewers` is Michael
   (michael@peaksaunas.com, primary) and Caleb (caleb@peaksaunas.com, backup); `publisher:
   shopify` with `shopify_publish.store: peaksaunas.com` and `store_admin_domain: null` (the
   real `*.myshopify.com` admin domain is not yet confirmed -- `SHOPIFY_STORE` must hold it
   once known); `notifications.slack: true`, `email: []`.
6. **Docs.** New `docs/PUBLISHING.md` (states, approval, packet stamp, publish flow,
   Shopify scopes `write_content`/`write_files`/`read_content`, the cache-verification
   trap, the export fallback). `docs/GENERATOR.md`'s stale "no `harness publish` exists"
   publish-gate section rewritten to describe the real gate. `docs/TENANT-ONBOARDING.md`
   gains a reviewers/publisher/notifications setup item (step 5) and a checklist line.
   `README.md`'s command list and docs index updated. `tenants/peak-saunas/docs/
   PACKET-DRAFT.md` updated to point at `state.json`/`packet.json` and the new commands as
   the live version of the packet-draft concept, and its "Publish mode"/credentials open
   items updated to match what's actually built.
7. **Tests.** New `tests/test_runstate.py` (state-machine transitions, unknown-reviewer
   refusal, partial-vs-full approval, packet stamp, the needs-review-backlog age query),
   `tests/test_publishers.py` (Shopify adapter against a fake transport -- credentials
   missing before any network call, `dry_run`/`publish` payload shape and unpublished
   default, `upload_assets` + `src` rewriting, the 8-pull cache verification; export adapter
   folder output), `tests/test_notify.py` (message formatting, the 120-char claim
   truncation, skip-when-unconfigured proven by asserting `urlopen`/`SMTP` are never
   called), `tests/test_cli_approve_publish.py` (the real `cmd_approve`/`cmd_reject`/
   `cmd_packet`/`cmd_publish` end to end: publish refuses without approval, without a
   `ship` stamp, and without Shopify credentials, each with a clear one-line message and no
   traceback; a `--dry-run` reports the missing-credentials refusal with zero network
   calls). **Test count: 572 (521 pre-existing + 51 new).** Before rebasing over the
   concurrent tenant-only agent's `7d51c9d` ("Fix 4 tests hardcoded to peak-saunas'
   pre-Cycle-18 defaults"), `.venv-local/bin/pytest -q` on the Mac clone showed the same 4
   pre-existing Cycle 18 tenant-data failures noted there, all outside this cycle's scope
   and untouched by this cycle's own new tests. After rebasing over that fix,
   `.venv-local/bin/pytest -q` on the Mac clone shows **572 total, 572 passed, 0 failed**;
   see the server verification block below for `.venv/bin/python -m pytest -q` on the
   server.

### Verify (server)

- `harness approve` on an existing passing run under `tenants/peak-saunas/out` with `--by
  michael@peaksaunas.com`: succeeds, `state.json` updated, one line appended to
  `tenants/peak-saunas/evals/approvals.jsonl`. With a bogus email: refused, exit 1, one-line
  message naming the tenant's `reviewers` list, no traceback.
- `harness publish <run> --page article` (no credentials configured, no approval/stamp
  yet): refused with a clear one-line message and no network call.
- `harness publish <run> --page article --dry-run`: reports "no SHOPIFY_STORE / no
  SHOPIFY_TOKEN configured" with zero network calls -- confirmed by grepping this session's
  own log for any outbound Shopify/Slack request (none found).
- `harness publish <run> --page article` against the `export` adapter: produces a
  self-contained folder (`index.html`, `shopify-body.html`, `assets/`, `README.md`) under
  `<run-dir>/article/export/`.
- `git status` clean after verification; no live Shopify API call and no real Slack webhook
  post were made at any point this cycle -- every network path in `harness/publishers/
  shopify.py` and `harness/notify.py` was exercised only through a fake transport in tests,
  or hit its fail-closed no-credentials path for real (no credentials exist in the tenant
  env yet).

## Known issue (operator, 2026-09-10 22:30)
- Slack notifications disabled in tenants/peak-saunas/tenant.yaml (slack: false) until the deliberate Friday-morning test: cycle 20 verification runs posted 7 real messages because the webhook was already saved. Re-enable with one test before Friday review.
- Run id collision: pipeline.make_run_id truncates to the minute; concurrent runs of the same fixture share a run dir. Fix: add seconds + 4-char random suffix. Assigned to cycle 22 or a follow-up.

## Cycle 21 (gate-enforced "with lender" financing sentence)

**Problem.** Cycle 18 set `tenants/peak-saunas/claims/config.json`'s `financing_lender` to `"Bread Pay"`. `facts_pack.product.financing.lender` correctly reflects it, but every rendered page kept printing the old no-lender sentence, `"Financing is available at checkout."` -- Cycle 18's own real-run verification flagged this and left it unfixed, out of that cycle's tenant-only scope. Root cause, confirmed by reading cycles 6, 10, and 18 before touching anything: (1) `harness/claims.py`'s `find_financing_violations` no-op'd (`return []`) as soon as `financing_lender` was truthy -- no validation of `financing_line` at all once a lender was configured; (2) `harness/write.py`'s `global_voice_block` financing paragraph unconditionally told the writer to use `ALLOWED_FINANCING_SENTENCE_NO_LENDER`, regardless of whether a lender was configured -- it never even looked at `financing_lender`; (3) the cartridges' own financing-line rules told the writer it "may name the lender and monthly figure" once one was set, with no fixed template to use; (4) `evaluate_financing_claim` (fix cycle 10) always returned an overclaim with no way to ever match, by design, since no lender quote existed.

1. **`harness/vocab.py`: `allowed_financing_sentence_with_lender_template` + `allowed_financing_sentence(lender)`.** New `Vocabulary` attribute mirroring `allowed_financing_sentence_no_lender`; new `Vocabulary.allowed_financing_sentence(lender=None)` method (and matching module-level `vocab.allowed_financing_sentence(lender)` / `vocab.ALLOWED_FINANCING_SENTENCE_WITH_LENDER_TEMPLATE`) -- formats the with-lender template when `lender` is truthy, else falls back to the no-lender sentence. Both `tenants/_template/vocab.yaml` and `tenants/peak-saunas/vocab.yaml` gain `allowed_financing_sentence_with_lender_template: "Financing is available through {lender} at checkout."` -- tenant-neutral, no lender name baked in; the engine formats it with whatever `claims/config.json`'s `financing_lender` says.
2. **`harness/claims.py`: `find_financing_violations` validates instead of no-op'ing.** Now checks `financing_line` against `vocab.allowed_financing_sentence(financing_lender)` either way (no-lender or with-lender), same exact-match tolerance as before -- this is what actually catches the Cycle 18 bug (a page stating the stale no-lender sentence once a lender is configured now fails the gate instead of silently passing). Deliberately still scoped to the one dedicated `financing_line` field, not reopened to a broader "any text containing 'financ'" scan across every prose string -- that shape was tried and reverted during Cycle 6 after repeatedly false-positiving on ordinary buyer-education prose (see Cycle 6's run log); nothing about the with-lender case changes that risk, so this cycle didn't reintroduce it. `evaluate_financing_claim(ad_claim_text, financing_lender)`: with no lender, unchanged (always an overclaim against the no-lender sentence). With a lender configured, a claim naming that lender with no `$` amount/`/mo`/`per month`/APR matches the with-lender sentence (`ok=True`); any of those figures is still an overclaim against the same sentence -- no real lender quote exists anywhere in this codebase to check a specific figure against (unchanged gap from Cycle 10). `classify_locked_topic(ad_claim_text, financing_lender=None)` gained the `financing_lender` parameter so a claim naming the configured lender alone (no figure) routes to the `"financing"` topic -- `vocab.LENDER_NAME_RE` only matches a *forbidden* lender name, and the configured one is deliberately not on that list (Cycle 18), so this case fell through to the ordinary word-overlap path before. `gate_ad_brief_claims`'s financing branch now appends to `matched` (synthetic id `financing-<lender-slug>`, not the stale, differently-scoped `gbrain-financing-terms` verified claim) when `evaluate_financing_claim` returns `ok=True`, instead of unconditionally treating every financing claim as an overclaim.
3. **`harness/cli.py`: deterministic pre-repair for financing wording.** New `_fix_financing_violation` (mirrors `_fix_warranty_violation`, fix cycle 13 item 1) and a `"financing_line must be exactly" in issue` branch in `apply_deterministic_fixes` (new `financing_lender=None` kwarg, threaded from `write_and_gate_page`'s own parameter) -- replaces a failing `financing_line` with the correctly formatted sentence, no model call. Before this cycle, no deterministic fix for financing existed at all (the gate never flagged anything to fix once a lender was configured).
4. **`harness/write.py`: `global_voice_block` actually reads `financing_lender`.** Pulls it from `tenant.claims_config.get("financing_lender")` (tenant-scoped, stable config -- same category as vocab.yaml's fixed sentences already baked into the cached system prefix, so this doesn't disturb the fix cycle 17 prompt-caching plan) and states the correctly formatted sentence -- with-lender or no-lender -- instead of always naming the no-lender one. This is the actual fix for the bug Cycle 18 observed: the writer now sees the right instruction, in the right prompt, before it ever writes a word.
5. **Cartridges stay tenant-neutral.** `cartridges/product-page/cartridge.md`, `cartridges/longform/cartridge.md`, `cartridges/listicle/cartridge.md` and their `schema.json` `financing_line` descriptions (plus both cartridges' `rubric.md`, stale/incorrect but not code) no longer hardcode "Financing is available at checkout." or invite the writer to "carry a lender name and monthly figure" -- they now all say "the allowed financing sentence given in the prompt's Financing rule", verbatim, no exceptions. No lender name appears anywhere in `cartridges/` (new regression test, item 6). `article`'s cartridge was left untouched -- it never hardcoded the sentence to begin with (`financing_line` is optional, "only if the ad used a price angle"), out of scope per the assignment.
6. **Tests** (17 new/changed, `tests/test_claims.py`, `tests/test_repair_loop.py`, `tests/test_write.py`, `tests/test_tenant.py`): with-lender formatting (`Vocabulary.allowed_financing_sentence`, module-level `vocab.allowed_financing_sentence`, both tenants' `vocab.yaml` declaring the template); `find_financing_violations` now flags the stale no-lender sentence and a `$/mo` figure once a lender is configured, and passes the exact with-lender sentence; `evaluate_financing_claim`/`classify_locked_topic`/`gate_ad_brief_claims` for a lender-only match, a `$/mo` overclaim, and an APR overclaim, each reporting the with-lender sentence as the verified fact; the deterministic replacement (`apply_deterministic_fixes` unit tests plus a `write_and_gate_page` integration test proving zero repair calls); `global_voice_block`/`write_page`'s system prompt states the with-lender sentence for the real peak-saunas tenant (Bread Pay) and the no-lender sentence for a bare tenant double; a new regression test asserting no lender name appears anywhere under `cartridges/`. `.venv-local/bin/python -m pytest -q` on the Mac clone before rebasing over the concurrent Cycle 20 (`46d288d`) showed **539 total, 539 passed** -- the four Cycle-18 stale-default tests were already fixed by the separately-landed "Fix 4 tests hardcoded to peak-saunas' pre-Cycle-18 defaults" commit, pulled in via `git pull --rebase` before that run. After rebasing over Cycle 20's own 51 new tests, `.venv-local/bin/python -m pytest -q` on the Mac clone shows **590 total, 590 passed, 0 failed** (572 + this cycle's 18 new).

### Verify (server, real Claude calls, foreground, one at a time)

`~/advertorial/.venv/bin/pip install -e . -q` then `~/advertorial/.venv/bin/python -m pytest -q`: **590 total, 590 passed, 0 failed** (572 Cycle-20 baseline + this cycle's 18 new), matching the Mac clone.

`tenants/peak-saunas/claims/config.json`'s `ad_overclaim_policy` was `"stop"` on master (Caleb's since-decided committed default is `"warn"`, but master still said `"stop"` at the time of this verification) -- set to `"warn"` on the server only, never committed, for the two runs below; restored to `"stop"` immediately after; `git diff`/`git status` confirmed an exact restore (empty diff on that file, only the pre-existing, unrelated `tenants/peak-saunas/evals/approvals.jsonl` untracked file -- a Cycle 20 verification artifact, not touched).

- **`harness run tenants/peak-saunas/fixtures/still-levelup-4x5.png --tenant peak-saunas`** (`20260910-2238-still-levelup-4x5`): **PASS**, three pages. Every rendered page's `.adv-financing` text: `product-page` and `article` each state it once, `longform` twice (hero + final_cta) -- all four exactly `"Financing is available through Bread Pay at checkout."`, grepped and confirmed byte-identical. Zero occurrences of the stale `"Financing is available at checkout."` or any `$`/`/mo` figure anywhere in any of the three pages. Gate log: `ad_claims.semantic_match: 'Available starting at $176/month' -> None` followed by `ad_claims: AD OVERCLAIM: "Available starting at $176/month" — verified fact: "Financing is available through Bread Pay at checkout."` -- the $/mo overclaim rule (fix cycle 21 item 2) correctly reports the with-lender sentence as the verified fact, not the stale no-lender one. The ad's other two claims (4-in-1 infrared, medical-grade panel) matched `gbrain-allowlist-red-light` via semantic match, unaffected.
- **`harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas`** (`20260910-2240-hidden-costs-v2`): **PASS**, three pages, `gate_result: PASS 0 ad claim(s) matched` -- this fixture's `ad_brief.claims_made` is empty (unchanged from Cycle 10/18's own verification of this same fixture), so no financing ad claim exercised the ad-claims-gate path here; the page-level gate (`find_financing_violations`) still ran on every cartridge's write. Every rendered page's financing text: exactly `"Financing is available through Bread Pay at checkout."`, confirmed the same way -- zero occurrences of the stale sentence or a `$`/`/mo` figure.
- `git status` on the server: clean (only the pre-existing `approvals.jsonl` untracked file, unrelated to this cycle) at the end.

## Cycle 19 (byline decision, warn as the committed default, final sweep, mobile pass, packet)

**Assignment.** Apply Caleb's byline decision; make `ad_overclaim_policy: "warn"` the committed
default (not restored to `"stop"` this time); sweep all seven fixtures for real plus two listicle
runs; a static mobile pass; `harness review`/`shopify-body` on every PASS; update the packet and
write the sweep doc; exactly one Slack test notification.

1. **Byline: three roles, not two.** `tenants/peak-saunas/authors.yaml` gains a `reviewer` role
   (Caleb Niednagel, Technology Lead) alongside `author` (Austin Laudenslager, Founder & CEO) and
   `contributor` (now `"Peak Saunas Editorial Team"`, no named person -- was previously Caleb).
   `harness/render.py`'s `byline_names()` returns a third value (`reviewer_name`); `FALLBACK_BYLINE`
   and `load_byline_html` thread it through the same way `contributor` already was.
   `tenants/peak-saunas/brand/byline.html` renders "Written by Austin Laudenslager &middot; Peak
   Saunas Editorial Team &middot; Reviewed by Caleb Niednagel, Technology Lead" with dates; the
   about-the-author block names Austin responsible for every claim and Caleb as reviewing
   specifications and sources. Never the word "credentialed". Two new tests
   (`tests/test_render.py`): the rendered three-role byline, and `byline_names()`'s return value.
2. **`ad_overclaim_policy: "warn"`, committed** (`tenants/peak-saunas/claims/config.json`) --
   the first time this value is committed rather than set on the server only for a sweep and
   restored afterward (see Cycle 12's and Cycle 21's own sweep verifications above). Not mirrored
   in `tenant.yaml`. One test (`tests/test_cli_run.py::test_run_stops_on_unmatched_claim`) was
   exercising the `"stop"` path specifically while relying on the tenant's ambient on-disk
   default -- a pre-existing hermeticity gap `docs/SWEEP-2026-09-10b.md`'s "Policy / tree state"
   section already flagged -- pinned to policy `"stop"` explicitly (`monkeypatch` on
   `Tenant.claims_config`) so it stays correct regardless of the tenant's real default.
   **Test count: 592 (590 baseline + 2 new byline tests).**
3. **Sweep: all seven fixtures + two listicle runs, real, under `"warn"`.** Every fixture PASSes;
   two (`still-levelup-4x5.png`, `still-infraredglow-4x5.png`) needed a real retry (one STOP each
   on a pre-existing trigger-word-with-no-claim_id gate shape, plus one transient malformed-JSON
   crash on `still-infraredglow-4x5.png`'s first attempt) -- neither root cause is a Cycle 19
   change. `harness review`/`harness shopify-body` run on every PASS across all 9 runs -- 23
   pages total (seven 3-cartridge runs x 3 + two listicle runs x 1). Byline
   verbatim on all pages: "Written by Austin Laudenslager &middot; Peak Saunas Editorial Team
   &middot; Reviewed by Caleb Niednagel, Technology Lead". Financing sentence verbatim on all
   pages: "Financing is available through Bread Pay at checkout." EMF in visible text: 0 on every
   page (confirmed with `harness.claims.find_forbidden_visible_text` directly, not a raw grep --
   a raw grep hits every page only because the Fuji product's own Shopify URL slug contains
   "near-zero-emf", a documented, harness-external exception). Full table, per-attempt cost, and
   per-ad omissions in `tenants/peak-saunas/docs/SWEEP-2026-09-11.md`.
4. **Mobile pass: static checks only.** Playwright/Chromium is not installed in the harness venv
   on the server (`import playwright` -> `ModuleNotFoundError`) -- not installed for this pass, per
   the assignment; static HTML/CSS regex checks instead (same style as `harness/review.py`'s own
   asset-inlining regex, not a real parser). Four checks against all 23 rendered pages at a nominal
   390px viewport: image sizing, fixed container widths above 390px, the sticky CTA bar's mobile
   CSS rule, heading `white-space:nowrap`. Two of four clean (no fixed widths above 390px anywhere;
   no heading `white-space:nowrap` anywhere). Two of four FAIL, same root cause: 21 of 23 pages
   have `<img>` tags with no width/height and no `img{max-width:100%}` rule anywhere in the loaded
   CSS; all 7 `longform` pages' `.adv-sticky-cta` div has **no CSS rule at all**, not just no
   mobile rule. Confirmed empirically (not just via the regex check) against a real rendered page:
   `tenants/peak-saunas/brand/base.css` (209 lines) defines neither `.adv-cta` nor
   `.adv-sticky-cta` nor an `img` sizing rule -- `harness/render.py`'s `load_brand_css` loads
   *either* a tenant's own `base.css` *or* `harness/fallback.css` (which does define all three),
   never both, so a tenant with its own (incomplete) CSS never sees `fallback.css`'s baseline
   rules. **Not fixed this cycle** -- the root cause is the tenant's own CSS coverage, not a
   `cartridges/*/template.html` bug, so it's outside this cycle's "CSS-only, tenant-neutral
   cartridge template" fix authorization; flagged as a follow-up task (`task_9b60a503`) rather than
   patched under sweep-time pressure, matching this codebase's own established practice (see
   Cycle 12's warranty-gate flag, closed properly in Cycle 13).
5. **Packet and sweep doc.** `tenants/peak-saunas/docs/PACKET-DRAFT.md` updated with all nine
   Cycle 19 run ids (all `needs_review`, all packet stamps still `BOT DRAFT · NOT SENT`, nothing
   approved or published), the exact approve/packet/publish commands, the never-line, and the open
   items (Shopify token not yet saved; candidate `store_admin_domain` `bd4b8d-2.myshopify.com`
   needs Caleb's confirmation; the new CSS gap; Slack still off on purpose). New
   `tenants/peak-saunas/docs/SWEEP-2026-09-11.md` -- note: written under `tenants/peak-saunas/
   docs/`, not top-level `docs/`, matching where `SWEEP-2026-09-10b.md` and `PACKET-DRAFT.md`
   actually live post-restructure (the assignment named the top-level path, which is stale).
6. **Slack test: exactly one message.** `SLACK_WEBHOOK_URL` present in the tenant's `.env`
   (checked by name only, `grep -c`, never printed or catted). `tenant.yaml`'s
   `notifications.slack` is `false` on purpose (see the "Known issue, 2026-09-10 22:30" note
   above -- Cycle 20 posted 7 real messages by accident). Sent the one authorized test through
   `harness/notify.py`'s own `format_needs_review_message`/`send_slack` functions directly (not
   `notify_needs_review`/`_send`, which check `notifications.slack` and would have skipped) for
   `20260910-2250-hidden-costs-v2` (the 3-cartridge hidden-costs run), text prefixed `"[TEST] "`,
   real message format (gate history, claim counts, review paths, approve command) rebuilt from
   that run's own `REVIEW.md`. Webhook returned success (`send_slack`'s own `200 <= status < 300`
   check: `True`). `notifications.slack` deliberately left `false` afterward -- re-enabling it
   permanently is Caleb's call, not made here, so no future run can post automatically before he
   decides.
7. **Tests.** Mac clone: `.venv-local/bin/pytest -q` -- **592 total, 592 passed, 0 failed** (590
   baseline + 2 new byline tests; the policy-hermeticity fix keeps the count the same, it doesn't
   add a test). Server: `~/advertorial/.venv/bin/pip install -e . -q` then `.venv/bin/python -m
   pytest -q` -- **592 total, 592 passed, 0 failed**, matching. `git status` clean on both after
   this cycle's commits (server `out/`/`runs/` sweep artifacts are gitignored; the pre-existing
   untracked `tenants/peak-saunas/evals/approvals.jsonl` from an earlier cycle, unrelated, still
   present and still untouched).

## Cycle 23 (R29 run-id collision; CSS layering -- FIXLOG Cycle 19 mobile-pass gap, task_9b60a503)

1. **Run-id collision (review R29).** `pipeline.make_run_id` truncated to the minute
   (`%Y%m%d-%H%M`), so two runs of the same input inside one wall-clock minute wrote into the
   same run directory, silently overwriting one another; it also made
   `test_workflows.py`'s "workflow reproduces harness run" comparison vacuous (both entry points
   resolved to the same directory within a minute). Fixed: `make_run_id` now formats
   `YYYYMMDD-HHMMSS-<slug>-<4 random lowercase base32 chars>` (seconds plus a
   `random.choices` suffix over `abcdefghijklmnopqrstuvwxyz234567`, ~1M combinations). New
   `pipeline.make_run_dir(base_dir, slug)` is the actual uniqueness guarantee --
   `os.makedirs(run_dir, exist_ok=False)`, one retry with a freshly generated id on a real
   collision, then raises. `pipeline.prepare_run` and `cli.cmd_ingest` (which duplicated the old
   two-line construction independently, R20's flagged duplication) both now go through it;
   `cli.make_run_dir` re-exported alongside the existing `cli.make_run_id`. No code outside
   `pipeline.py` parses a run id's shape (`notify.py`/`log.py`/`runstate.py`/`cli.py` all treat
   it as an opaque string, `run_dir.name` or interpolated into messages) -- confirmed by a
   repo-wide grep before landing this, so there was nothing else to update. Older,
   minute-granularity run dirs are untouched and stay readable (`make_run_dir` only ever creates
   a new directory, never touches an existing one unless it collides).
   `tests/test_workflows.py::_newest_run_dir` and four `out_dirs[-1]` call sites in
   `tests/test_cli_run.py` assumed a run id's lexicographic order matched creation order, which a
   random suffix breaks when two runs land in the same second (the fast fake-client test suite
   does this constantly); replaced with a new shared `tests/support.py::newest_run_dir()` that
   picks by directory `st_mtime` instead of by name. New `tests/test_pipeline.py` (7 tests):
   format, uniqueness under a frozen-clock/50-draw stress case, the real
   `os.makedirs(exist_ok=False)` retry-then-raise path (deterministic via a monkeypatched
   `make_run_id`), and that a legacy minute-granularity run dir is left alone.
2. **CSS layering (FIXLOG Cycle 19 mobile-pass finding, `task_9b60a503`).** The renderer loaded
   EITHER a tenant's `brand/base.css` OR `harness/fallback.css`, never both
   (`render.load_brand_css`) -- a tenant stylesheet that styles only its own theme classes
   (`.btn-primary`, `.container`, ...) and never touches the harness's `adv-*` classes at all
   left every cartridge template's structural classes (`.adv-cta`, `.adv-sticky-cta`,
   `.adv-financing`, image sizing, ...) with no matching CSS rule once that tenant had its own
   `base.css` in place: 21 of 23 swept pages had unsized `<img>` tags, and every `longform`
   page's sticky CTA bar had no CSS at all. Fixed: `harness/fallback.css` renamed to
   `harness/structure.css` (`git mv`, `pyproject.toml`'s package-data updated) -- the
   layout/component/responsive/token-default layer every cartridge template's classes are
   defined against, now **always** loaded first. `render.load_brand_css` split into
   `load_structure_css()` (always the harness file) and `load_tenant_css(brand_dir, log=None)`
   (the tenant's `base.css` if present and non-blank, else `""`); `render_page` passes both into
   the template as `structure_css`/`tenant_css`, and `harness/templates/base.html` inlines two
   `<style>` blocks in that order (the tenant one only when non-empty) -- an override layer, not
   a replacement. `structure.css` gained every selector a cartridge template references that
   `fallback.css` didn't have (`.adv-hero`, `.adv-proof-stats`/`-value`/`-label`, `.adv-problem`,
   `.adv-specs-proof`, `.adv-social-proof`, `.adv-faq`, `.adv-final-cta`, `.adv-warranty`,
   `.adv-angle`, root-element classes, an explicit `.adv-byline`), a generic
   `img { max-width: 100%; height: auto; }` rule, and a `@media (max-width: 480px)` block for the
   sticky bar (full-bleed, full-width CTA), hero (smaller type), tables (`display: block;
   overflow-x: auto` -- a pure-CSS scroll container, no extra wrapper markup needed), and proof
   rows (single column). `tests/test_css_coverage.py` (new, 5 tests) greps every
   `cartridges/*/template.html` for `class="..."` usage (Jinja-expression-only tokens excluded)
   and asserts each has a selector in `structure.css`, except a class a cartridge's own inline
   `<style>` block already defines for itself (listicle ships a fully self-contained `.pk-*`
   design system on purpose -- unaffected by this bug, since it never depended on
   `fallback.css`/tenant `base.css` for anything). Image sizing: `render.resize_asset_bytes`'s
   already-decoded Pillow image is the source of truth, but its `(bytes, ext)` return shape
   (6 existing call sites in tests) was left alone -- instead `download_asset` re-opens the
   final (already downscaled) bytes via a new `_image_dimensions()` helper and now returns
   `(path, width, height)` instead of a bare path; `render_page` sets `asset["width"]`/`["height"]`
   when known. Every cartridge template's asset-backed `<img>` tag gained a conditional
   `width="..." height="..."` (only emitted when the renderer knows them) alongside the CSS's
   unconditional `max-width: 100%; height: auto`. `docs/GENERATOR.md` ("Add a new cartridge
   type") and `docs/TENANT-ONBOARDING.md` ("Brand") now describe `brand/base.css` as an override
   layer over `harness/structure.css`, not a fallback pair; `docs/HARNESS-MAP.md`'s renderer row
   and `tenants/_template/brand/base.css`'s own header comment updated to match. New
   `tests/test_css_layering.py` (8 tests): `load_structure_css`/`load_tenant_css` in isolation,
   `render_page` inlines `structure.css` even with no tenant `base.css`, the tenant stylesheet
   loads strictly after structure.css in the rendered HTML, the exact cycle-23 shape (a tenant
   `base.css` that never touches `adv-*` classes still gets them from `structure.css`), and
   `width`/`height` attributes land on a rendered `<img>` when the renderer knows the dimensions.
   Two existing `download_asset(...)` call sites (`tests/test_path_safety.py`,
   `tests/test_render.py`) updated for the new 3-tuple return; one existing render test
   (`test_render_page_omits_longform_proof_stats_row_when_absent`) updated from "the class name
   never appears in the HTML" (true only because no stylesheet defined it before this fix) to
   "the class is never used as a `class="..."` attribute" (structure.css's own rule for it is
   now always present in the inlined `<style>` block, correctly, regardless of whether that run's
   page actually uses it).
3. **Tests.** Mac clone: `.venv-local/bin/pytest -q` -- **695 total, 695 passed, 0 failed** (675
   baseline + 7 run-id + 5 CSS-coverage + 8 CSS-layering new tests). `ruff check .` clean.

### Verify (server, real `harness run`/`harness review`)

See report for the real-run verification against `hidden-costs-v2.mov` and
`product-features-v2.mov --cartridges listicle`, the per-template static mobile checks, and the
server test count.

## Cycle 24 (final Friday sweep, 2026-09-11)

No code changes this cycle -- pure verification sweep confirming Cycle 23's run-id and
CSS-layering fixes hold under a full real-run pass, plus the recurring per-fixture PASS/STOP
and content checks.

1. **Full fixture sweep, all real `harness run` calls, foreground, one at a time, 600s timeout,
   default three cartridges (article/longform/product-page) on all seven fixtures, plus
   `--cartridges listicle` on `hidden-costs-v2.mov` and `product-features-v2.mov`.** Seven of
   seven fixtures PASS, both listicle runs PASS. Two attempts STOPped at the claims gate
   (`price-comparison-v2.mov`'s 1st attempt, `still-infraredglow-4x5.png`'s 1st attempt) on a
   trigger-word/no-claim_id pattern already on record from prior sweeps -- not a Cycle 23
   regression; both converged cleanly on their real 2nd attempt. Total real spend across the
   nine final PASS attempts: **$1.9612**. See `tenants/peak-saunas/docs/SWEEP-2026-09-11-final.md`
   for the full run-id table, attempts/repairs, word counts, and both STOP writeups.
2. **Cycle 23 fixes reconfirmed clean under real runs.** Run-id collision fix: all nine runs
   this cycle got unique `YYYYMMDD-HHMMSS-<slug>-<4char>` directories, including two same-input
   reruns within the same session (`price-comparison-v2`, `still-infraredglow-4x5`) that landed
   in distinct directories as designed. CSS-layering fix: all 23 rendered pages load
   `structure.css` before the tenant's `brand/base.css`, all 80 real `<img>` tags across those
   pages carry `width`/`height` attributes (0 missing), and all 7 `longform` pages'
   `.adv-sticky-cta` bar has a matching CSS rule and is actually used in the body. No
   regressions found.
3. **New findings, not code changes (flagged as follow-ups, not fixed this cycle):**
   `cartridges/product-page/template.html` never renders a byline block at all (confirmed via
   template source -- no "byline" reference; only unused `.adv-byline`/`.byline` CSS rules
   exist), so all 7 `product-page` pages this sweep have no byline line, by cartridge design,
   not a per-run defect. Separately, `cartridges/article/template.html`'s financing paragraph
   is conditional on the writer populating `page.financing_line`; 2 of 7 `article` pages this
   sweep left it unset and instead paraphrased financing into body prose, not verbatim to the
   fixed sentence "Financing is available through Bread Pay at checkout." -- confirmed via each
   run's own `page.json` (`financing_line: null`) and the rendered prose. Neither is a
   claims-gate or EMF/lender violation. See `docs/SWEEP-2026-09-11-final.md`'s byline and
   financing sections for detail and exact text.
4. **Banned-term check.** Zero hits across all 23 pages, checked directly against each page's
   own visible text with `harness.claims.find_forbidden_visible_text` (not a raw grep), run via
   a one-off script through `.venv/bin/python` -- no tenant `.env` read or printed at any point.
5. **Deliverables.** Every PASS run's `harness review` and `harness shopify-body` outputs
   generated per cartridge; the 23 `*-review.html` files copied and renamed
   `<ad>-<cartridge>-review.html` under `tenants/peak-saunas/out/FRIDAY-2026-09-11/`, with a
   `README.md` index (file listing, sizes, the sweep table). No Shopify call was made at any
   point -- `shopify-body` only renders a static local export.
6. **Policy / tree state.** `ad_overclaim_policy` unchanged (`"warn"`).
   `notifications.slack` unchanged (`false`) -- no Slack message sent or attempted this cycle.
   `git status` clean on the server after this cycle's commits (`out/`/`runs/` sweep artifacts
   gitignored; the pre-existing untracked `tenants/peak-saunas/evals/approvals.jsonl`, unrelated,
   still present and untouched).
7. **Tests.** Server: `~/advertorial/.venv/bin/pip install -e . -q` (clean, no output) then
   `.venv/bin/python -m pytest -q` -- **695 total, 695 passed, 0 failed**, matching Cycle 23's
   count exactly (no test changes this cycle, sweep-only).

## Cycle 25

**Assignment.** Cycle 24's final sweep found 2 of 7 `article` pages left `financing_line`
unset and paraphrased financing into body prose instead ("Financing sentence check" in
`docs/SWEEP-2026-09-11-final.md`); asked to make the financing gate cartridge-independent so
`article` no longer escapes it, mirroring the warranty fix.

1. **Read first, before writing anything.** The assignment's literal spec ("the financing check
   walks every text field of page.json for any cartridge: any text containing 'financ' ...")
   is the exact shape `find_financing_violations` used, and reverted, in Cycle 6: 8 real-run
   STOPs on ordinary buyer-education prose (an FAQ question, "financing terms and sticker
   price are two separate questions", a coincidental digit from the product's own short_name)
   before Cycle 6 re-scoped the check to the one dedicated `financing_line` field. Cycle 21
   re-affirmed that scoping explicitly when it revisited this exact function. Implementing the
   assignment's literal wording would have reintroduced that regression across every
   cartridge, not just `article`, and very likely re-broken this cycle's own verification runs
   the same way. Implemented the *goal* (article's paraphrases get caught and fixed) using the
   same anti-false-positive shape `find_warranty_violations` already established (fix cycle 7/16):
   a text field is only scanned once it actually ASSERTS financing terms, not merely mentions
   the topic.
2. **`harness/claims.py`: `find_financing_violations` gains a cartridge-independent prose
   scan.** New `_find_financing_prose_violations` walks every string field (skipping
   `NON_PROSE_KEYS` and the already-handled `financing_line` key itself, same as
   `find_warranty_violations`) and flags one only if `_financing_states_terms` is true --
   the text mentions "financ" AND (names a lender, configured or forbidden, via the existing
   `vocab.LENDER_NAME_RE` plus a `financing_lender` substring check; OR states a monthly
   figure/APR, `_FINANCING_FIGURE_OR_APR_RE`; OR echoes the allowed sentence's own "available
   ... at checkout" structure) -- and `_is_allowed_financing_prose` is false: the field isn't
   the allowed sentence and doesn't contain it verbatim as its own whole sentence (split on
   `[.!?]\s+`, mirroring Cycle 6/7's "combined field" exemption) with nothing else in the field
   stating a monthly figure, APR, or a lender other than the configured one. Both of Cycle 24's
   real violating sentences name the configured lender ("Bread Pay"), so both trip the trigger
   without needing a bare "financ" scan; `test_find_financing_violations_ignores_ordinary_prose_
   that_discusses_financing` (Cycle 6's own regression test) still passes unchanged -- confirmed,
   not assumed.
3. **`harness/cli.py`: `_fix_financing_violation` reused as-is** for the new prose-field
   violations -- same whole-field replacement it already does for the dedicated `financing_line`
   field (mirrors `_fix_warranty_violation`). `apply_deterministic_fixes`'s issue-text match
   widened from `"financing_line must be exactly"` to also match the new prose message,
   `"financing wording must be exactly"`.
4. **`cartridges/article/cartridge.md`.** Close step (item 8) now spells out that
   `financing_line`, when used, must be exactly the allowed sentence as its own sentence, and
   a new sentence: never paraphrase financing anywhere in the piece -- use the exact sentence
   verbatim or don't mention it. Schema (`financing_line` optional) and template were already
   correct and untouched.
5. **`cartridges/product-page/cartridge.md`.** Added one line to the Footer step noting the
   page intentionally has no byline (unlike article/longform/listicle) and that the disclosure
   paragraph still renders -- not a defect, per Cycle 24's own "New findings" note; no code
   change, template already correct.
6. **Tests** (7 new, `tests/test_claims.py`, `tests/test_repair_loop.py`): both of Cycle 24's
   real paraphrases, verbatim, now flagged (`find_financing_violations`) and resolved
   deterministically (`apply_deterministic_fixes`, `write_and_gate_page` end to end, zero
   repair calls); the dedicated-field "combined with unrelated content" exemption still holds
   for prose fields; an article page with no financing mention at all still passes cleanly,
   `deterministic_fixes == [0]`. `product-page`/`longform`/`listicle` tests unchanged --
   confirmed by the full suite, not assumed. `.venv-local/bin/python -m pytest -q` on the Mac
   clone: **702 total, 702 passed, 0 failed** (695 + 7 new). `.venv-local/bin/ruff check .`:
   clean.

### Verify (server, real Claude calls, foreground, one at a time)



`~/advertorial/.venv/bin/pip install -e . -q` then `.venv/bin/python -m pytest -q` on the
server: **702 total, 702 passed, 0 failed**, matching the Mac clone exactly.

- **`price-comparison-v2.mov`**: 1st attempt (`20260911-010454-price-comparison-v2-kkbz`)
  STOPped -- unrelated claims-gate issue (an FAQ sentence with a customer-quoted dollar figure
  missing a claim_id, the same pattern Cycle 24 hit on this fixture; nothing to do with this
  cycle's financing change). Rerun **PASSed**: `20260911-010829-price-comparison-v2-ebcu`.
- **`product-features-v2.mov`**: **PASSed** on the first attempt: `20260911-011051-product-features-v2-57yx`.
- Both `article` pages' `page.json`: `financing_line` is exactly `"Financing is available
  through Bread Pay at checkout."`; a full-tree walk for the substring "financ" in both files
  finds exactly that one hit, at `$.financing_line`, in each -- no paraphrase anywhere else on
  either page. Confirmed the same way against the rendered `index.html` (`grep -o
  'Financing[^<]*'`): one match per page, byte-identical to the allowed sentence.
- `harness review` run on both new run dirs; `article-review.html` copied into
  `tenants/peak-saunas/out/FRIDAY-2026-09-11/`, replacing the two prior `article-review.html`
  files for these ads (`longform`/`product-page`/`listicle` review files for these ads
  untouched -- that gap was `article`-only). That folder's `README.md` (gitignored, server-only)
  updated: file sizes for the two replaced files, the sweep table's run ids for rows 2/3, and a
  Cycle 25 update note; the two rows' dollar cost is left blank -- no cost ledger was found for
  an ad-hoc rerun, and Cycle 24's original `$1.9612` total predates these reruns so it's called
  out as not updated rather than silently left looking current.
  `tenants/peak-saunas/docs/PACKET-DRAFT.md` (git-tracked): the two runs' ids updated
  everywhere they appear (table + `harness approve`/`publish --dry-run` commands), plus a
  Cycle 25 update note.
- `git status` on the server: clean except the pre-existing untracked
  `tenants/peak-saunas/evals/approvals.jsonl`, unrelated.

## Cycle 26

**Assignment.** A reviewer web app (`harness serve`) so a reviewer can see and act on a run
from a browser instead of the CLI, plus a feedback-driven revision command (`harness revise`)
that applies what a reviewer asked for and writes a new, versioned page.

1. **`harness/runstate.py`**: new `request_changes` (the "Request changes" action -- validates
   the reviewer against `tenant.yaml`'s `reviewers` list, same rule as `approve`/`reject`;
   appends `{page, by, at, scores, notes, cuts}` to `state.json`'s `feedback` list; sets that
   page's state to the new `PAGE_CHANGES_REQUESTED` ("changes_requested") value -- a per-page
   state the run-level `state` never takes), `latest_feedback` (the last feedback entry for a
   page), `mark_revised` (page back to `needs_review` once a new version is written), and
   `set_revise_status`/`get_revise_status` (so the review site can poll a background revise's
   progress across process restarts -- stored in `state.json`, not in memory).
2. **`harness/revise.py` (new)**: `harness revise <run-dir> --page <cartridge> [--by <email>]`.
   Reads the latest feedback entry; `cut:` lines are pulled out of the reviewer's free-text
   notes by `parse_cuts_and_notes` and applied deterministically (`apply_cuts_to_page`) --
   a normalized-whitespace sentence match, recursive over every string field, dropping a list
   item whose own `text` field the cut emptied out. No model call for cuts alone. Free-text
   notes left over, if any, get one writer call (`write_page` with a "REVIEWER NOTES -- apply
   these changes and keep everything else" block carrying the current, already-cut page as
   context) whose result feeds `write_and_gate_page`'s existing bounded repair loop (via its
   `initial_page` param, the same one `--batch` mode uses) -- so a revise gets the SAME
   word-range/CTA/financing/warranty/claim-id/vocab gates and repair behavior a fresh run does,
   with no new gate logic written for this cycle. Fresh budget per revise: 60k tokens / 4 calls
   (`REVISE_TOKEN_BUDGET`/`REVISE_CALL_BUDGET`), separate from a run's own 220k/14. Before
   writing, the current `page.json`/`index.html`/`<page>-review.html` are renamed to
   `.vN.`/`-review.vN.html` (N starting at 1); the new version is rendered with the same
   `render_page` a fresh run uses, `harness/review.py`'s `build_reviews` regenerates the
   run's review HTML, and a `## Revision: <page> vN` section is APPENDED to `REVIEW.md` (the
   original generation report from `cli.write_review_md` is never rewritten -- reconstructing
   its `gate_matched`/`budget`/`ad_not_repeated` context at revise time would mean duplicating
   most of `pipeline.py`'s own bookkeeping for no real benefit). `runstate.mark_revised` puts
   the page back to `needs_review`; `notify.notify_revise_complete` (new) fires the same
   fail-closed way every other notification in this harness does. A repair loop that exhausts
   every attempt (`ClaimsGateFailure`) or the revise budget (`BudgetExceeded`) leaves every file
   on disk untouched and re-raises as `ReviseError` -- same "never write a page that failed the
   gate as though it passed" rule a fresh run follows; the reviewer's feedback is still there to
   revise from again.
3. **`harness/cli.py`**: `cmd_revise` (thin wrapper -- calls `revise.revise_page`, prints the
   result, exit 1 on `ReviseError`) and its `revise` subparser. `cmd_score`'s body extracted
   into `record_score(tenant, *, run_dir, angle, brand, claims, publish, by=None, note="",
   page=None)` so both `harness score` (unchanged output -- `page` is only added to the JSON
   line when given) and the review site's per-page feedback form write the exact same
   `scores.jsonl` schema through the exact same function -- no duplicated write path. `cmd_serve`
   (new) loads/activates the tenant, calls `serve.build_app`, runs it.
4. **`harness/serve.py` (new)**: the reviewer web app. Plain HTML/CSS built the same way
   `cli.write_review_md` builds Markdown (Python string joins, `html.escape` on every value
   that came from disk or a reviewer) -- no template files, no CDN, no JS framework; a few
   lines of inline JS toggle an iframe's CSS class between 1200px/390px widths. Auth:
   Cloudflare Access header (`REVIEW_TRUST_CF_ACCESS=true` + a matching
   `Cf-Access-Authenticated-User-Email`, falling back to basic auth if the header is absent so
   a direct request can't skip auth) else HTTP basic auth (`hmac.compare_digest` against
   `REVIEW_PASSWORD`); `build_app` refuses to construct the app at all without
   `REVIEW_PASSWORD` set. `/` lists runs (state, ad, product, cartridges, cost and "claims not
   repeated" count scraped from `REVIEW.md` -- neither is persisted as JSON anywhere else --
   and reviewer actions from `state.json` history), newest first. `/run/<id>` shows the source
   ad (an image route for `input_type: still`, the transcript inline for `video`/`text`), the
   ad brief, and each page: an iframe of `<page>-review.html`, the width toggle, existing
   feedback history, and a feedback form (four 1-5 selects, a "Notes and cuts" textarea, three
   buttons). "Approve page"/"Request changes" call `runstate.approve`/`request_changes` for
   that one page; "Reject page" calls the existing `runstate.reject`, which -- like the CLI's
   own `harness reject` -- rejects the WHOLE run (no per-page reject exists in `runstate.py`;
   the button says so). "Request changes" with its "Regenerate now" box ticked launches
   `python -m harness.cli revise ... ` as a background `subprocess.Popen`, logged to
   `tenants/<t>/runs/<run>-revise-<page>.background.log`; its status is polled by re-reading
   `state.json` on reload (`runstate.set_revise_status`/`get_revise_status`), not by any
   in-process state. "Approve all" only renders once every page has at least one recorded
   score. Path safety (`_safe_path`/`_safe_dir`): every request-supplied path component goes
   through `textutil.safe_filename` (Cycle 22 finding R36, reused here for a URL path instead
   of a single filename) and the resolved path must still be inside the tenant's `out_dir` (or
   `fixtures_dir` for a still image) -- both enforced, both tested directly and through real
   HTTP requests attempting `..%2f` traversal.
5. **`crons/harness-review.service` (new)** + **`crons/install.sh`**: the review service is a
   long-running server with no matching `.timer`, unlike every other unit here, so it's
   excluded from the generic `*.service`/`*.timer` render loop and only rendered + `systemctl
   --user enable --now`'d when `--with-review` is passed (falls back to a `nohup`, still bound
   to 127.0.0.1, with a printed command, if `systemctl --user` isn't available). Its
   `EnvironmentFile` points at `tenants/<tenant>/.env`, `Restart=on-failure`.
6. **`docs/REVIEW-SITE.md` (new)**: the exact Caddy vhost stanza and Cloudflare Tunnel ingress
   YAML for `review.<domain>` -> `127.0.0.1:4870`, why Cloudflare Access with email OTP is the
   recommended gate, the interim `ssh -L 4870:127.0.0.1:4870 prod` access path, and how to set
   `REVIEW_PASSWORD` without ever printing it.
7. **`pyproject.toml`**: added `flask` -- a small, mature WSGI framework for `harness/serve.py`
   rather than hand-rolling routing/auth/multipart-form parsing over `http.server`.
8. **Tests** (32 new -- `tests/test_revise.py` 9, `tests/test_serve.py` 19, 4 more appended to
   `tests/test_path_safety.py`): cut-only revise makes no model call (a `make_client_fn` that
   raises if called) and removes the sentence; a cut only drops the list item whose own text it
   emptied, leaving an unrelated sibling paragraph untouched; notes-only revise calls the
   writer exactly once (asserted on the fake client's own call log) and the REVISION block it
   sent carries the reviewer's notes and "keep everything else"; a second revise on the same
   page produces v2 with v1 still on disk; `ReviseError` with no feedback recorded and for an
   unknown page. Serve: no-auth 401, wrong password 401, non-reviewer 403, correct reviewer
   200; CF Access header trusted when enabled, rejected for an unlisted email, and falls back
   to basic auth when the header is missing; run list and run detail render (iframe, form,
   "Notes and cuts" help text); a page-review route serves the real HTML; the approve action
   writes both `scores.jsonl` (with `page` set) and `state.json` through `record_score` and
   `runstate.approve`; request-changes records `cuts`/`notes` split correctly; reject flips the
   whole run; "Approve all" only appears once every page has a score; three traversal attempts
   (`..%2f` in a review-html request, `..%2f` as a run id, and `_safe_path` called directly)
   all come back 404/None. Every existing test still passes. `.venv-local/bin/pytest -q` on the
   Mac clone: **734 total, 734 passed, 0 failed**
   (702 + 32 new). `.venv-local/bin/ruff check .`: clean.


### Verify (server)

`~/advertorial/.venv/bin/pip install -e . -q` then `.venv/bin/pip install -q flask`, then
`.venv/bin/python -m pytest -q` on the server: **734 total, 734 passed, 0 failed**, matching
the Mac clone exactly.

`crons/install.sh --with-review peak-saunas "$(pwd)"` on the server: `systemctl --user` was
available, so it rendered and `systemctl --user enable --now`'d
`harness-review@peak-saunas.service` (the four existing digest/refresh timers were also
rendered, as before, and left un-enabled). `systemctl --user is-active
harness-review@peak-saunas.service` -> `active`.

`REVIEW_PASSWORD` set: `openssl rand -base64 15` appended to `tenants/peak-saunas/.env` as
`REVIEW_PASSWORD=...` inside one remote shell script that never echoed the value -- only
`grep -c ^REVIEW_PASSWORD` (count 1) and curl's own status codes were ever seen. Read it on
the server with `grep ^REVIEW_PASSWORD ~/advertorial/tenants/peak-saunas/.env`.

`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4870/`:
- No auth: **401**, `WWW-Authenticate: Basic`.
- `-u caleb@peaksaunas.com:<REVIEW_PASSWORD>`: **200**.
- `-u caleb@peaksaunas.com:wrong-password`: **401**.

**Real revise** (1 real Claude call, foreground on the server, well under the 600s cap):
picked the hidden-costs-v2 article from the FRIDAY-2026-09-11 sweep, run
`20260911-001517-hidden-costs-v2-7tnk` (`tenants/peak-saunas/out/FRIDAY-2026-09-11/README.md`'s
own sweep table names it as that ad's final PASS run). Posted feedback through
`/run/<id>/page/article/action` with curl + basic auth: one `cut:` line copied verbatim from
`article/page.json`'s `open[0].text` ("She said she typed in her contact information more than
once just to see a number on a screen, and each time a sales call followed within the hour."),
one note ("shorten the opening paragraph by half."), `regenerate=1` ticked. `state.json`'s new
feedback entry confirmed the cut/notes split correctly; page state went to
`changes_requested`; the review site launched `harness revise` as a background subprocess as
designed.

Result (`tenants/peak-saunas/runs/20260911-001517-hidden-costs-v2-7tnk-revise-article.background.log`): `Revised article for .../20260911-001517-hidden-costs-v2-7tnk
-> v1 (1 cut(s), writer called=True, gate=PASS)`. Confirmed directly, not just from the log
line: `article/page.v1.json` (9,924 bytes, the original) and `article/index.v1.html` sit
alongside a freshly written `article/page.json` (9,738 bytes, the cut sentence gone) and
`index.html`; `article-review.v1.html` alongside a regenerated `article-review.html`.
`state.json`'s page state is back to `needs_review`, history shows `changes_requested` then
`needs_review; revised to v1; gate PASS`, and `revise_status.article` is `{"status": "done",
"detail": "gate PASS"}`. `REVIEW.md` carries a new `## Revision: article v1` section: the cut
sentence, the note, "Writer called: yes", "Gate result: PASS", and "Estimated cost of this
revision: $0.0812" (one writer call, well inside the fresh 60k-token/4-call revise budget --
`calls_used: 4` in the revision's own budget summary above that section was the ORIGINAL run's
total, not this revise's; the revise's own cost is the separate $0.0812 line).

`git status` on the server: clean except two long-standing untracked files,
`tenants/peak-saunas/evals/{scores,approvals}.jsonl` -- the test suite's approve/score tests
(pre-Cycle-26; `tests/test_cli_approve_publish.py` and others) write to the tenant's real
`evals/` path rather than an isolated fixture, so running the suite on the server (as every
prior cycle's own FIXLOG entry also did) appends to those files; neither is tracked or
gitignored today. Pre-existing test-isolation gap, not touched this cycle -- named here rather
than silently worked around.

## Cycle 26b

Hotfix on the review site (harness/serve.py), reported from a screenshot: a run made with
`harness run` alone (no separate `harness review`) showed "Not Found" inside the run detail
page's iframe.

### Bug 1 -- iframe 404 when `<page>-review.html` doesn't exist yet

`harness/review.py`'s `build_reviews` was split into a new `build_review_for_page(run_dir,
page_name)` (one cartridge) that `build_reviews` now just loops over -- same behavior, no
duplicated inlining logic. `harness/serve.py`'s `page_review` route: when the review file is
missing but `<run>/<page>/index.html` exists, it now calls `build_review_for_page` on demand,
writes the result to the expected path, and serves it; when neither file exists it serves a
200 "Page not rendered yet" page (was Flask's default 404) so the iframe always shows
something readable. `harness/pipeline.py`'s `execute()` now also calls `review.build_reviews`
itself right after a successful run (before `state.log.close()`), wrapped in a bare
`try/except` that logs a `run:` warning line to the run's own log instead of failing the run --
so new runs get every page's review html without a separate `harness review` call, and old
runs fall back to the on-demand path above.

### Bug 2 -- run list showed test-suite artifacts

`tests/conftest.py`'s `FakeClient` runs every test in this suite against the tenant's real
`out/` dir (`tests/support.py`'s `TENANT`), so 161+ test runs had accumulated there
indistinguishable from real ones. `harness/runstate.py`'s `init_state` gained a `dry_run` flag,
written into state.json going forward; `harness/pipeline.py`'s `prepare_run` sets it by
checking `type(state.client).__name__ == "FakeClient"` (no import of the test double). The run
list (`harness/serve.py`'s `run_list` route and new `_looks_like_test_run` helper) hides a run
by default when `state.json`'s `dry_run` is true, its run log has no `input_tokens=` line
(covers pre-fix fake runs and any run log doesn't exist for), or it has no page's rendered
directory at all -- with a "Show test runs (N hidden)" / "Hide test runs" toggle
(`?show_test=1`) linking back and forth. A new `_is_run_dir` helper (state.json or
ad_brief.json present) replaces the old bare `state.json` check everywhere a run id is
resolved, so non-run directories under `out/` (`FRIDAY-2026-09-11`, `_archive-test-runs`) were
already excluded by the state.json check and stay excluded now via the same helper.

### Bug 3 -- checked, nothing broken

Run list already showed ad name/product (`_run_summary`), sorted newest-first
(`runs.sort(key=..., reverse=True)`), and the run detail page already renders the source ad
inline (`_render_source_ad`). `harness/revise.py`'s versioning (`index.vN.html`,
`<page>-review.vN.html`) already leaves the *current*, unversioned `index.html` /
`<page>-review.html` in place after every revise -- `page_review`'s lookup is unaffected.
Locked in with a new test (`test_page_review_still_resolves_after_a_revise`) that revises a
page and confirms the review route serves the new content, not a 404 or a stale version.

### Tests

New: on-demand review build (`test_page_review_builds_on_demand_when_review_file_missing`),
missing-page placeholder (`test_page_review_placeholder_when_page_never_rendered`), test runs
hidden by default / shown with the flag, now also checking ad name + product appear
(`test_run_list_hides_test_runs_by_default_and_shows_with_flag`, replaces the old
`test_run_list_renders_the_run`), non-run dirs skipped from the list and 404 by id
(`test_non_run_dirs_are_skipped`), and the Bug 3 revise/lookup check above.

`.venv-local/bin/pytest -q` on the Mac clone: **738 total, 738 passed, 0 failed** (734 + 4 net
new). `.venv-local/bin/ruff check .`: clean.

## Cycle 28 — merge kimi/long-run

Merged the external `kimi/long-run` branch (12 commits, 130 files,
+17,665/-1,214) into `master` after `docs/REVIEW-KIMI-LONG-RUN.md`'s review
(reviewed at `67b4fef` against `master` at `3eb4a2b`, in a throwaway worktree
on prod) came back **MERGE WITH FIXES**: the engineering itself was judged
unusually disciplined (the `cli.py` split is a genuine move, not a rewrite;
generated pages byte-identical to master), with two required fixes before
the merge commit, neither touching the gate's own logic.

The twelve commits, in order:

1. `f83805c` phase 0 (bootstrap): `evals/fake_run.py` -- the suite's dry run as a standalone command
2. `ceab311` phase 1 (baseline): `docs/KIMI-BASELINE.md` + `evals/baseline/` regression reference
3. `a8ee766` phase 2 (checks): deterministic image/link/JSON-LD/HTML checks in the page gate
4. `3557741` phase 3 (blocks): `harness/blocks` registry, block gate, writer block selection, daily spend cap
5. `b0b4f59` R1/R2 (+R5): split the domain logic out of `cli.py`; the import cycle dies
6. `b9af67d` R11: one page walker (`textutil.walk_page`) replaces ten hand-written recursions
7. `9535a08` R23: config precedence stays; disagreements become visible
8. `9188fc4` R20 + R24 + R9: shared budget-abort tail, call-time config paths, spec glossary
9. `88a70cf` phase 5 (eval-export): `harness dataset export` + `harness eval report`
10. `9072c20` phase 6 (comparison-cartridge): draft comparison cartridge, sourced table schema, competitor claims home
11. `d375553` phase 7 (runbook): `docs/PROMPTING.md` + the repair loop's OnFailAction vocabulary
12. `67b4fef` `evals/soak.py`: the 200-generation dry-run driver + the run's report

Merged on top of Cycle 26b (master had moved to `551c6ac` by the time the
merge landed): one conflict, in `harness/pipeline.py`'s `execute()` --
Cycle 26b's review-html-build block called the old pre-split
`_log_run_result` (a lazy `from .cli import _log_run_result`, the
circular-import workaround R1/R2 removed); resolved to
`review_md.log_run_result`, keeping Cycle 26b's build-review-html-on-success
block intact. Everything else auto-merged clean.

### K1 -- tenant product names in an engine-level cartridge schema

`cartridges/comparison/schema.json:30` named Peak's own products as the
worked example ("... e.g. 'the Fuji vs. the Mini' for {{ tenant.name }}'s
own lineup") -- the same class of leak as R40, and new with this branch.
Replaced with a neutral `'Model A vs. Model B'` example.

The R40 tenant-neutrality scan (`tests/test_tenant.py`) used to run over
`harness/` only; it now also runs over `cartridges/` and `harness/blocks/`,
widened by every product's short name read live from
`tenants/peak-saunas/claims/products.json` (not hardcoded, so a new product
is covered automatically) rather than just the fixed proper-noun list.
That caught one more real leak, `cartridges/listicle/cartridge.md:5`'s
"until Caleb approves it for the default rotation" -- fixed by dropping the
name. Two worked "Shop the Fuji" CTA examples in `product-page`/`longform`
`cartridge.md` are legitimate prompt text (the worked example of the
`{short_name}`/`{model_name}` substitution), not tenant leakage, and are
exempted the same way `harness/repair.py`'s existing `Fuji` example already
was. `harness/blocks/` itself came back clean under the wider list.

### K2 -- the daily spend cap was advisory, not enforced

`harness/budget.py:89-143` checked the cap once, before a run
(`pipeline.py:182`), and recorded spend only after (`pipeline.py:498/522/536`,
`cli.py:118`) -- two runs starting in the same window both read $0
committed and both proceeded; a ledger write failure
(`spend_ledger_path`'s file being read-only or `runs/` being full) was
logged and silently swallowed, making the cap infinite.

Fixed by reserving first: `budget.reserve_spend()` (replaces
`check_daily_cap`) runs once, at run start, before any model call. Under an
exclusive `fcntl.flock` on a lock file beside the ledger it reads today's
committed total -- finalized runs' actual cost, plus every reservation
whose run hasn't finalized yet -- and either appends this run's own
reservation (`{"run_id", "reserved_usd", "ts"}`; the estimate is the
tenant's `budget.per_run_usd` if set, else the median of the last 10
finalized runs' cost, else $0.60) or refuses with exit 3 and `daily spend
cap reached: $X of $Y (tenant <t>)` -- the whole read-decide-append happens
inside the lock, so two runs starting at once can't both see $0 and both
proceed. A ledger write failure during this step now raises
`LedgerWriteError` (a `BudgetExceeded` subclass, so every existing abort
path -- `pipeline.execute`, `cli.cmd_ingest` -- catches it unchanged)
instead of being swallowed: the run stops before ingest ever spends a
token. `record_spend()` at run end still logs-and-swallows a write failure
(the run already happened; crashing over bookkeeping now would be worse
than a missing line, same argument the original design made) -- the
reservation it should have reconciled just stays live until `harness spend
reconcile` drops it, which it only does once the run's own log shows a
final `run_result:` line **and** the reservation is more than 2 hours old
(a reservation with no final state on disk is left alone; it may still be
running). Bare `harness spend` prints today's spent/reserved/cap.

`tests/test_budget.py`: real two-thread concurrent `reserve_spend` under a
cap that allows exactly one reservation (only one passes, verified by
result and by the ledger holding exactly one reservation); an unwritable
`runs/` directory refuses with a clean `LedgerWriteError`, not a raw
`OSError`; `reconcile_stale_reservations` drops a stale reservation whose
run's log shows `run_result:` while leaving an equally stale one with no
final state alone, and leaves a fresh one alone regardless of final state.

### Non-blocking (F2, F3 -- F1 skipped)

`README.md` and `docs/GENERATOR.md` now document `harness dataset export`,
`harness eval report`, `harness spend`/`harness spend reconcile`, the
`harness/blocks/` layout registry, and the comparison cartridge (F2). This
entry is F3.

### Verify

Full suite green on the merge commit (not just the branch tip): **827
passed**. `ruff check .`: clean. Both `evals.fake_run` dry runs
(`founder-warranty-demo.txt`, `hidden-costs-v2.transcript.txt`) stay
byte-identical to `evals/baseline/` on all three cartridges -- K1/K2 only
touch a prompt-text example and spend bookkeeping, never page generation.
Real-run verification (`harness run
tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas`,
foreground, 600s, on a server worktree of the merge branch) and the landing
onto `master` happen after this entry -- see the merge commit and this
cycle's operator report for that run's result and cost.
## Cycle 27 (brand-kit import)

Tenant-agnostic `harness brand import --tenant <t> (--drive-folder <url-or-id> | --local
<dir>) [--dry-run] [--force]`, so any tenant (not just Peak, whose brand files were all
hand-extracted in earlier cycles) can point the harness at a Drive folder or a local
directory and get a usable `brand/` layer.

**Enumeration, no OAuth.** `harness/sources/drive.py`'s `list_public_folder(folder_id,
fetch=None)` parses a Drive folder's own public HTML listing -- the same no-OAuth,
public-link-only adapter pattern `download_drive_file` already used for a single file. First
draft assumed a `data-id="<id>"` attribute per item (going by the ingest research notes'
`agents/drive-index.md`-style description of the technique); fetching a real folder during
server verification (below) showed that never actually appears per item -- a real row is
`<div ... aria-label="<name> <type>? Shared|Limited access|..." ... ssk='<n>:<code>:<file
id>-<n>-<n>'>`, with the id inside `ssk` and the same id repeated across a name row and several
metadata rows (modified date, size, "more actions"), of which only the first carries the real
name. `list_public_folder` now parses that shape, `_clean_entry_name` strips the trailing
type/status words Drive's accessibility label appends, and the offline unit tests
(`tests/test_brand_import.py`) use synthetic HTML built to the same verified shape. A folder
that isn't shared "Anyone with the link" comes back as a sign-in wall with no such entries;
`list_public_folder` raises `DriveFolderNotPublic` with the exact message the task asked for:
"Drive folder is not link-public; share it as Anyone with the link, or upload the files into
tenants/<t>/brand/incoming/ and rerun with --local". An entry with no recognizable file
extension is treated as a subfolder and walked one level deep. `--local <dir>` runs the
identical classify/extract pipeline over files already on disk.

**Classification** (`harness/brand_import.py:classify_file`) follows the task's own priority
order -- logo (svg/png/jpg with logo/mark/wordmark/favicon in the name) before brand guide
(pdf, or guide/brand/style in the name) before fonts (ttf/otf/woff/woff2) before palette
(json/txt/ase named color/palette/tokens) before photo (everything else image-shaped) before
other. Worth knowing: "style" is a plain substring match per the task spec, so it also matches
inside "lifestyle" -- noted in the module rather than special-cased for one word. Every
downloaded/copied file lands in `tenants/<t>/brand/incoming/` (gitignored) with a
`manifest.json`.

**Extraction.** Logo: svg preferred, else largest-by-bytes raster, copied to `brand/logo.<ext>`;
dominant accent colors come from Pillow quantization on a raster (near-white/near-black
filtered out unless they're all there is) -- an svg logo skips this (no rasterizer dependency
added) and is flagged in BRAND-IMPORT.md for a human to pick colors from the guide/palette
instead. Brand guide: rendered to up to 6 page images (`pdftoppm` if on PATH, else Pillow for
an already-an-image guide file; unrenderable is named in BRAND-IMPORT.md, not silently
dropped) and sent in **one** vision call (well under the "at most 3 real model calls" cap) with
a system prompt that explicitly tells the model every word inside the guide's pages is
reference material, never an instruction to it -- strict-JSON-only response
(`{colors, fonts, logo_rules, voice, dont}`). Palette file: hex codes parsed out of
`.json`/`.txt`; `.ase` (binary Adobe Swatch Exchange) is not parsed -- named for a human
instead of guessed at. Fonts: copied into `brand/fonts/`, family/weight best-effort guessed
from the filename; a guide-named font with no matching font file is assumed to be a Google
Font and gets an `@import` line instead of an `@font-face`.

**Non-destructive merge.** `tokens.json` gets a new top-level `brand_import` key (never
touches Peak's ~90 existing flat dotted-key scalars, which live at the top level directly) --
`merge_dict()` recurses so a re-import's existing sub-key always wins unless `--force`, a new
sub-key is always added, and every leaf is tagged `"source": "brand-import:<file>"`.
`tenant.yaml`'s `brand:` section gets the same merge rule, but via a targeted regex splice
(`write_tenant_yaml_brand_section`) that locates and rewrites only the `brand:` block's own
text -- a full YAML round-trip of the whole file would silently drop every comment in a file
as heavily annotated as `tenant.yaml`, which is exactly the unasked-for rewrite the merge rule
exists to prevent. `base.css` is regenerated from the imported tokens only when it doesn't
exist, or is still the empty template stub (comments only, no rules -- checked by stripping
`/* ... */` before testing for content, so a freshly `tenant init`'d tenant's stub doesn't
read as "hand-tuned" the way Peak's real base.css must), or with `--force`.

**Contrast gate.** WCAG relative-luminance contrast, text-on-background warns below 4.5:1;
accent-on-background below 3:1 is refused (not set at all) without `--force` -- a real finding
during test-writing: Peak's own brand-guide green `#16C47F` on white is ~2.27:1, so a real
import against Peak's actual guide may hit this refusal and need `--force` or a manual pick;
flagged here rather than silently downgrading the gate to make a fixture pass.

**Renderer wiring.** `harness/render.py`'s `find_tenant_logo(brand_dir)` (checked svg first,
matching the importer's own logo choice) + `render_page` copying it into the run's
self-contained `assets/` folder, same treatment Fix 8 already gives an ad-sourced asset. New
`logo_url` template var, used by `product-page` and `longform` (the two cartridges whose own
`cartridge.md` already calls for "the tenant logo" in the header -- neither template actually
rendered one before this cycle). New `.adv-brand-logo` rule in `harness/structure.css`
(`tests/test_css_coverage.py` requires it). `harness review`'s asset-inlining and the Shopify
publisher's CDN-URL rewrite both already match any `src="assets/..."`, so the logo needs no
changes there.

**Tests** (`tests/test_brand_import.py`, 42 new): the folder-listing parser against a small
synthetic HTML sample (and its dedup, its not-public refusal with the exact message, an empty
-but-public folder not being mistaken for a refusal, one level of subfolder recursion); the
classifier; `merge_dict` (existing wins, `--force` overwrites, inputs never mutated,
source-tagging); the `tenant.yaml` `brand:` splice (rest of the file, including comments,
untouched); WCAG contrast math; `base.css` generation and its not-overwritten/`--force`-
overwritten cases; palette hex parsing and the `.ase` no-op; dominant-color extraction (a
solid-color PNG, and an svg returning `[]`); and a demo-tenant end-to-end
(`tenants/_template` copied into a `tmp_path` "tenants dir", never the real `tenants/`) that
imports a locally-built logo + palette.txt and confirms both the logo `<img>` and the imported
accent color actually land in `render_page`'s output HTML/`base.css` -- no `tenants/demo-co`
left in the repo, no real network or model call anywhere in the suite (the demo fixture has no
brand-guide file, so the one vision call this module makes is never exercised in tests).

`.venv-local/bin/pytest -q` on the Mac clone: **776 total, 776 passed, 0 failed** (734 + 42
new). `.venv-local/bin/ruff check .`: clean. (Four follow-up commits during server verification,
below, brought the Mac-clone suite to 784 -- see their own commit messages for what each fixed.)

### Verify (server)

`~/advertorial-c27` (git worktree of `cycle27/brand-import`, `git worktree add ../advertorial-c27
cycle27/brand-import`), own `.venv` (`/usr/bin/python3` -- `/usr/local/bin/python3` on this box is
a wrapper the deploy user cannot execute), `models`/`vendor` symlinked from `~/advertorial`,
`tenants/peak-saunas/.env` copied in. `.venv/bin/pip install -e . pytest pillow ruff flask`, then
`.venv/bin/ruff check .`: clean; `.venv/bin/python -m pytest -q`: **784 total, 784 passed, 0
failed**, matching the Mac clone exactly.

The first live fetch of the real folder (`1bR_iS3rOE_lNfOhz5BJUkJOw0wnvI-Xf`) immediately
disproved the data-id assumption the first commit above was built on -- a real Drive folder row
carries the file id inside an `ssk='<n>:<code>:<id>-<n>-<n>'` attribute, not `data-id`, with the
same id repeated across a name row and several metadata rows. `list_public_folder` and
`_clean_entry_name` were rewritten to that verified shape (fix commit 2). Three more real-data
findings, each its own fix commit, verification numbers folded into each commit message:
`looks_like_folder` mistook `.ai`/`.eps` design-source files in the real "Logo Files" subfolder
for a subfolder (no extension this classifier's buckets recognize) and sent a real file id into
a folder-listing fetch, which 404s (fix 3); `choose_guide` (two one-page color-variant PDFs were
listed before the actual, comprehensive "... BRAND GUIDE.pdf" and `guide_entries[0]` read the
wrong one -- fix 4); `choose_logo` (a big JPEG social-media photo beat a small transparent PNG
favicon purely on byte size -- fix 5); `write_tokens_json_brand_import` (the original
`json.loads`/`json.dumps` round-trip reformatted Peak's entire hand-authored tokens.json --
single-line arrays exploded, blank lines dropped -- even though only one new top-level key was
added, which fails this cycle's own "diff empty except added keys" bar just as surely as
clobbering a value would; rewritten to splice just that key by bracket-matching in the raw text,
mirroring `tenant.yaml`'s already-targeted splice -- fix 6, which also added
`_looks_like_a_real_font_name` after a guide with no named typeface came back
`"Sans-serif (appears to be a modern geometric sans-serif)"` and nearly became a broken Google
Fonts `@import`).

**Dry run**, `harness brand import --tenant peak-saunas --drive-folder
1bR_iS3rOE_lNfOhz5BJUkJOw0wnvI-Xf --dry-run`, with every fix applied: found 21 files under the
"Logo Files" subfolder (favicon/mountain-mark variants as .ai/.eps/.pdf/.jpg/.png -- none of the
.ai/.eps got past "other", this classifier has no vector-source bucket) plus the brand guide PDF
at the folder root; chose `favicon-Peak-Saunas-PNG.png` as the logo; sent 1 real vision call
(well under the 3-call cap) against "Peak Saunas BRAND GUIDE.pdf" and got back 5 colors, 2 font
entries (both a description, not a name -- correctly filed as human decisions instead of a
broken `@import`), and 9 logo-usage rules matching
`tenants/peak-saunas/brand/brand-guide-summary.md`'s earlier hand-read of the same PDF almost
verbatim (90px/32mm minimum width; don't rotate/distort/shadow/place-on-busy-backgrounds/use-
off-brand-colors). `base.css` would not be touched (already exists, no `--force`). The logo's own
dominant accent color, `#e9f6f3`, was refused as the accent token (1.11:1 contrast against white,
below the 3:1 floor) -- filed as a human decision, not silently dropped.

**Real run** (same command, no `--dry-run`, no `--force`): `git diff` on
`tenants/peak-saunas/brand/tokens.json` is exactly one hunk -- the pre-existing last line gains a
trailing comma and a new top-level `"brand_import"` key follows it; every one of Peak's ~90
existing dotted-key values is byte-identical, single-line arrays and blank lines included.
`tenants/peak-saunas/brand/base.css` diff is empty. `tenants/peak-saunas/tenant.yaml` diff is one
new `brand:` / `logo_path: brand/logo.png` block appended after `notifications:`, nothing else
touched (accent/primary hex were NOT written, since the contrast gate refused them, matching what
the dry run said it would do). `tenants/peak-saunas/brand/logo.png` lands as a real 48x48 RGBA
PNG (confirmed by opening it with Pillow, not just checking the file exists);
`brand/BRAND-IMPORT.md` is written with the same findings as the dry run. Real-run artifacts
(the tokens.json/tenant.yaml changes, logo.png, BRAND-IMPORT.md) were left uncommitted and
discarded with the worktree -- this cycle ships the tool, not a decision on Peak's actual new
brand tokens for a human to make.

`git worktree remove --force ../advertorial-c27` after.

## Cycle 29 — merge PR #2 (design-skills pack) and the DESIGN.md reference pack (2026-09-14)

- Merged github/cursor/design-skills-playbook-fdad (942ada8) after review docs/REVIEW-PR2-DESIGN-SKILLS.md: verdict MERGE; 895 tests; baseline byte-identical; real run PASS $0.29; no new runtime dependency; MIT skill vendored with attribution; raw SKILL.md never reaches a prompt.
- New: harness/design_skills/ (README, SOURCE.json, LICENSE, rules.json take/adapt/decline, adapter.py, gate.py wired into the page gate via pagechecks.find_design_skill_violations), blocks risk-reversal and tagline-reveal, CLI `harness design-skills list|explain|check`.
- Also on master (committed directly, docs-only, 2ee5271/3948622): harness/design_skills/design-md/ — ten DESIGN.md site analyses from getdesign.md (MIT, VoltAgent) as evidence; neutrality test skips vendored evidence files.
- Open: warm-up-window gate (brief item, not started); the Amin article rules stay [CONFIRM IN ARTICLE] until the text is available; tenant `design_reference` slugs → adapter (follow-up).
- Process note: reference packs were committed straight to master; code changes keep going through branches and review.

## Next-cycle queue (operator, 2026-09-14)
- Simplicity gate (from docs/RESEARCH-HORMOZI-LANDING.md section 3): (a) at most one distinct link above the fold per page, (b) headline word band per cartridge (article 8–14 already; set product-page 4–10, longform 6–12, listicle 8–14), (c) exactly one offer element per page (CTA block + optional financing sentence; no second offer card), (d) value-equation checklist injected into the product-page proof-bullet instruction (dream outcome, proof-backed likelihood, time to first benefit, install effort — verified claims only). Soft checks to REVIEW.md first; enforce after one clean sweep. Lands after cycles 30 and 31 merge.
## Cycle 30 — warm-up window gate + tenant design references (2026-09-14)

Branch `cycle30/warmup-and-design-refs`. Concurrent with cycle31/images
(harness/render.py, harness/structure.css, harness/ground.py,
cartridges/*/template.html, harness/shopify.py) -- none of those files
touched here.

**Part 1 -- warm-up window (article only).** `cartridges/article/schema.json`
gains `warmup_window_words: 600` (a sibling data key, same convention as its
own `allowed_cta_texts`); tenant.yaml gains `cartridges.article.
warmup_window_words` / `warmup_mode` (template default: 600 / "warn").
`harness/claims.py` gets `find_warmup_violations(page_json, tenant, window)`
and `warmup_first_mentions(page_json, tenant)`, in the file's own
find_*_violations pattern (as directed) but deliberately NOT wired into
`gate_page_json` -- unlike every check that function runs, whether this one
is even a hard gate is per-tenant. It walks the article's own reading order
(headline -> dek -> open -> body_sections -> alternatives_section ->
how_it_works_section -> turn_section -> close), not `textutil.walk_page`,
because a model's own JSON key order isn't guaranteed to match the
cartridge's structural order and reading order is exactly what this
measures. Flags: tenant/product brand name (tenant.yaml's name +
tenant_short_name, plus every short_name in claims/products.json, read
directly -- never a live Shopify fetch, so the gate stays deterministic),
a `$` price token, and any of the cartridge's own allowed CTA texts.
Exempt by construction: the "Advertisement" label, byline block, and
disclosure paragraph are renderer-injected and never reach page.json.

Wired into `harness/repair.py`: `check_page_gates` runs it as a hard gate
only when `warmup_mode: enforce`; `find_soft_check_warnings` writes
"Warm-up window: brand/price/CTA appears at word N" lines only when
`warmup_mode: warn`. `review_md.write_review_md` reports the first
brand/price/CTA word index for the article page unconditionally, regardless
of mode. One sentence added to `cartridges/article/cartridge.md`'s Rules,
the window number injected via `{{ tenant.cartridges.article.
warmup_window_words }}` (tenant-neutral wording, same placeholder mechanism
already used for `{{ tenant.reviews.platform_name }}`).

Peak Saunas is set to `warmup_mode: enforce` -- confirmed via `python -m
evals.fake_run tenants/peak-saunas/fixtures/hidden-costs-v2.transcript.txt
--tenant peak-saunas` (byte-identical to `evals/baseline/`) that the
current baseline article page already passes at a 600-word window: the
brand first appears at word 1063, and no price/CTA text appears in the
window at all. `tenants/_template/tenant.yaml` still defaults new tenants
to `warmup_mode: warn` -- a tenant only earns "enforce" once its own
baseline is checked the same way.

**Part 2 -- tenant design references.** New `harness/design_skills/
design_md.py`: `tenant.yaml`'s `design_reference: [slugs]` (template: `[]`;
Peak Saunas: `[tesla, apple]`) each resolve to `design-md/<slug>/DESIGN.md`.
The two vendored files are NOT uniform (apple has real YAML frontmatter
with numeric spacing/typography tokens; tesla has none, only prose and
markdown tables) -- every derivation falls back from a frontmatter read to
a text-section scan, and degrades to `None`/a qualitative label rather than
guessing a number. Derives exactly six structural facts (hero style,
whitespace scale, max content width band, heading-to-body ratio band, image
aspect preference, section rhythm) -- fonts, colors, and the referenced
brand's own name are never read into a derived value. `design_reference_
rules(tenant)` encodes each as a rules.json-shaped entry (action always
"adapt"); only hero style carries a `check` this cycle (soft only --
`gate.find_design_reference_warnings` flags a landing-style page whose
hero style is photo-first but has no `hero_image.asset_id`). Content width /
ratio / whitespace / aspect / rhythm are informational only this cycle (no
render.py/CSS change -- that's the images branch's scope), surfaced through
`harness design-skills list --tenant <t>`'s new "design_reference" group
and through `write.py`'s writer-prompt guidance lines (landing-style
cartridges only; article declines the landing skeleton entirely, same as
the existing design-skills pack).

**Tests**: 30 new (`tests/test_warmup.py` x18, `tests/test_soft_checks.py`
+2, `tests/test_design_skills.py` +10) -- 925 total (baseline 895), all
green; `ruff check` clean; `tests/test_fake_run.py` (baseline byte parity)
unaffected, confirming the fake client ignores the new prompt content as
expected.

**Verification**: see the cycle 30 commit/PR for the server real-run
result (`harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov
--tenant peak-saunas`), REVIEW.md's warm-up numbers, and the design_reference
guidance lines that reached the writer prompt.

## Cycle 30 merge note (operator)
- Merged with Peak warmup_mode set back to warn: the real run under enforce STOPped after three repairs (brand at word 465, then 583). Enforce again after five consecutive clean real runs report first-brand-mention beyond the window in REVIEW.md.
- Follow-up for cycle 32: repair loop should normalize warm-up failures to a stable key ("brand inside warm-up window") so the REVISION REQUIRED memory recognizes repeats, and the repair prompt should state the exact word budget remaining.
## Cycle 31 — image selection, markup, and delivery audit + fixes (2026-09-14)

Branch `cycle31/images`, not merged. `docs/IMAGES-AUDIT-2026-09-14.md` audits the Friday
23-page set first, before any code change; `docs/IMAGES.md` is the resulting reference.

- **Selection** (`harness/ground.py`): `pick_hero`/`pick_section_images`/`build_slot_plan`
  (a real photo preferred over the plain Shopify product-on-white shot for hero, never a
  logo/ai_render); `enforce_slot_plan` is the render-time backstop that fixes a bad hero pick,
  a duplicate asset id within one page, or a disallowed `ai_render`, without ever touching
  copy. `record_used_asset_ids`/`all_used_asset_ids` (`<run_dir>/.image-selection.json`) give
  the three cartridges of one run cross-cartridge dedupe.
- **Markup**: `render.render_image_slot()` is the one function every cartridge template and
  every image-bearing block now calls -- width/height, loading (lazy except hero: eager +
  fetchpriority high), decoding="async", a fixed aspect box (`adv-img--4x3`/`--1x1`, picked by
  `detect_near_white_border`), optional `<figcaption>`, and (when Pillow supports WebP) a
  `<picture>` wrapping a WebP `<source>` + JPEG `<img>` fallback.
- **Delivery**: `generate_image_variants` writes 480/800/1200/1600px JPEG+WebP srcset
  variants per asset (never upscaled); `harness/review.py` gained a 12 MB size-cap warning;
  `harness/shopify.py`'s `build_asset_manifest` now lists every srcset/picture variant with
  its own CDN filename, not just the single inlined fallback.
- **Checks** (`harness/pagechecks.py`): `find_image_markup_violations`,
  `find_duplicate_asset_violations`, `find_hero_requirement_violations` -- all new; only one
  image check (`find_image_allowlist_violations`) existed before this cycle.
- Eval baseline deliberately re-captured (`evals/baseline/{founder-warranty-demo,
  hidden-costs-v2-transcript}/{longform,product-page}.page.json`): the committed fake-writer
  fixtures had a real duplicate baked in (same hero photo across longform/product-page, and
  twice within one longform page) that `enforce_slot_plan` now correctly fixes.
- 938 tests passed (895 baseline + 43 new/updated for this cycle), `ruff check` clean.
- Verified on the server: `harness run` on both `product-features-v2-v2.mov` (Mini, listicle
  Drive pack) and `hidden-costs-v2-v2.mov` (Fuji) fixtures in a worktree of this branch, plus
  `harness review` on both -- see the cycle 31 verification note for per-page evidence.

## Cycle 32: simplicity gate + warm-up repair-loop key fix (2026-09-14)

Branch `cycle32/simplicity`, from `master` (head includes cycle 30).
Concurrent with `cycle31/images` (harness/render.py, harness/structure.css,
harness/ground.py, cartridges/*/template.html, harness/shopify.py, and the
image checks in harness/pagechecks.py) -- none of those files touched here.

**Part A -- simplicity gate (docs/RESEARCH-HORMOZI-LANDING.md section 3, new
module `harness/simplicity.py`).** Four page.json-level checks:

1. **Links above the fold.** Distinct `url`/`cta_url` targets in the page's
   above-the-fold region, defined per cartridge (product-page/longform:
   `hero` + the top-level `cta_url` reused there; article: `headline` +
   `dek` + `open[0]`; listicle: `headline` + `dek` + `proof_row`). At most
   1. The disclosure paragraph and byline "Full bio" link are exempt by
   construction, not by special-case code -- both are renderer-injected
   (harness/render.py) and never appear in page.json at all, the same fact
   claims.py's own cycle-30 warm-up module docstring already notes about
   the same two blocks.
2. **Headline word band per cartridge.** New `headline_word_band` key in
   each cartridge's own schema.json: article/listicle `[8, 14]` (already
   the stated prose rule in each cartridge.md), longform `[6, 12]`,
   product-page `[4, 10]`. product-page has no literal `headline` field --
   `hero.promise` (the ad-angle one-liner) is measured instead, documented
   inline in its schema.json.
3. **One offer element per page.** A second, differently-worded CTA text or
   financing sentence anywhere on the page fails; the *same* text/sentence
   repeating verbatim in more than one render slot (hero + sticky bar +
   final block, same convention the CTA-text allowlist already uses) is
   fine. A second `cta_url`/offer card was already an unconditional hard
   gate (`claims.find_second_cta_violation`, run inside `gate_page_json`
   regardless of mode) -- reused for the Simplicity report, not
   re-implemented.
4. **Value-equation checklist (product-page proof_bullets, soft only, every
   mode).** `cartridges/product-page/cartridge.md` gets the tenant-neutral
   writer instruction (bullet order: outcome the buyer wants, proof it's
   likely, time to first benefit/install effort, verified claim ids only,
   never price/warranty/shipping/returns). `find_value_equation_warnings`
   checks the 3 bullets carry 3 distinct claim_ids and none is transactional
   (claims/verified.json's own `category` field for price; the claim id's
   own naming for warranty/shipping/returns, since the claims file has no
   dedicated category for either).

New per-tenant `simplicity_mode: warn|enforce` in tenant.yaml (template and
Peak Saunas both `warn`) -- under `enforce`, items 1-3 become hard gates
through the writer repair loop (`harness/repair.py`'s `check_page_gates`,
wired with one import and one conditional call in the same place the
warm-up gate runs); item 4 always stays soft. New "## Simplicity" section
in REVIEW.md (`harness/review_md.py`) reports every check's PASS/WARN result
per page unconditionally, regardless of mode -- same pattern the existing
"Warm-up window" section uses.

**Part B -- warm-up repair-loop memory (harness/claims.py +
harness/repair.py).** Per the cycle 30 merge note above:
`find_warmup_violations` now gives each hit a stable `"key"`
(`"warmup:brand"`/`"warmup:price"`/`"warmup:cta"`), separate from the
varying detail in `"issue"`, which now states the exact word budget
remaining ("brand at word 583; window 600; move the first brand mention
past word 600 -- you have 17 words to cut before it or 17+ words to add of
brand-free copy before it") instead of just the window and position.
`write_and_gate_page`'s failures-seen memory is now a dict keyed on a
failure's own `"key"` when it has one (else `(path, issue)` as before)
instead of a dedup set + append-only list, so a repeat under the same key
replaces the stored detail instead of being dropped or duplicated -- the
REVISION REQUIRED block always states the latest detail for a still-open
violation.

**Tests.** `tests/test_simplicity.py` (39 tests): each simplicity check
positive/negative on synthetic page.json per cartridge, `headline_word_band`
read from the real schema.json files, the real ARTICLE_PAGE/
PRODUCT_PAGE_PAGE/LONGFORM_PAGE fixtures (tests/test_render.py) passing the
gate cleanly, warn vs enforce wired into `check_page_gates`, and the
REVIEW.md Simplicity section. `tests/test_warmup.py` gains one test: two
consecutive warm-up failures at different word indexes produce exactly one
memory entry, carrying the latest detail and budget, not the first; its
`_gate_problems` helper switches from a substring match on the old
"warm-up window" wording to the new stable key. No change to the fake-run
baseline (`tests/test_fake_run.py`'s byte-identical page.json comparison --
the fake client ignores prompts, so nothing about what the writer is told
changes what it returns). Full suite: 965 passed (925 baseline + 40 new),
`ruff check` clean.

### Verify (real `adv run`/`harness run`, server, real Claude calls, worktree
`/home/deploy/advertorial-c32` of `cycle32/simplicity`)

- **Run 1** (`harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov
  --tenant peak-saunas`, `simplicity_mode: warn`, out dir
  `20260914-163455-hidden-costs-v2-dgj6`): **PASS**. Simplicity section:
  longform and article all PASS; product-page's headline band WARNs
  (`hero.promise` is 11 words against a 4-10 band -- advisory only, no
  repair triggered under warn). Warm-up window (article): first brand
  mention word 481 (inside the 600-word window -- advisory only under
  Peak's current `warmup_mode: warn`), first price word 751, no CTA
  mention.
- **Enforce trial** (simplicity_mode flipped to `enforce` in the worktree
  only, not committed; `harness run tenants/peak-saunas/fixtures/
  still-levelup-4x5.png --tenant peak-saunas`, out dir
  `20260914-163905-still-levelup-4x5-knxh`): **PASS**. Simplicity section:
  every check on every page PASS (product-page's headline band passed this
  time on the model's own output). Article took 2 repairs (3 attempts) --
  both repairs were pre-existing gates (an unsourced "medical"/digit
  trigger-word claim_id miss, then a word-count overage), confirmed against
  the run log; the simplicity gate itself caused zero repairs on this run.
  Worktree removed after verification (`git worktree remove --force`).

## Cycle 33 (merged 2026-09-14)
- Six Cursor swarm packages merged after docs/REVIEW-CYCLE33.md: fetch-url-safety, run-log-context-manager, warmup-window (root cause: cartridge and voice block invited a body brand mention; real run now first brand at word 1267 vs 600 window), shopify-body-rename (harness/page_body.py; `harness shopify-body` alias kept), ci-pipeline, packaging-metadata. Review FIX items applied in one follow-up commit. Synthesis: docs/SWARM-2026-09-14.md (~30 agents, not 300).
- Backlog from the swarm: untrusted-transcript framing in prompts (prompt-injection posture), cmd_review move + `harness --version`, docs accuracy sweep, RunLog call sites to `with`.

## Cycle 34 fixes (branch `cycle34/fixes`, not yet merged to master)
- Base: `master` @ `0f0b5b3` (the three clean-MERGE cycle 34 packages --
  repair-warranty-scope, runlog-close-protocol, publisher-store-guard --
  and docs/REVIEW-CYCLE34.md already on master). Merged the three
  review-marked-FIX packages on top (`github/cycle34/notify-webhook-https`,
  `github/cycle34/revise-spend-ledger`, `github/cycle34/serve-auth-hardening`),
  no conflicts, then applied every FIX finding from docs/REVIEW-CYCLE34.md
  as its own commit:
  - **notify-webhook-https**: the https check was scheme-only -- any
    `https://` URL was accepted. Pinned the host: a Slack webhook must be
    `https://hooks.slack.com/...`; anything else is refused before any send,
    still behind the tenant off-switch. New test for a non-Slack https host;
    the "valid" test now uses a real `hooks.slack.com` URL.
  - **revise-spend-ledger**: (a) removed the unused `page` local in
    `tests/test_revise.py` (ruff F841); (b) `cmd_revise` now catches
    `BudgetExceeded` and prints the same "budget exceeded: daily spend cap
    reached: ..." message with exit code 3 as `harness run`, no traceback
    (previously only `ReviseError` was caught); (c) the revise ledger's
    run_id now reuses the RunLog's own filename stem
    (`<run_id>-revise-<page>-v<version>`) instead of a separate
    `__revise__<page>__v<version>` string, so `budget._run_has_final_state`
    can actually find a crashed revise reservation's log and
    `harness spend reconcile` can sweep it. New tests:
    `test_cmd_revise_exits_3_on_cap_refusal_no_traceback` and
    `test_reconcile_drops_stale_revise_reservation_whose_run_is_final`.
  - **serve-auth-hardening**: the cycle 34 clickjacking fix sent
    `X-Frame-Options: DENY` / CSP `frame-ancestors 'none'` on every
    response, including `/run/<id>/review/<page>` (`page_review`) -- the
    same route `run_detail`'s own `<iframe>` embeds for the reviewer's live
    preview, so DENY broke the app's own preview. `page_review` alone now
    sends `SAMEORIGIN` / `frame-ancestors 'self'`; every other route keeps
    DENY / `'none'`. Also corrected `_authenticate`'s docstring: the CF
    Access check is a non-empty-header presence check, not cryptographic
    JWT verification -- said so plainly and pointed at backlog item #1 in
    docs/SWARM-2026-09-14-cycle34.md for real JWKS verification. New tests:
    `test_run_detail_also_denies_framing`,
    `test_page_review_allows_same_origin_framing`.
- Verify: full suite 1048 passed, `ruff check .` clean, both
  `evals.fake_run --baseline-dir` fixtures (`hidden-costs-v2-transcript`,
  `founder-warranty-demo`) byte-identical to `evals/baseline/` (manifest.json
  excluded). Live check on a spare port against a worktree of this branch:
  index returns `X-Frame-Options: DENY`; an existing run's page-review route
  returns `SAMEORIGIN` / CSP `frame-ancestors 'self'`.
- Branch pushed to `origin` (the prod mirror) only -- not merged to master,
  not pushed to `github`.

## Cycle 35b — review images (2026-09-14)
- Bug: review html kept <picture><source srcset=assets/...> and <img srcset>; browsers pick those candidates and show a broken image when they 404 (no fallback to the inlined src). Fix in harness/review.py: strip <source>, srcset, sizes before inlining. Production pages unchanged. Rebuilt review html for today's runs.

## Cycle 35a — eval hygiene (2026-09-14)
- Problem: `tenants/peak-saunas/evals/scores.jsonl` and `approvals.jsonl` had grown to 89 lines each, almost all written by the test suite. Root cause: `tests/test_serve.py`'s `harness serve` approve/reject/changes POST-action tests (`test_approve_action_writes_scores_and_state` and its siblings) ran against the real `TENANT` from `tests/support.py` (the actual peak-saunas Tenant) instead of a tmp_path/FakeTenant one, so every suite run appended real lines to the real files through `evals.record_score` / `runstate.approve` / `runstate.reject`. Separately, `~779` fake-client run dirs (and matching run logs) had accumulated under `tenants/peak-saunas/out/` and `runs/` from this and other tests -- archived to `out/_archive-test-runs/` and `runs/_archive-test-runs/` on the server (not deleted, not committed here -- tenant runtime data is gitignored).
- Fix (tests only, no `harness/` changes): `tests/test_serve.py` gets a new `isolated_tenant_paths` fixture that monkeypatches `Tenant.out_dir` / `Tenant.runs_dir` / `Tenant.evals_path` (plain `self.root / "..."` properties) at the class level, redirecting every write the app makes into `tmp_path` for the duration of the test while claims/brand/fixtures data still comes from the real tenant (needed for realistic gate/claims behavior). Wired into the `run_dir` and `app_client` fixtures so every test in the file inherits it.
- Guard: `tests/conftest.py` adds a session-scoped autouse fixture, `_real_tenant_evals_unchanged`, that hashes `tenants/peak-saunas/evals/scores.jsonl` and `approvals.jsonl` before the first test and asserts the same hashes in its own teardown, which runs only after every other test in the session has finished regardless of collection order. `tests/test_suite_guards.py` adds `test_real_tenant_evals_files_are_unchanged`, which re-checks the fixture's baseline against disk at the point it runs, as a second, ordering-independent tripwire.
- Verify: `pytest tests/` -- 1049 passed, 1 pre-existing failure unrelated to this change (`tests/test_images_cycle31.py::test_inline_assets_as_data_uris_inlines_only_the_plain_src_not_srcset`; confirmed failing identically on master at 620f481 with these changes stashed out -- a stale cycle-31 assertion left behind by cycle 35b's `harness/review.py` srcset-stripping fix, in a file this cycle does not touch). `ruff check .` clean. `tenants/peak-saunas/evals/` held only `.gitkeep` after the full local suite run (no scores.jsonl/approvals.jsonl created).
- Branch pushed to `origin` (the prod mirror) only.

## Cycle 35a merge note (operator)
- Merged cycle35/test-isolation. Evals files verified unchanged across a full suite run (0 lines before and after). The suite still creates ~25 dry-run run dirs under the real tenant out/ per run (hidden by the review site, archived by hand today). Follow-up: apply isolated_tenant_paths to every test that creates a run so the real tenant tree stays untouched; then add a guard asserting no new dirs under tenants/*/out after the suite.

## Cycle 35c — brand import: nested Drive folder tree (2026-09-14)
- Bug: `harness brand import --drive-folder ...` 500'd against a real, deeply-nested link-public Drive folder. Root cause: `enumerate_source`'s folder detection was name-based (`looks_like_folder`: "no extension in the name") and its recursion depth cap was 2; a real .ai/.eps design-source file with no bucket in `classify_file` already defeated the name heuristic once, and once the walk hit its depth cap it fell back to `entries.append(...)` for a folder it couldn't recurse into -- handing a Drive **folder id** to `download_drive_file`, which 500'd on the download endpoint.
- Fix (`harness/sources/drive.py`, `harness/brand_import.py`):
  - `drive.list_public_folder` now extracts a real per-entry folder marker from the listing HTML: a folder's aria-label ends in the literal word "folder" ("1 - Acme Logotype Shared folder"); a file's ends in its status word ("Acme_Brochure.pdf PDF Shared"). Verified against a real folder's HTML (root + several subfolders); a trimmed real sample lives at `tests/fixtures/drive-folder-listing-sample.html`. `_ARIA_TYPE_SUFFIXES` gained "Binary"/"Unknown"/"iWork Pages" -- real type words Drive uses for a `.DS_Store`, a `.otf`, and a `.pages` file respectively, needed so cleaned names actually match (`.DS_Store` skip-list, "note a .pages file" logic).
  - `enumerate_source`'s `_walk` now trusts that marker first, falls back to a two-step probe (no-extension guess, then "does the id actually list as a folder") only when the marker is absent, and -- the actual fix for the 500 -- **never** appends a folder id as an entry under any circumstance: a too-deep folder or a subfolder whose listing failed is skipped and logged, not treated as a file. Depth cap raised 2 -> 6; `.DS_Store`/`Thumbs.db` and zero-byte downloads are skipped; the walk logs an indented tree via the `log` param now threaded through from `import_brand_kit`.
  - `classify_file` is now folder-path-aware: `.ai/.eps/.pdf` under a Logotype/Icon-named folder classify as `"logo"` (`.ai/.eps` flagged in the report as source files, not usable directly -- never a `choose_logo` candidate even so); `choose_logo`'s preference order gained a `.pdf` tier (svg > png > pdf > jpg); a `.pdf` under a Colors-named folder classifies as the new `"palette_pdf"` kind, read via the same vision path as a brand guide (`extract_brand_guide_json` reused, since its schema already returns hex/name/role) but merged into tokens as its own step, separate from `choose_guide`'s "one comprehensive guide" pick. `import_brand_kit` also now groups logo candidates by variant (logotype/icon/combined, via `_logo_variant_for_folder`) for tenants whose source has that folder structure.
  - Fonts: any font filename containing "trial"/"demo"/"eval" (case-insensitive) is now flagged in `human_decisions` as not licensed for production and is excluded from `import_fonts` entirely -- never copied into `brand/fonts`, `--force` or not.
- Tests: `tests/test_brand_import.py` -- nested three-level walk with a fake fetch (`.DS_Store` skipped at two levels, folder ids never in the result), a depth-cap-skips-not-falls-back-to-file regression, `download_incoming` never calling the file downloader with a folder id, zero-byte downloads dropped, folder-aware `classify_file` parametrized over `.ai/.eps/.pdf/.otf` under Logotype/Icon/Colors/Fonts/Info-Sheet folders, trial-font refusal (flagged, never copied even with `--force`), and a Colors-folder `.pdf` palette extraction end-to-end against a fake vision response. New real-HTML fixture: `tests/fixtures/drive-folder-listing-sample.html`.
- Verify: full suite 1075 passed (1051 baseline + 24 new), `ruff check .` clean.
- Branch `cycle35c/brand-import-nesting` pushed to `origin` (the prod mirror) only -- not merged to master.
- Dry-run against Peak Saunas' real rebrand-toolbox Drive folder, and the resulting report, are in `tenants/peak-saunas/docs/BRAND-IMPORT-REBRAND-2026-09-14.md` (same branch) -- nothing under `tenants/peak-saunas/brand/` or `tenant.yaml` was written; whether to apply the rebrand is still Caleb's call.

## Cycle 35d — brand import: palette merge + PDF fail-soft (2026-09-14)
- Bug 1: `extract_brand_guide_json`'s color merge (`harness/brand_import.py`) keyed each color on the model's own free-text `role` string (`tokens["colors"].setdefault(role, ...)`), so a second color sharing a role silently clobbered the first -- three real, distinctly-named colors were lost this way in the Peak Saunas dry run (`tenants/peak-saunas/docs/BRAND-IMPORT-REBRAND-2026-09-14.md`, "Palette -- exactly as extracted"). Same bug shape hit the logo-dominant-color and palette-file writes too (each keyed its own per-source counter, so a second palette *file* would have collided with the first).
- Fix: every color found, from any source (logo dominant colors, a palette file, a guide's vision read, a Colors-folder PDF's vision read), now funnels into one `palette` list via `_add_palette_color`, deduplicated by normalized (lowercase) hex ONLY -- `role` is carried as advisory data on each entry (`hex`, `name`, `role`, `source`, `rank`), never a dict key, so it can repeat freely. `tokens.json`'s `brand_import` key now carries this full `palette` list alongside `colors`, which holds only the four *derived picks* (`primary`/`accent`/`background`/`text`): an explicit label from the source document wins first, then a documented rule (`DERIVATION_RULE_TEXT`, echoed into `BRAND-IMPORT.md`'s new "How the primary/accent/background/text picks were made" section) -- background = lightest neutral color (HSV saturation <= 0.12) or white; text = highest-WCAG-contrast color against the background, preferring one darker than it; accent = most saturated warm-hued (red/orange/yellow/pink) color, or most saturated overall if none is warm; primary = the same pick as accent. A color already claimed by another role (accent/text/primary/brand) is excluded from the background heuristic, so a single saturated brand color can never become the page background too (which would zero out its own contrast).
- Bug 2: a large or mislabeled PDF (a real 263.8 MB info-sheet PDF, misclassified as a brand guide) rasterizes to a page image whose base64 encoding exceeds Anthropic's 10 MB image limit -- the real dry run's vision call 400'd before reaching the model, with no size guard anywhere in `render_guide_pages`'s callers.
- Fix: a new `PDF_SIZE_CAP_BYTES` (25 MB) is checked before any guide or Colors-folder PDF is rendered; an oversize file is skipped with a report line (`"<name>: skipped: <N.N> MB, over cap (25.0 MB) -- ..."`) instead of attempting the read, and is excluded from guide/palette-doc candidate selection entirely (not just skipped after being chosen) so a smaller, legitimate candidate is picked instead. `MAX_GUIDE_PAGES` (per-document page cap) is unchanged.
- Bug 3: `classify_file`'s guide bucket was not folder-scoped -- any unclaimed `.pdf` defaulted to `"guide"`, so 32 files under a real "Logo layout for Merch" folder misclassified (`BRAND-IMPORT-REBRAND-2026-09-14.md`, "How the run actually went" #2).
- Fix: a `.pdf` under a folder whose name contains "merch" or "layout" now classifies as the new `"merch_layout"` kind (checked before the guide rule, PDF-only -- a preview image in the same folder keeps its normal `"photo"` classification). A `.pdf` found INSIDE a walked Drive folder now only classifies `"guide"` when its own filename says so (unchanged) or its folder name contains guide/brand/info/deck/presentation; a `.pdf` with NO folder context at all (a top-level Drive file, or a flat `--local` import) keeps the old inclusive default, since there's no folder signal to gate on and a flat import dropping in an oddly-named guide PDF is a normal, fully-supported case. `import_brand_kit` also now vision-reads up to `MAX_GUIDE_DOCS` (2) guide-classified PDFs per import instead of just one -- `rank_guide_candidates` orders candidates guide-named-first then largest-by-bytes (the same preference `choose_guide`, now a thin wrapper over it, always used); the rest are listed in `human_decisions`, not read. `BrandImportResult.raw_model_json` is now a list of `{"file", "json"}` entries (was a single dict) to hold more than one guide's raw response; `as_markdown` renders one subsection per guide read.
- Tests (`tests/test_brand_import.py`, 19 new): merch/layout folder classification (parametrized, plus the non-PDF-unaffected and no-folder-context cases); `_add_palette_color` keeps every distinct hex even with a repeated role, and dedupes an exact repeated hex (first-seen wins); a guide response with three same-role colors all survive into `brand_import_tokens["palette"]`; `_pick_background`/`_pick_text`/`_pick_accent` unit tests (explicit label wins, neutral/contrast/warm-saturation fallback, a claimed-role color never doubles as background); an oversize guide PDF and an oversize Colors-folder PDF are both skipped with a report line and never handed to `render_guide_pages`; three guide-classified PDFs only vision-read the top two (by the `rank_guide_candidates` order), the third listed as not-read. `test_palette_pdf_from_a_colors_folder_extracts_hex_via_vision` updated for the now-lowercase-normalized hex keys and the `palette` list.
- Verify: full suite 1094 passed (1075 baseline + 19 new), `ruff check .` clean. No server runs, no real model calls -- all vision-call tests use `FakeClient`.
- Branch `cycle35d/palette-merge` pushed to `origin` (the prod mirror) only -- not merged to master.

## Cycle 36 — image library (2026-09-16)
- Feature: a human reviewer can now look at every image a product could ever place on a page, write real alt text, and exclude an image entirely, and have that flow back into grounding so the writer never sees an excluded image and gets the reviewer's alt text instead of the default. `harness/serve.py` gains `/images` (index with reviewed/excluded counts per active product), `/images/<product slug>` (paginated grid, 48/page, source filter), `POST /images/<product slug>/save`, and `/images/thumb/<asset id>` (Pillow thumbnail, cached, inline SVG placeholder on any fetch/decode failure -- never a 500).
- `harness/asset_review.py` (new): loads/applies/saves `tenants/<t>/brand/asset-review.json`. `ground.py`'s `facts_for()` applies it right after building the shopify+drive+listicle pool, before `pick_hero`/`build_slot_plan`; a Shopify override also pins the url it was written against, so a re-ordered product image list doesn't mislabel a different photo. `ground.full_asset_pool()` (new) is the uncapped pool the review page shows -- unlike `facts_for()`'s own capped `assets`, it ignores `DRIVE_ASSET_MAX`/`LISTICLE_PACK_MAX`/`allow_ai_renders` and does not apply asset-review.json itself, so an already-excluded asset still shows (greyed out) to be un-excluded.
- Tests: `tests/test_asset_review.py` (12), `tests/test_ground.py` (+4, `full_asset_pool`), `tests/test_serve_images.py` (11) -- auth, pagination/filtering, save writes/ignores, 404s, thumb placeholder on download failure.
- Verify: full suite 1121 passed (1094 baseline + 27 new), `ruff check .` clean, `pip install -e .` clean. Manual smoke test against a second `harness serve` instance on port 4871 (not the live 4870 service) -- see commit message for exact curl outputs.
- Branch `cycle36/image-library`, not merged, not pushed.

## Cycle 39 — Shopify asset upload via GraphQL staged uploads (2026-09-18)
- Bug: `upload_assets` POSTed to `/admin/api/2024-10/files.json` (base64 `file.attachment`) -- that REST endpoint does not exist; Shopify answers 406 and every real publish failed before any page was created.
- Fix (`harness/publishers/shopify.py`): `upload_assets` now runs Shopify's GraphQL staged-upload flow per asset: `stagedUploadsCreate` -> hand-built `multipart/form-data` POST of the raw bytes straight to the signed target (no Admin API host, no token, `file` field last -- GCS requires it) -> `fileCreate` -> poll `node(id: ...)` until `fileStatus: READY` with a non-empty `image.url` (1s interval, 30 polls, `PublishFailed` on `FAILED` or timeout). Mime type is derived from `cdn_filename`'s extension (`.webp` -> `image/webp`, else `mimetypes.guess_type`, default `image/jpeg`) since the manifest carries none. New `_graphql`/`_raw_post` helpers reuse the existing `_transport`; a second injectable, `upload_transport` (defaults to the same urllib call), carries the one non-Admin-API POST. `upload_assets`'s signature/return type are unchanged, so `cli.cmd_publish` needed no edits.
- Tests: `tests/test_publishers.py`'s `FakeTransport` now queues responses per (method, URL suffix) so repeated GraphQL calls to the same `graphql.json` URL can be scripted per-call. New tests cover the happy path (staged target, multipart field order, poll PROCESSING->READY), `userErrors` from both mutations, a `FAILED` poll, a 30-poll timeout (injected sleep, no real wait), a non-2xx staged upload, and mime-type derivation. `tests/test_cli_approve_publish.py`'s Shopify fixtures updated to the same flow.
- Verify: full suite 1139 passed (1133 baseline + 6 net new), `ruff check .` clean.
- Branch `cycle39/shopify-staged-upload`, not merged, not pushed.
## Cycle 40 — listicle header chrome, header measure, and publish --update (2026-09-18)
- Bug 1: `harness/page_body.py`'s `strip_document_chrome` stripped every `<header>`/`<footer>`/`<nav>` TAG unconditionally so the storefront theme's own chrome wins -- but cartridges use these as their own styled elements too (`cartridges/listicle/template.html`'s `<header class="lst-header">`, `harness/templates/base.html`'s `<footer class="adv-footer">`, the byline/disclosure/Sources band every cartridge renders through `base.html`), so the live listicle page lost `lst-header`'s width/background/padding band entirely -- only `.lst-header-inner` survived. Fix: a tag now converts to a `<div>` with its attributes carried over verbatim when it carries a `class` (a cartridge element), and is still unwrapped (removed, content left in place) only when it's bare (real document chrome). A small stack (`_CHROME_TAG_RE` opener/closer pairs, keyed on whether the opener had a `class`) tracks this across nesting so a closing tag becomes `</div>` or is dropped to match. `article`/`longform`/`product-page`'s `<header>` tags are all bare (unaffected); `comparison`'s `<header class="adv-comparison-header">` gets the same div conversion as listicle's, previously losing its own band the same way.
- Bug 2: `.adv-listicle .lst-header-inner{max-width:42rem}` capped the headline block to 672px while the reason sections below it fill their full container width (no matching cap of their own) -- the header visibly misaligned with the body. Fix: `max-width:none` (keeps `margin:0 auto`, harmless now); no listicle baseline byte-compare test exists to re-capture (`evals/baseline/` only covers article/product-page/longform `page.json`, not listicle markup/CSS).
- Feature: `harness publish --update`. Re-running `harness publish` for an already-published page created a second Shopify page (`<handle>-1`) instead of updating the first. `ShopifyPublisher.update_page(page_id, page, *, unpublished=True)` (`harness/publishers/shopify.py`) PUTs `pages/<id>.json` instead of POSTing `pages.json`, same return shape as `publish` (`{"id", "url", "admin_url", "handle"}` -- `publish` itself was missing `handle` from its return value despite already reading `created.get("handle")` for `url`; now included). `runstate.mark_published` gained optional `page_id`/`handle`/`url` kwargs, stored as a structured `state.json["published_pages"][<page>]` record (new `runstate.published_page_record`) alongside the existing free-text history note -- `cli.cmd_publish` now passes these on every real publish, not only under `--update`. `harness publish <run-dir> --page <cartridge> --update` looks up that record; refuses with a clear message and exit 1 if this run has no stored page id for the page yet (never published, or published through the `export` adapter, which has no page id). `--handle` together with `--update` is ignored, with a printed warning -- handle changes are out of scope. `--redirect-from` is unaffected.
- Tests: `tests/test_shopify_body.py` (+5: bare header/footer unwrapped, classed header/footer become divs with identical attributes, nested classed-inside-bare). `tests/test_publishers.py` (+5: `publish()` return includes `handle`; `update_page` PUTs the right payload, sets `published` from `unpublished`, never sends a `handle` field, raises on non-2xx). `tests/test_runstate.py` (+3: no structured record without a page id, a structured record with one, `published_page_record` is `None` for an unpublished page). `tests/test_cli_approve_publish.py` (+4: `--update` refuses with no stored id and makes no Shopify call; `--update` PUTs the stored id instead of POSTing a new page; `--update --handle` warns and never sends `handle`; a normal publish records the structured `page_id`/`handle`/`url`).
- Verify: full suite 1156 passed (1139 baseline + 17 new), `ruff check .` clean.
- Branch `cycle40/listicle-header-and-update`, not merged, not pushed.

## Cycle 41 — listicle v2 (2026-09-18)
- Rebuild of the listicle cartridge (v0.1.0 -> v0.2.0) against `tenants/peak-saunas/docs/REFERENCE-LANDERS-2026-09-18.md`: the DTC listicle structure (Grüns/Jones Road/Moon Pod/Earthling/Resilia) with Sun Home's evidence-labelled honesty and HubSpot/ClickUp's above-fold stack and CTA/trust-line pairing.
- `harness/listicle.py` (new): the five-style system (`reasons`/`mistakes`/`questions`/`myths`/`tested`) — headline formula and item pattern per style, `resolve_style` (the `harness run --style` flag, else a deterministic pick from the run seed so a batch rotates through every style a tenant allows via `cartridges.listicle.styles`), `writer_style_lines` for the writer prompt, `render_context` for the renderer-owned sections, and `find_listicle_violations` (the structural gate, each problem carrying a stable repair key).
- Page structure: header (Advertisement label, H1, dek, hero slot, primary CTA, trust line, byline) -> 5-7 numbered items (H2, 60-150 word body, one image, a claim-backed or attributed proof line, micro-CTA after items 2 and 4) -> pull-quote band after item 3 -> "who this is for / who it is not for" -> model picker -> 5-7 question FAQ -> closing block (3-bullet recap, CTA, warranty sentence, financing sentence, HSA/FSA line) -> disclosure/Sources -> sticky bottom CTA bar. Word range 600-1,100 -> 900-1,400.
- Renderer-owned, never writer-written: the trust line, pull-quote band, model picker, HSA/FSA line and the sticky bar's rating line all come from `facts_pack` alone, so "omitted when unverified" is structural — there is no page.json field to invent one in, and `listicle:renderer_owned:<key>` rejects a page that adds one. `harness/ground.py` gains `_model_options` (+`include_listicle`/`live_price_claims` on `facts_for`), building up to three active-product rows — the run's product first, then the closest in price — each with a claim-backed price and capacity/placement fit, with the backing claims joined into `verified_claims` so the Sources list can cite them (same shape as `_comparison_targets`).
- CTA policy: one `cta_text`/`cta_url` now renders in five places (header, after items 2 and 4, closing, sticky bar). `claims.find_second_cta_violation` and the CTA allowlist are unchanged; `harness/simplicity.py`'s listicle above-the-fold region becomes `hero` + the top-level `cta_url` (the hero CTA is the one allowed above-fold link) and its docstring records why the sticky bar is exempt by construction.
- `harness/ground.py`'s `hero_container` and `pagechecks.find_hero_requirement_violations` now cover listicle's own `page.hero` slot (v0.1 had none and used the first item's image); `claims._ATTRIBUTABLE_PATH_RE` gains `reasons[i].proof` so an item's proof line may be an attributed customer statement.
- Design: the cartridge's scoped `<style>` block rewritten to the reference measurements — ~700px measure, 17-18px/1.6 body, 36-44px H1, 24-28px H2, 48-64px section rhythm, alternating soft bands, full-width 52px mobile CTA at a 10px radius, 64px sticky bar with `env(safe-area-inset-bottom)`, aspect-boxed lazy images through `render_image_slot`. No colour or font is hardcoded any more: every token resolves `--ps-*` (brand import) first, `--adv-*` (structure.css) second. The sticky bar sits inside the cartridge wrapper, so `page_body.strip_document_chrome` and `review.inline_assets_as_data_uris` both carry it through.
- Offline: `evals/fake_run.py` gains a canned listicle page built per style and a `--style` flag, so all five render with no API key.
- Tests: `tests/test_listicle.py` rewritten (59), `tests/test_fake_run.py` (+6), `tests/test_shopify_body.py`/`tests/test_simplicity.py`/`tests/test_images_cycle31.py` updated to v0.2's shape.
- Deterministic repair (from the verification runs): `listicle.fix_headline_number`, applied by `repair.apply_deterministic_fixes` on a `listicle:headline_formula` failure -- a spelled-out count ("Six Mistakes ...") or a numeral that no longer matches the item count after another repair changed it. One token, re-checked against the style's own formula, never applied to `tested` (whose number is a duration). `listicle.writer_rules_lines` adds the four rules the gate knew and the prompt did not say plainly (an image per item plus the hero, all distinct; the 60-150 word floor including the last item; exactly one top-level `cta_url`; claim_ids on any number in an `audience_fit` line, a recap bullet or an FAQ answer).
- Verify: full suite 1214 passed (1156 baseline + 58 net new), `ruff check .` clean. `evals/baseline/` covers article/product-page/longform `page.json` only and is byte-unchanged, so no baseline was re-captured.
- Real runs, `tenants/peak-saunas/fixtures/hidden-costs-v2.mov`, one per style: `reasons` PASS (1,110 words), `mistakes` PASS (1,088), `questions` PASS (923) -- every section present except the pull-quote band and the HSA/FSA line, both correctly omitted (this tenant's facts_pack carries no review quotes, and `hsa-fsa-truemed` exists in `claims/verified.json` but is in neither `universal_claim_ids` nor `benefit_allowlist_ids`, so it never reaches a facts_pack). `myths` and `tested` STOP (exit 2) after their one retry, both on the shared claims gate rather than anything listicle-specific: uncited numbers in item bodies, a second `cta_url` the writer added inside the closing block, item bodies of 53-57 words, and the `rated` trigger word inside "outdoor-rated". Total spend for nine real runs: $1.56.
- Branch `cycle41/listicle-v2`, not merged, not pushed.
