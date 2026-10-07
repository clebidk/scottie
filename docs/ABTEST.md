# A/B/C tests (`harness abtest`)

Every new Meta ad gets three different page builds. The three pages go live
as hidden Shopify pages behind one **split link**. The team pastes the split
link into the ad as its destination in Ads Manager. The harness never edits
an ad.

The winner is the build with the highest **CTA click-through**: the share of
unique visitors on a variant who click a CTA to a product page. Orders show
as a second number and break a near-tie.

Code: `harness/abtest.py` (library, selection, record, scripts, orders),
`harness/abstats.py` (P(best)), `harness/abevents.py` + `POST /e` in
`harness/serve.py` (event receiver).

## 1. Create

```
harness abtest create --tenant peak-saunas \
  --input tenants/peak-saunas/fixtures/<ad>.mov --name "<ad name>"
```

- The test id is a slug of the name plus 4 characters, for example
  `hidden-costs-v2-cfc5`.
- The harness picks 3 builds from `abtest.library` in `tenant.yaml` and runs
  `harness run` once for each build.
- A build that fails is tried with 3 seeds. Then the next-best unused build
  replaces it.
- If the daily budget cap stops a run, the test is `queued` (exit 3). When
  the cap allows more runs, do `harness abtest resume <test_id>`.
- Optional: `--source-json meta.json` keeps the ad's Meta fields (ad id, name,
  campaign) on the test record.

Review the three pages in the review app as usual before you publish.

