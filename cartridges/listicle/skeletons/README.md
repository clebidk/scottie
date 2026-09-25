# Listicle winner skeletons (v1)

Ten **page component maps** + seventeen **headline swipe templates** for one-shot generation: pick a page skeleton + a title formula, fill slots with this brand’s voice, verified claims, and assets. Do not invent padded advertorials.

## Page skeletons (10)

| # | id | Group | Target cartridge |
|---|----|-------|------------------|
| 1 | `hormozi-value-stack` | Hormozi | listicle |
| 2 | `hormozi-mistakes` | Hormozi | listicle |
| 3 | `native-article-comments` | Advertorial | listicle |
| 4 | `simplified-pdp` | PDP | **product-page** |
| 5 | `classic-n-reasons` | Classic (Peak live DNA) | listicle — **default** |
| 6 | `hidden-costs` | Classic | listicle |
| 7 | `switcher-reasons` | Classic | listicle |
| 8 | `myth-bust` | Classic | listicle |
| 9 | `buyers-checklist` | Classic | listicle |
| 10 | `day-in-the-life` | Classic | listicle |

## Headline skeletons (17)

From the “17 Best-Performing Headlines for Pre-Sell Listicles” swipe. See [`headlines/`](headlines/). Each file keeps the raw `[PLACEHOLDER]` template, pairs with page skeletons, and carries Peak-safe fill hints (no invented `100,000+`, no fake doctors, rewrite banned hype like “game-changer”).

If a swipe suggests N outside 5–7, keep the formula and clamp to the listicle cartridge’s item range.

## How to use

```bash
harness run <ad> --tenant peak-saunas --cartridges listicle \
  --skeleton classic-n-reasons --headline everyones-switching

harness run <ad> --tenant peak-saunas --cartridges listicle \
  --skeleton hormozi-mistakes --headline still-problem-after-trying

# product-page shaped winner (no headline swipe):
harness run <ad> --tenant peak-saunas --cartridges product-page --skeleton simplified-pdp
```

When `--skeleton` / `--headline` are omitted, the harness auto-picks from ad angle tags (fallback page: `classic-n-reasons`; fallback headline: `everyones-switching`).

## Adaptation rules

- Keep **component order**, **item density**, and **CTA placement pattern**.
- Fill the **headline template** from ad_brief + brand; prefer `peak_saunas.example_fills` when accurate.
- Swap **brand, tone, images (by image_role → asset_id), verified claims**.
- No page-level word floor on listicle; match the skeleton’s `density`.
- `peak_saunas` blocks are Peak fill hints only — other tenants ignore or replace them.

## Compliance note (`native-article-comments`)

Requires a visible Advertisement label, real tenant bylines only, and comments that are claim-free opinion or verified/attributed — never invented stats.
