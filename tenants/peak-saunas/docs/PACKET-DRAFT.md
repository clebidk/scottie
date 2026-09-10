# Coach packet draft

**Stamp: `BOT DRAFT · NOT SENT`** -- this is a draft object for Caleb to review, not a
message that has gone anywhere. Nothing in this file has been sent to anyone, published,
or acted on.

**Cycle 20 update:** every run's own `state.json`/`packet.json` (under
`out/<run-id>/`) are now the live version of this document's `stamp` and
"decisions still open" fields -- see `docs/PUBLISHING.md` for the full flow.
The stamp above is set per run with `harness packet <run-dir> --stamp
ship|redo|kill --by <email>`; a page only moves once `harness approve
<run-dir> --by <email> --pages <cartridge>` (a listed reviewer only --
Michael primary, Caleb backup, per `tenant.yaml`'s `reviewers`) has also run.
`harness publish <run-dir> --page <cartridge> [--live]` is the one code path
that can reach a Shopify page, and it refuses outright unless both of those
are true -- it still makes no live Shopify call today, since
`SHOPIFY_STORE`/`SHOPIFY_TOKEN` are not yet set in this tenant's `.env`.

```json
{
  "outcome": "Four sample advertorial page sets (article, product-page, longform, and two listicle runs) are generated, claims-gated, and ready for Caleb's review -- none are published to the Peak Shopify page.",
  "owner": "Peak Shopify page",
  "files": {
    "article_product_page_longform": {
      "run_dir": "out/20260909-2212-hidden-costs-v2",
      "paths": [
        "out/20260909-2212-hidden-costs-v2/article/index.html",
        "out/20260909-2212-hidden-costs-v2/article/page.json",
        "out/20260909-2212-hidden-costs-v2/product-page/index.html",
        "out/20260909-2212-hidden-costs-v2/product-page/page.json",
        "out/20260909-2212-hidden-costs-v2/longform/index.html",
        "out/20260909-2212-hidden-costs-v2/longform/page.json",
        "out/20260909-2212-hidden-costs-v2/REVIEW.md"
      ]
    },
    "listicle_hidden_costs": {
      "run_dir": "out/20260910-1836-hidden-costs-v2",
      "paths": [
        "out/20260910-1836-hidden-costs-v2/listicle/index.html",
        "out/20260910-1836-hidden-costs-v2/listicle/page.json",
        "out/20260910-1836-hidden-costs-v2/listicle/shopify-body.html",
        "out/20260910-1836-hidden-costs-v2/listicle/shopify-body.assets.json",
        "out/20260910-1836-hidden-costs-v2/REVIEW.md"
      ]
    },
    "listicle_product_features": {
      "run_dir": "out/20260910-1838-product-features-v2",
      "paths": [
        "out/20260910-1838-product-features-v2/listicle/index.html",
        "out/20260910-1838-product-features-v2/listicle/page.json",
        "out/20260910-1838-product-features-v2/listicle/shopify-body.html",
        "out/20260910-1838-product-features-v2/listicle/shopify-body.assets.json",
        "out/20260910-1838-product-features-v2/REVIEW.md"
      ]
    },
    "page_id": null,
    "page_id_note": "No new Shopify page id exists yet -- nothing from this cycle has been published.",
    "existing_listicle_page_id": "155864727853",
    "existing_listicle_redirect_id": "548101325101",
    "existing_listicle_note": "The live '5 Reasons' listicle page id/redirect id above are the CURRENT production page -- reference only, not created by this harness, and not what page_id above refers to."
  },
  "never_line": "No EMF mentions, anywhere, ever. No lender names while financing_lender is unconfigured. No unverified claims -- every specific claim on a page must trace to a claims/verified.json id. No AI-rendered image without 'Rendering:' leading its alt text. No publish, ever, without a packet stamped 'ship' and Caleb's explicit written approval.",
  "stamp": "BOT DRAFT · NOT SENT",
  "proof": {
    "preview_paths": [
      "out/20260909-2212-hidden-costs-v2/article-review.html",
      "out/20260909-2212-hidden-costs-v2/product-page-review.html",
      "out/20260909-2212-hidden-costs-v2/longform-review.html",
      "out/20260910-1836-hidden-costs-v2/listicle-review.html",
      "out/20260910-1838-product-features-v2/listicle-review.html"
    ],
    "research_memo_path": "docs/RESEARCH-LISTICLE.md"
  }
}
```

All paths above are relative to `~/advertorial` on the server (`ssh prod`); `out/` is
gitignored and exists only there, never in a local clone.

## Decisions still open for Caleb

- **Pending-claim approvals** (`claims/pending.json`, status `needs-caleb`, added fix
  cycle 12 problem 5 from a g Brain search -- all four confirmed real, current offerings,
  none yet moved to `claims/verified.json`):
  - **Peak Wellness Club** -- real, free-with-purchase; a pricing discrepancy was found
    between sources ("60-day free trial then $49/month" in two pages vs. "Currently FREE
    -- no trial, no cost" in a third) that needs resolving before any pricing detail here
    reaches `verified.json`.
  - **Leaderboard / Sauna Lounge** -- both real features; an internal engagement audit
    found the Lounge has near-zero real member activity (16 of 18 posts with zero
    engagement) -- worth checking against current numbers before an ad implies a bustling
    community.
  - **Expert protocols** -- "Celebrity & Expert Protocols" confirmed real; the "studies"
    half of "expert studies + actionable protocols" is not confirmed by any allowed-type
    source -- needs Caleb's call on "protocols" vs. "studies" framing.
  - **Longevity Lab** -- confirmed real, but high-ticket, application-only, and never
    included with a sauna purchase; not currently referenced by any live ad fixture.
  - **Peak Wellness Club price** -- see the pricing discrepancy above; blocks any
    financing/price claim about the Club specifically.
- **`ad_overclaim_policy`: `"stop"` or `"warn"`.** Currently `"stop"` in the committed
  config. `"warn"` lets a run continue past an unmatched or overclaimed ad claim (dropping
  it from what the writer may use, listed in `REVIEW.md`) instead of stopping outright --
  Caleb's call on which failure mode is safer for an unreviewed ad.
- **`financing_lender`.** Currently `null` -- no real lender approved. Until set, every
  financing line is locked to "Financing is available at checkout." with no figure or
  name.
- **AI render policy (`allow_ai_renders`).** Currently `false` (fix cycle 15 default). 34
  of the 105 listicle-pack files are AI composites, not photographs -- turning this on
  makes them eligible for selection (always alt-texted "Rendering:", never used as
  evidence of a real installation).
- **Publish mode.** `harness publish` exists as of cycle 20 (`harness/publishers/`,
  `docs/PUBLISHING.md`); it defaults to writing a draft (unpublished) Shopify page unless
  `--live` is passed, and refuses to run at all without an approval and a `ship` stamp. Who
  holds day-to-day "ship" stamp authority is still Caleb's call -- `reviewers` in
  `tenant.yaml` lists Michael (primary) and Caleb (backup) for *approval*, but
  `harness packet --stamp ship` has no separate authorization check today; the packet
  stamp and reviewer approval are two independently-gated steps, not one.
- **Shopify Admin API credentials.** `SHOPIFY_STORE` (the storefront's `*.myshopify.com`
  admin domain) and `SHOPIFY_TOKEN` are not set in this tenant's `.env` -- `store_admin_domain`
  in `tenant.yaml` is deliberately left `null` until the myshopify domain is confirmed. Until
  both are set, `harness publish` fails closed with a one-line message and makes no network
  call; `harness publish --dry-run` reports this without needing an approved run at all.
