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
  fbclid) and adds `pk_t=<test_id>&pk_v=<A|B|C>`.
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

## How builds are picked

- Library: `tenant.yaml` `abtest.library`. An entry is `cartridge`,
  `cartridge:look`, or `listicle:style[:look]`. If the entry has no look, the
  tenant's style to look pairing supplies it.
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
accepts only a JSON body of 1 KB or less. The event must be `view` or `cta`
and must name a live or finished test and one of its variants. The receiver
drops crawlers and link previewers (for example facebookexternalhit)
silently. A repeat event from the same visitor is a no-op. The limit is 60
events per minute per IP. Replies: 204 (stored, duplicate, or bot), 400,
413, 429.
