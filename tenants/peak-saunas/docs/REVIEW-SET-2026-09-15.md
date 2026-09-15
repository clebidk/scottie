# Review Set — 2026-09-15

- Harness git commit: `08632b7`
- Tenant: peak-saunas
- Cartridges used: article, product-page, longform, listicle (per tenant.yaml `default_cartridge_pool: [article, product-page, longform]` plus opt-in `listicle`; `comparison` exists but is not runnable per instructions, `quiz` cartridge does not exist in `cartridges/`)

## Step 1: archive

Old run directories at `tenants/peak-saunas/out/` used **two different naming formats**:
- `YYYYMMDD-HHMM-slug` (no seconds, no random suffix) — runs from 2026-09-10/11
- `YYYYMMDD-HHMMSS-slug-suffix` — runs from 2026-09-11 onward

The originally-specified regex (`^[0-9]{8}-[0-9]{6}-`) only matches the second format and would have left ~55 old runs visible in the reviewer app. Broadened the match to `^[0-9]{8}-[0-9]{4}` to catch both formats while still excluding the three non-run folders (`FRIDAY-2026-09-11`, `REVIEW-SET-2026-09-14`, `_archive-test-runs`).

**216 run directories moved** into `tenants/peak-saunas/out/_archive-2026-09-15-pre40/`. Top level of `out/` now contains only the archive folder and the three named non-run folders (plus today's new runs).

## Step 3: the 10 inputs

| # | Input | Run id(s) | Pages | Attempts | Spend delta |
|---|-------|-----------|-------|----------|-------------|
| 1 | hidden-costs-v2.mov | 20260915-205423-hidden-costs-v2-evp6 | 4 | 1 | $0.3585 |
| 2 | product-features-v2.mov | 20260915-211257-product-features-v2-6rfn (article/product-page/longform) + 20260915-211810-product-features-v2-hnp6 (listicle) | 4 | 7 (3x 4-cartridge attempts failed at listicle claims-gate, then split: 2x 3-cartridge attempts + 2x listicle-only attempts) | $1.6125 |
| 3 | price-comparison-v2.mov | 20260915-212355-price-comparison-v2-jmzt | 4 | 2 (1st attempt failed at listicle claims-gate, same recurring bad asset_id) | combined with #4: $1.1661 |
| 4 | still-lessthan300-4x5.png | 20260915-213135-still-lessthan300-4x5-r3ee | 4 | 2 (1st attempt failed at listicle claims-gate) | combined with #3 above |
| 5 | still-levelup-4x5.png | 20260915-213437-still-levelup-4x5-mwwr | 4 | 1 | combined with #6, #7: $0.9208 |
| 6 | still-infraredglow-4x5.png | 20260915-213729-still-infraredglow-4x5-us3c | 4 | 1 | combined with #5, #7 above |
| 7 | still-unforgettable-4x5.png | 20260915-214210-still-unforgettable-4x5-mcne | 4 | 1 | combined with #5, #6 above |
| 8 | Drive 1vP1JTMDagf0o58CyJXdkLEnm6S4WXG0Q (LevelUpYourHealth v4) | 20260915-214531-view-ictv | 4 | 1 | combined with #9, #10: $0.9893 |
| 9 | Drive 1Prj7zVhdx_w-XcdXSdYM2v968LHTT4Ja (EnterYourInfraredGlow) | 20260915-215012-view-2qbg | 4 | 1 | combined with #8, #10 above |
| 10 | Drive 1lNCO5S_tvMOi18o0MEFRPiLx8I9KuUhL (UnforgettableGlow) | 20260915-215332-view-ovme | 4 | 1 | combined with #8, #9 above |

Spend was checked at coarser checkpoints for inputs 3-10 (grouped), so the table shows combined deltas where per-input figures were not captured individually. Total pages: **40**.

**Total spend for the batch: $5.0472** (cap $10.00, tenant total for the day since it started at $0.0000).

## Known code issue (not fixed, per instructions)

Across three different inputs (product-features-v2, price-comparison-v2, and again on retry), the **listicle cartridge** repeatedly hit claims-gate STOP (exit 2) referencing an asset id not in that run's manifest: `asset-listicle-1xsSIgyjTB_wCqz63DP4O3sMCZI7UFnrf` (and similar ids), plus occasional word-count and attribution-rule failures. This looks like a systemic bug in the listicle cartridge's asset selection / content-length logic rather than input-specific bad luck — the same bad asset id recurred across unrelated inputs. Worth a look by whoever owns `cartridges/listicle/`.

## EMF check

`grep -ril "emf" tenants/peak-saunas/out/2026*/ --include=*.html` matched many files, but nearly all hits were:
- the known/acceptable Fuji product URL handle (`near-zero-emf-full-spectrum-infrared-sauna...`), and
- false positives from `-review.html` artifact files (base64-encoded embedded font data where "emf" appears as random byte noise, and CSS `BlinkMacSystemFont` matching "emf" case-insensitively).

Restricting to the actual rendered `index.html` pages and excluding those two known patterns: **zero hits**. No EMF body-copy violations found.

## Failures

None outstanding — all 10 inputs eventually produced their 4 pages. The only failures were the listicle claims-gate STOPs documented above, all resolved via retry/seed or cartridge-splitting within the attempt budget.
