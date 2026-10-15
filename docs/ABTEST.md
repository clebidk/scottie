# A/B/C tests (`harness abtest`)

Every new Meta ad gets three different page builds. The three pages go live
as hidden Shopify pages behind one **split link**. The team pastes the split
link into the ad as its destination in Ads Manager, or (cycle 107) the
harness puts it there with `harness abtest go-live` (see "Go live on Meta").

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
Cycle 107: `harness abtest go-live` does this step for an ad you choose; see
"Go live on Meta (cycle 107)".

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

Cycle 107:

```
harness abtest refresh-metrics [--test <test_id>]   # cache orders + Meta spend (cron, hourly)
harness abtest leaderboard [--by build|look] [--sort cta|orders]
```

`refresh-metrics` stores, on each live or finished test record, its orders
per arm (`orders_cache`, from Shopify) and its Meta ad's spend, impressions and
link clicks for the live period (`meta_insights`, from
`<ad_id>/insights?fields=spend,impressions,inline_link_clicks`; read at most
once an hour per test). The live period starts at go-live (`meta_go_live.at`),
else at `live_at` for a split link pasted by hand, and ends at the revert or
finish, else today (whole days, the ad account's time zone). `results` then
prints the Meta line and, per arm, its share of the spend and its cost per
order. The spend is shared by the arms in proportion to the visitors the split
page sent each one (assign events); Meta cannot see the split.

`leaderboard` pools every generated variant of every live and finished test by
BUILD (`<cartridge>:<look>:<first screen>`, from the variant's own `look` and
`hero_style`) or by LOOK, and ranks by CTA rate (default) or orders per 1000
views. A group with fewer than `abtest.min_views` views is listed after the
ranked ones, with its views and "not ranked". Orders per 1000 views counts the
views of tests with cached orders only. The site's Results page shows the same.

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

## Go live on Meta (cycle 107)

Owner instruction 2026-10-12: put generations live as A/B/C on the Meta ads the
owner chooses, until the owner turns on the lever for all new ads.

```
harness abtest go-live <test_id> --by <reviewer email> --dry-run   # first: read only
harness abtest go-live <test_id> --by <reviewer email> [--force]
harness abtest revert-go-live <test_id> --by <reviewer email>
```

Meta does not allow a change to an existing creative. `go-live` reads the ad
(`<ad_id>?fields=...creative{...}`), makes a COPY of its creative with every
link field set to the split link -- `object_story_spec.link_data.link`, the
`link_data` / `video_data` call-to-action `value.link`, carousel cards,
`asset_feed_spec.link_urls[].website_url`, `object_url` -- with `url_tags` and
every other field unchanged (POST `act_<id>/adcreatives`), points the ad at
the copy (POST `<ad_id>` with `creative={"creative_id": ...}`; Graph's update
call is a POST), reads the ad back, and checks that it uses the new creative
and that every link is the split link. The test record gets

```
"meta_go_live": {"at", "by", "ad_id", "ad_name", "old_creative_id", "new_creative_id",
                 "old_destination", "split_url", "verified_destination", "verified", "forced"},
"linked_at": "<at>"      (only when verified)
```

and the ad's inbox item gets the verified `destination_url`, so the linked-ad
icon shows it at once. Both commands write a row in the site's audit log
(`meta_go_live`, `meta_revert_go_live`, target `test:<test_id>`).

Consequences, printed before every change: Meta restarts the ad's learning
phase, and the new post starts with no likes, comments or shares (the old post
keeps them).

Refusals (nothing is written): the test is not live or has no split link; it
was not built from a Meta ad (an upload); it is live on Meta already (revert
first); the ad is in another ad account; the ad is not ACTIVE (approved and
running) unless `--force`; the ad is an existing-post ad (`object_story_id`, no
spec) -- change that one by hand in Ads Manager. A read-back that does not
match is recorded with `"verified": false` and the command exits 1; check the
ad in Ads Manager and revert. A POST is not retried after a network error or a
5xx (it may have gone through); only a rate-limit refusal is retried.

`revert-go-live` sets the ad back to `old_creative_id`, reads it back, checks
the creative and the old link, and records `reverted_at`, `reverted_by`,
`revert_verified_destination`, `revert_verified` on the block; `linked_at`
goes back to null. A later go-live moves the old block to
`meta_go_live_history`.

The token needs `ads_management` (the owner's system-user token has it,
2026-10-12). On the site: the "Go live on Meta" and "Revert" buttons on a live
test's card (docs/LISTICLE-SITE.md).

### The lever: `abtest.auto_go_live`

`tenant.yaml` `abtest.auto_go_live` (default false; PEAK ships it OFF). When
true, `harness abtest from-inbox` goes live for each test it has just built
AND published (so it needs `auto_publish: true` too), with the same record.
Only for an ad whose effective_status is ACTIVE: a test whose ad is still in
review is marked `auto_go_live.state: pending`, and each later from-inbox run
(the hourly meta-pull cron) tries it again, so it goes live as soon as Meta
approves the ad. It never uses `--force` and never touches an existing-post ad
(`state: refused`). Tests the lever did not publish (for example the 20 tests
live on 2026-10-12) are never touched by it. Cycle 108: nor a test created
before `abtest.auto_since`, nor a "manual link" test ("Automatic pipeline").

## Replace a live variant (cycle 69)

On the listicle site, feedback on a live variant makes a new version of its
page but does not publish it. **Replace live variant** (with a confirm step)
updates the variant's Shopify page and resets that variant's views and CTA
clicks: its old events move to the key `<key>@<n>` in `events.sqlite`, and
the test record lists them under the variant's `replacements`. `results` and
`finish` count only the events after the replacement; the library's pooled
statistics still count the old ones for the build. See
docs/LISTICLE-SITE.md.

## Rebuild an arm (cycle 109)

```
.venv/bin/harness abtest rebuild-arm <test_id> A --by <reviewer email> --tenant peak-saunas
.venv/bin/harness abtest rebuild-arm <test_id> --all --by <reviewer email> --tenant peak-saunas
```

Builds a live test's generated arm again with the current code and claims and updates the arm's
OWN Shopify page: same page id and handle, so the split link and the Meta ad that points at it are
untouched. The build is the arm's own (same cartridge and style), pinned to the look and hero the
arm rendered, with the next seed of the arm's seed family (`seed + 1000 * (letter index + 1) +
attempts + 1`, as the first build). The design gate runs in warn mode for the record
(`HARNESS_DESIGN_GATE=warn` for that run only): its score goes on the variant, a low score never
stops the rebuild. The new run takes over the old run's published-page record and the test's
beacon, then `harness publish --update` runs (approve and ship stamp first, as for every A/B/C
publish). The variant's `run_dir` becomes the new run; the old one is kept under the variant's
`history` with its design score, look, hero and the time it was replaced. The arm's views and
clicks are not reset (Replace live variant does that). Refused, with nothing changed: a test that
is not live, an external arm, an arm with no published page, an unlisted reviewer. A failed build
leaves the live page as it was. `--all` checks every generated arm before it builds any.

## Held offers (cycle 109)

`from-inbox` reads each built arm's ad brief. When the ad states an offer (harness/offer.py) that
matched no verified promo claim, the test is built and published but NOT taken live on Meta: it
gets `offer_hold` (`state: held`, `reason: "held: offer not verified"`, the ad's offer sentence),
the lever skips it (`auto_go_live.state: held`), the inbox item's reason says so, and the Ads page
status line counts it ("1 held (offer not verified)"). The ad's card shows the sentence and a form,
prefilled with that sentence and its deadline, that writes the store's offer to
`claims/verified.json` as a `promo` claim (id `promo-<name>-<date>`, source `owner <email>
<date>`, approved by the reviewer). The form refuses a text that does not state the ad's amounts
and codes (or whose date has passed). Each later from-inbox run (`release_held`, the hourly cron)
checks held live tests against verified.json: once the claim matches, it rebuilds every generated
arm (`rebuild-arm --all`, so the pages become offer-led), marks the hold `released` and, with the
lever on, sets the go-live `pending`; the same run's retry takes the test live on its ACTIVE ad.
A failed rebuild marks the hold `rebuild_failed` (shown on the card, not retried): run
`harness abtest rebuild-arm <test_id> --all`, then go live by hand. Go live by hand (the card's
button or `abtest go-live`) still works on a held test; that is the owner's call.

The form edits verified.json in the prod working tree; commit it (or copy the claim into the
repository) so a later deploy does not overwrite it.

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

### Cycle 110: the PEAK library and the beacon's `data-el`

PEAK's `abtest.library` is `landing:peak`, `landing:proof`, `landing:cinematic`: `open` is retired from the
library (it lost head to head; its files stay and `--look open` still works). Agent A's test library
(`landing:peak` vs `landing:proof` vs `external:home`) replaces it when merged. Every landing CTA carries
`data-el` = hero | mid | stack | cards | reviews | closing | sticky, and "Text us" `data-el="sms"` (not a CTA
class), for the beacon's per-element counts. Model cards on the `peak` look link to each model's own product
page with class `ld-link` (a CTA class).

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
accepts only a JSON body of 1 KB or less. The event must be `view`, `cta`,
`assign` (cycle 88, from the split page), `sms_tap` or `leave` (cycle 110)
and must name a live or finished test and one of its variants. The receiver
drops crawlers and link previewers (for example facebookexternalhit)
silently. A repeat event from the same visitor is a no-op. The limit is 60
events per minute per IP. Replies: 204 (stored, duplicate, or bot), 400,
413, 429.

## Beacon detail (cycle 110)

The variant beacon (`abtest.beacon_script`, under 2 KB, no library) sends
more than view and CTA. Each field is optional, checked and bounded by
`abevents.parse_event`; a bad field is dropped, never the event.

| Event | When | Fields (wire -> column) |
|---|---|---|
| `view` | on load | `vh`, `vw` (viewport px), `cn` -> `conn` (effective connection type) |
| `cta` | a click on a CTA anchor (`CTA_CLASSES`) to /products/ or /collections/ | `el` (the anchor's `data-el`: hero, mid, cards, reviews, closing, sticky, sms, other), `y` (scroll depth % at the click) |
| `sms_tap` | a tap on an `sms:` link (never a CTA) | `el`, `y` |
| `leave` | pagehide, or the page goes hidden | `m` -> `max_scroll` (%), `s` -> `secs`, `lcp`/`fcp`/`dcl` -> `lcp_ms`/`fcp_ms`/`dcl_ms`, `hid` -> `hidden_ms` (ms to the first tap/key or the first hide) |

One row per (test, variant, visitor, event) as before, so CTA counts stay
unique visitors and `el` is the visitor's first CTA click. A repeat `leave`
updates its row: the deepest scroll and the longest stay win, the first load
timings stay. `abevents.connect` adds the new columns to an old database in
place; old rows keep NULL and read as before. `harness abtest results` adds,
per generated arm with new data: clicks by element and sms taps, the scroll
depth at the click, the deepest-scroll distribution of visitors who left and
their median time on the page, p50/p75 LCP with the share over 2.5 s, and the
share of viewports under 700 px tall. Pages published before cycle 110 carry
the old beacon until they are re-rendered (`harness abtest rebuild-arm`).

Every test record also carries `ad_angle` (sale, financing, recovery, story,
apartment, other; `abtest.ad_angle` from the arm's ad brief, set when the
test is built, derived on read for older tests), shown by `abtest status`
and `abtest results`.

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
  home; one external arm alone. Cycle 108: the arms take the letters A, B, C
  in the order of the list, so an external arm listed first is arm A (the
  split page, `utm_content=<test_id>_<key>` and each variant's beacon use the
  same letter). Every preset in the table lists its external arms last, so its
  letters did not change. A pinned page version is the first GENERATED arm.
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
  Ads Manager by hand, as before. (Cycle 107: or `harness abtest go-live`.)

## Automatic pipeline (cycle 108)

Owner decision 2026-10-13: "lets turn it on. only exception is if ad is
uploaded with specific landing page, that becomes A of the A/B/C test and only
2 others are generated". Earlier: every new approved Meta ad gets versions, 2
generated with the 3rd a control (the PDP or the home page).

The hourly job `crons/meta-pull.sh` (installed by `crons/install-cron.sh`,
minute 7, docs/META-INGEST.md section 5) pulls new ads, refreshes every ad's
status and link, then runs `harness abtest from-inbox`:

1. **Which ads.** Only Meta ads whose `effective_status` is `ACTIVE`,
   `PENDING_REVIEW` or `IN_PROCESS` and whose `created_time` is on or after
   `abtest.auto_since` (00:00 UTC of that date; PEAK: `2026-10-13`). Every
   other item stays `new`, nothing is written to it, and its card on the Ads
   page says `not auto-built: <reason>`. A paused ad that is turned on later
   (after `auto_since`) is built on the next run after refresh-links sees it
   ACTIVE. Uploads, `harness abtest create` and the site's upload form are not
   limited by these rules.
2. **How many.** At most `abtest.auto_max_per_run` items per run (default 5;
   `0` stops auto builds), with the daily budget cap as before (queued items
   resume on the next run). `--limit N` can only lower it. The rest wait as
   `new` for the next run.
3. **Which plan.** `abtest.inbox_plan` (default `auto`; or a preset id). An
   upload's own Destinations choice wins, then `from-inbox --destinations`,
   then `inbox_plan`. Under `auto`, `abtest_inbox.plan_for_item` takes the
   first rule that fits, and the test records `plan` and `plan_rule`:

   | Plan name | Rule | A | B | C |
   |---|---|---|---|---|
   | `auto-page` | 1: the ad links to a specific page on the store (not `/`, not a `/products/<handle>` page, not a harness `/pages/lp-...` page) | that URL | generated | generated |
   | `auto-pdp` | 2: the ad's own words (ad name, headline, primary text, description) name a model the product map knows (the same detection as the PDP arm) | generated | generated | that model's PDP |
   | `auto-home` | 3: anything else | generated | generated | home page |

   Rule 2 names the PDP before any build, from the ad copy. A model named
   only in the video's speech is not seen here, so such an ad gets rule 3.
4. **Levers.** Unchanged from cycle 107: with `auto_publish` the test is
   published, and with `auto_go_live` too it goes live on the ad once the ad
   is ACTIVE (pending tests are tried each hour). The lever never goes live
   for a test created before `auto_since`, and never for a "manual link" test.
5. **Manual link.** An ad whose creative has no `object_story_spec` and no
   `asset_feed_spec` (an existing Page post) is built and published, but the
   test gets `manual_link` and the card says "Manual link": go-live cannot
   change that ad, so paste the split link into it by hand.
6. **Status.** Each from-inbox run writes `tenants/<t>/runs/auto-pipeline.json`
   (`inbox`: at, built, failed, published, went_live, pending, waiting, left,
   skipped and skipped_by, spend_usd for the run's builds, the levers, and per
   item the plan and rule; `pull`: the last `harness meta pull` count). The Ads
   page shows it as one line, for example "Auto pipeline: last run 14:07,
   pulled 4, 2 built, 2 published, 1 live, 1 pending, 3 skipped (paused),
   $0.84 spend (auto publish on, auto go-live on)". `meta-pull.log` names each
   ad and the rule of its plan.

### Runbook

Turn it off (from the repo on prod, as the deploy user):

```
# stop going live on ads; tests still build and publish
#   tenant.yaml abtest.auto_go_live: false
# stop publishing too (builds still run, for review)
#   tenant.yaml abtest.auto_publish: false
# stop building new tests (pull and refresh-links still run)
#   tenant.yaml abtest.auto_max_per_run: 0
# stop the hourly job entirely
crons/install-cron.sh --remove peak-saunas
```

A tenant.yaml change takes effect on the next hourly run. A test that is
already live on its ad stays live; revert it with `harness abtest
revert-go-live <test_id> --by <email>`.

Re-point one ad by hand:

```
# take this ad's test off the ad (the ad links to its old page again)
.venv/bin/harness abtest revert-go-live <test_id> --by <reviewer email> --tenant peak-saunas
# build a new test with other destinations for the same ad's media
.venv/bin/harness abtest create --tenant peak-saunas --name "<ad name>" \
  --input tenants/peak-saunas/meta_inbox/<ad_id>/<media_file> \
  --destinations '[{"kind":"external","target":"url","url":"https://peaksaunas.com/pages/<page>"},{"kind":"generated"},{"kind":"generated"}]'
.venv/bin/harness abtest publish <new_test_id> --by <reviewer email> --tenant peak-saunas
# or paste the new split link into the ad in Ads Manager by hand
```

`abtest create` has no option to name the Meta ad, so the new test cannot use
`go-live`; paste its split link into the ad in Ads Manager. To build an ad the
rules skipped (older or paused) with the automatic plan, use the same
`abtest create` with the destinations the table above gives.

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

