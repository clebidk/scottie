# Coach packet draft

**Stamp: `BOT DRAFT · NOT SENT`** -- this is a draft object for Caleb to review, not a
message that has gone anywhere. Nothing in this file has been sent to anyone, published,
or acted on.

**Cycle 19 update:** the byline decision is applied (author = Austin Laudenslager, Founder
& CEO; contributor = "Peak Saunas Editorial Team", no named person; reviewer = Caleb
Niednagel, Technology Lead -- see `tenants/peak-saunas/authors.yaml`), `ad_overclaim_policy`
is now committed as `"warn"` (no longer restored to `"stop"` after a sweep), and all seven
fixtures plus both listicle runs PASS under it -- see `docs/SWEEP-2026-09-11.md` for the full
table, per-ad omissions, and the mobile-pass findings. Every run below is `needs_review`,
every packet stamp is still `BOT DRAFT · NOT SENT` -- nothing has been approved or published
this cycle.

### Cycle 19 final run ids (needs_review, nothing approved or published)

| Ad | Cartridges | Run id | State | Pages |
|---|---|---|---|---|
| hidden-costs-v2.mov | article, longform, product-page | `out/20260910-2250-hidden-costs-v2` | needs_review | article, longform, product-page: needs_review |
| hidden-costs-v2.mov | listicle | `out/20260910-2319-hidden-costs-v2` | needs_review | listicle: needs_review |
| product-features-v2.mov | article, longform, product-page | `out/20260910-2253-product-features-v2` | needs_review | article, longform, product-page: needs_review |
| product-features-v2.mov | listicle | `out/20260910-2320-product-features-v2` | needs_review | listicle: needs_review |
| price-comparison-v2.mov | article, longform, product-page | `out/20260910-2255-price-comparison-v2` | needs_review | article, longform, product-page: needs_review |
| still-lessthan300-4x5.png | article, longform, product-page | `out/20260910-2258-still-lessthan300-4x5` | needs_review | article, longform, product-page: needs_review |
| still-levelup-4x5.png | article, longform, product-page | `out/20260910-2306-still-levelup-4x5` | needs_review | article, longform, product-page: needs_review |
| still-infraredglow-4x5.png | article, longform, product-page | `out/20260910-2313-still-infraredglow-4x5` | needs_review | article, longform, product-page: needs_review |
| still-unforgettable-4x5.png | article, longform, product-page | `out/20260910-2317-still-unforgettable-4x5` | needs_review | article, longform, product-page: needs_review |

All paths relative to `~/advertorial` on the server (`ssh prod`); `out/` is gitignored, exists
only there.

### Exact approve / packet / publish commands (none of these have been run this cycle)

```
harness approve tenants/peak-saunas/out/<run-id> --by <email> --pages <cartridge>[,<cartridge>...]
harness packet tenants/peak-saunas/out/<run-id> --stamp ship --by <email>
harness publish tenants/peak-saunas/out/<run-id> --page <cartridge> --dry-run   # check credentials first
harness publish tenants/peak-saunas/out/<run-id> --page <cartridge>            # draft page
harness publish tenants/peak-saunas/out/<run-id> --page <cartridge> --live     # live, + 8-pull cache check
```

`--by` must be `michael@peaksaunas.com` (primary) or `caleb@peaksaunas.com` (backup) per
`tenant.yaml`'s `reviewers` list -- an email not on that list is refused. `harness publish`
refuses outright unless both the page's own state is `approved` and the packet stamp is
exactly `ship`.

**The never-line (unchanged, still the standing rule):** No EMF mentions, anywhere, ever. No
lender names while `financing_lender` is unconfigured (now configured: Bread Pay, gate-
enforced since Cycle 21 -- see `docs/SWEEP-2026-09-11.md`'s financing-sentence check). No
unverified claims -- every specific claim on a page must trace to a `claims/verified.json`
id. No AI-rendered image without "Rendering:" leading its alt text. No publish, ever, without
a packet stamped `ship` and Caleb's explicit written approval.

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
- ~~`ad_overclaim_policy`: `"stop"` or `"warn"`.~~ **Decided, Cycle 19: `"warn"`, committed as
  the default** (`tenants/peak-saunas/claims/config.json`) -- no longer restored to `"stop"`
  after a sweep. All seven fixtures PASS under it; see `docs/SWEEP-2026-09-11.md`.
- ~~`financing_lender`.~~ **Decided, Cycle 18/21: Bread Pay**, gate-enforced since Cycle 21 --
  every financing line renders exactly "Financing is available through Bread Pay at
  checkout." (verified across all 23 Cycle 19 sweep pages).
- **Cycle 19 byline decision, for the record (already applied):** author = Austin
  Laudenslager, Founder & CEO, responsible for every claim; contributor = "Peak Saunas
  Editorial Team" (no named person, was previously Caleb); reviewer = Caleb Niednagel,
  Technology Lead, reviews specifications and sources -- a new third role, distinct from
  contributor. See `tenants/peak-saunas/authors.yaml` and `tenants/peak-saunas/brand/
  byline.html`.
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
- **Shopify Admin API credentials -- still open, Cycle 19.** `SHOPIFY_STORE` (the storefront's
  `*.myshopify.com` admin domain) and `SHOPIFY_TOKEN` are not set in this tenant's `.env` --
  the Shopify token has not yet been saved. `store_admin_domain` in `tenant.yaml` is
  deliberately left `null` until the myshopify domain is confirmed -- candidate domain
  `bd4b8d-2.myshopify.com`, needs Caleb's confirmation before it's written to `tenant.yaml`.
  Until both `SHOPIFY_STORE`/`SHOPIFY_TOKEN` are set, `harness publish` fails closed with a
  one-line message and makes no network call; `harness publish --dry-run` reports this without
  needing an approved run at all. No Shopify call was made at any point in Cycle 19.
- **New, Cycle 19: `.adv-cta`/`.adv-sticky-cta`/image-sizing CSS gap.** The mobile pass found
  every live page's CTA button, and `longform`'s sticky bottom CTA bar, render with zero CSS at
  all -- `tenants/peak-saunas/brand/base.css` never defines those classes. Not fixed this cycle
  (root cause is the tenant's own CSS, not a `cartridges/*/template.html` bug, so outside this
  cycle's authorized fix scope) -- flagged as a follow-up task. See `docs/SWEEP-2026-09-11.md`'s
  "Mobile pass" section for detail.
- **New, Cycle 19: Slack notifications still off (`tenant.yaml`'s `notifications.slack:
  false`), on purpose.** Cycle 20's verification runs posted 7 real messages by accident
  (the webhook was already saved, `notifications.slack` was `true`); disabled pending one
  deliberate test (see `docs/FIXLOG.md`'s "Known issue, 2026-09-10 22:30" note). That one test
  ran this cycle (below) via `harness/notify.py`'s own functions directly, bypassing the
  tenant-level toggle for that single send -- `notifications.slack` was deliberately left
  `false` afterward so no future run can post automatically until Caleb decides to flip it back
  on himself.