Cycle 86: an upload from the Library's **Generate with this** pins one build
(the inbox item's `pinned_arm`, e.g. `landing:proof`). That build is variant
A; B and C are chosen as above. The record keeps `pinned_arm`.

Cycle 69: an ad uploaded on the listicle site (docs/LISTICLE-SITE.md) and
`harness abtest from-inbox` (Meta inbox items) create tests the same way.
When `abtest.auto_publish` is true in `tenant.yaml` (PEAK: true; template:
false), they also run step 2 at once, with no review first.

## 2. Publish

```
harness abtest publish <test_id> --by <reviewer email>
```

For each variant, this command approves the page, stamps the packet `ship`,
and publishes the page through `harness publish` with the handle
`lp-<test_id>-a|b|c`. Then it publishes the split page `lp-<test_id>`. At the
end it prints:

```
SPLIT LINK (paste as the ad's destination in Ads Manager): https://peaksaunas.com/pages/lp-<test_id>
```

- All four pages get the Shopify metafield `seo.hidden = 1`. This keeps them
  out of the sitemap and search results.
- Each variant body gets a small tracking script at the end.
- The split page body is only a small script and a `<noscript>` link list.
  The script keeps a returning visitor on the same variant (cookie
  `pk_ab_<test_id>`, 30 days). It forwards the full query string (utm_*,
  fbclid) and adds `pk_t=<test_id>&pk_v=<A|B|C>`. Cycle 88: it also sets
  `utm_content=<test_id>_<key>` and, when the query has none,
  `utm_source=meta`, `utm_medium=paid`, `utm_campaign=<test_id>`, and it
  sends an `assign` event before it redirects (see "Control arms").
- If one page fails, fix the error and run the same command again. Pages
  that are already published are updated, not duplicated.
- `--draft` publishes all four pages unpublished. Use it only to check the
  pages. The split link does not work until the pages are live.

Before the first live test, make sure of these three things:
1. `abtest.beacon_url` (`https://listicle.peaksaunasteam.com/e`) reaches
   `harness serve`'s `POST /e`. Publish refuses while the setting is empty.
   It does not check that the domain resolves.
2. The Shopify token has the `read_orders` scope. Without it,
   `harness abtest orders` reports "unavailable" and the CTA numbers still work.
3. Open the split link once in a private window. You must land on a variant
   URL that contains `pk_t=` and `pk_v=`.

## 3. Paste the split link

In Ads Manager, set the ad's website URL to the split link. Keep your usual
URL parameters (utm_*). The split page forwards them to the variant.

## 4. Read results

```
harness abtest status [<test_id>]
harness abtest results <test_id>          # add --no-orders to skip Shopify
harness abtest orders <test_id>
harness abtest library
```

`results` shows, for each variant: unique views, unique CTA clickers, CTR,
P(best) (Beta posterior, Monte Carlo), orders and revenue. An order counts for
a variant when its `landing_site` has `pk_t=<test_id>&pk_v=<key>`, or when its
cart attribute `pk_ab` is `<test_id>:<key>`. The variant page sets that cart
attribute when a visitor clicks a CTA. Cancelled orders do not count.

`library` shows each build's pooled results over all live and finished tests,
and how often the selection would pick it now (`P(picked)`).

## 5. Finish

```
harness abtest finish <test_id> [--force] [--orders]
```

This names the winner and marks the test `finished`. It refuses unless both
of these are true:
- each variant has at least `abtest.min_views` (default 300) unique views;
- the leader's P(best) is at least 0.95.

`--force` finishes without these rules. `--orders` fetches orders, and when
two P(best) values are within 0.05, the variant with more orders wins.
Finishing does not remove the pages. To stop traffic, change the ad's URL.

## Replace a live variant (cycle 69)

On the listicle site, feedback on a live variant makes a new version of its
page but does not publish it. **Replace live variant** (with a confirm step)
updates the variant's Shopify page and resets that variant's views and CTA
clicks: its old events move to the key `<key>@<n>` in `events.sqlite`, and
the test record lists them under the variant's `replacements`. `results` and
`finish` count only the events after the replacement; the library's pooled
statistics still count the old ones for the build. See
docs/LISTICLE-SITE.md.

## How builds are picked

- Library: `tenant.yaml` `abtest.library`. An entry is `cartridge`,
  `cartridge:look`, or `listicle:style[:look]`. If the entry has no look, the
  tenant's style to look pairing supplies it.
  Cycle 85: `landing:open` and `landing:proof` let a test compare the two
  landing looks; each built variant records the `look` its page.json was
  stamped with.
- With a probability of `abtest.explore` (default 0.2), the harness picks 3
  builds at random. If no build has data yet, it also picks at random.
- Otherwise it uses Thompson sampling: one draw per build from
  Beta(clicks + 1, views - clicks + 1), pooled over all live and finished
  tests. The top 3 draws win.
- A build with no data yet draws from Beta(1, 1). This draw is often higher
  than a proven build's CTR (about 10-20%). Thus the harness tries every
  build at least once before it favors the best ones.

## Data

- Test records: `tenants/<t>/abtests/<test_id>.json`.
- Events: `tenants/<t>/abtests/events.sqlite` (WAL). There is one row for
  each (test, variant, visitor, event). The IP is never stored. The database
  keeps only a salted hash of 16 hex characters.
- Both are gitignored, and `crons/backup-tenant-data.sh` backs them up with
  the rest of the tenant tree. Clearing `out/` does not delete them.
  Deleting a variant's run directory breaks the link from the test to its
  page.

## The receiver (`POST /e`)

This is the one route in `harness serve` that is not behind the login. It
accepts only a JSON body of 1 KB or less. The event must be `view`, `cta` or
`assign` (cycle 88, from the split page)
and must name a live or finished test and one of its variants. The receiver
drops crawlers and link previewers (for example facebookexternalhit)
silently. A repeat event from the same visitor is a no-op. The limit is 60
events per minute per IP. Replies: 204 (stored, duplicate, or bot), 400,
413, 429.

## Control arms (cycle 88)

Per ad, the operator can make an arm the product's page (PDP) or the store
home page instead of a page the harness builds, or send the ad only to one
of them. The split link stays in every case: the click is counted, and the
destination can be changed later without a new link in the ad.

| Destinations (site) | `--destinations` | A | B | C |
|---|---|---|---|---|
| 3 generated (default) | `generated` | generated | generated | generated |
| 2 generated + PDP | `gen2-pdp` | generated | generated | PDP |
| 2 generated + home | `gen2-home` | generated | generated | home |
| 1 generated + PDP + home | `gen1-pdp-home` | generated | PDP | home |
| PDP only | `pdp-only` | PDP (100%) | | |
| Home only | `home-only` | home (100%) | | |

- Where to choose: the **Destinations** field on the upload form, `harness
  abtest create --destinations <plan>`, and `harness abtest from-inbox
  --destinations <plan>` (for inbox items that have no plan of their own; an
  upload's choice wins). The CLI also takes a JSON list of arms, for example
  `[{"kind":"generated"},{"kind":"generated"},{"kind":"external","target":"url","url":"https://..."}]`.
  Allowed shapes: 3 generated; 2 generated + 1 external; 1 generated + PDP +
  home; one external arm alone. Generated arms always take the first keys.
- A plan is fixed when the test is created. To change it, make a new test.
- **PDP** is the product page of the product the ad names: the
  `facts_pack.product.url` of the first built variant (the URL its CTA links
  to). With no generated arm (`pdp-only`), the harness reads the ad once
  (`harness ingest`, one model call, spend recorded) and takes the product
  from `claims/products.json`. If the ad names no product, the PDP arm is
  refused and the test fails with "product not named in ad": the run's
  fallback to the default product is acceptable for a written page, but it
  would send the ad to a product the ad never mentions. With a generated arm,
  the refusal comes after the first build, not after all of them.
- **Home** is `site_url` from `tenant.yaml` plus `/`.
- The split page redirects within the store (`/products/<handle>`, `/`), with
  the same cookie, query string and `pk_t`/`pk_v` as for a generated page.
- **Counting.** An external page has no beacon. The split page sends
  `{e: "assign"}` to `POST /e` (same visitor id `pk_vid`, same 60 per minute
  limit, one row per visitor and arm) before it redirects. For an external
  arm this is the only number the harness has: `results` shows it under
  `assigned` and shows `-` for views, CTA, CTR, P(best), orders and revenue,
  with the note "measure in GA4/Shopify by utm_content=<test_id>_<key>".
  Orders are not credited to an external arm: the cart attribute that
  attributes them is set by the beacon.
- P(best) compares generated arms only, and only when there are two or more.
  `finish` refuses a test with fewer than two generated arms; `--force`
  finishes it with no winner.
- The harness never writes to Meta. You paste the split link into the ad in
  Ads Manager by hand, as before.

## First-screen styles (cycle 79)

A listicle build is `listicle:<style>:<look>:<hero>` -- the copy style, the look (one since
cycle 79: `open`; a retired look name resolves to it) and the first-screen style (`face`,
`story`, `display`; harness/first_screen.py). The default library (and PEAK's) pairs every
copy style with a first screen so all three are in play; the Thompson sampler is unchanged
(one arm = one build). Each listicle variant records the `hero_style` it actually rendered
(face falls back to story/display when the ad has no usable still), and
`harness abtest library --by hero` pools CTR per first-screen style across tests. An entry
without the 4th part still works: the run seeds a hero style. The beacon counts clicks on
the open look's `op-btn` / `op-link` (abtest.CTA_CLASSES).

