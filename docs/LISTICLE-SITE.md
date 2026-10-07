# The listicle site (listicle.peaksaunasteam.com)

Cycle 69. The team uses this site to:

- see every ad and its landing pages, grouped by ad name;
- give feedback on a page, which makes a new version of that page;
- upload an ad, which builds an A/B/C test from it (and, when
  `abtest.auto_publish` is on, publishes the test and shows the split link).

The site is part of `harness serve` (`harness/serve.py`, `harness/site.py`).
It uses the same login as the reviewer app. Long work does not run in the web
app: the site writes a job, and `harness worker` does the job.

```
browser --> Caddy (listicle.peaksaunasteam.com) --> harness serve 127.0.0.1:4870
                                                        |  writes jobs
                                                        v
                                   tenants/<t>/jobs/jobs.sqlite (+ audit log)
                                                        ^  runs jobs, one at a time
                                                        |
                                           harness worker (harness-worker@<t>)
```

## 1. Pages

| URL | What it shows |
|---|---|
| `/` | Every ad, newest first, 20 for each page. Filters **All / Needs review / Live** (with counts) and a search by ad name. For each ad: its thumbnail (the image ad, or the ad still of a video ad), name, source, inbox state, created date, and its generations as phone-shaped cards (a scaled live preview, hero style, headline, style, headline template, Jev composite, gate, status chip Draft / Approved / Live / Hidden, the live URL with a Copy button, A/B/C numbers for a test, and **Post live**). Also the split link with a Copy button and today's model spend against the daily cap. |
| `/gen/<run>/<page>` | One generation: phone / desktop preview (old version next to the new one after feedback), the Status card (live URL, **Post live**, **Update live page**, **Unpublish**, **Publish new version** or **Replace live variant**, the latest publish job), the feedback form, the details (ad, style, hero, headline template, skeleton, look, Jev composite, gate, cost, run id, created), the Jev drafts table, the versions and the history. |
| `/gen/<run>/<page>/post-live`, `/update-live`, `/unpublish` | Cycle 80: the confirm step (a dialog on the page, or this page without JS) and the POST that queues the job. |
| `/run/<run>/ad-frame` | Cycle 80: a thumbnail of the run's ad still (`ad-frame/frame.jpg`). |
| `/upload` | The upload form. Cycle 86: `?cartridge=&look=&hero_style=&style=` pins a page version (the Library's **Generate with this**). |
| `/library` | Cycle 86: every page version the harness can generate, as reference cards grouped by cartridge (see **Library** below). |
| `/library/<version>/review`, `/library/<version>/thumb` | Cycle 86: one version's self-contained reference page (framed like `/run/<run>/review/<page>`) and its thumbnail. |
| `/library/rebuild` | Cycle 86: the POST of **Rebuild library** (queues a `library_build` job). |
| `/jobs`, `/job/<id>` | The job queue and one job (state, reason, result, split link). |
| `/audit` | Who did what, and when. |
| `/runs` | The old run list (it was at `/` before cycle 69). |
| `/e` | The A/B/C beacon receiver (cycle 67). The only route without a login. |

### Look (cycle 80)

`harness/site_ui.py` holds the shell, the stylesheet and the small script
(copy, phone / desktop preview, the post-live dialog, the slug check, Cmd/Ctrl+K
to the search). Owner direction 2026-10-06: the team dashboard's layout
(peaksaunasteam.com) in the brand's official palette, with no dark sidebar and
no Solar Flare:

- a left sidebar in Fossil Dust `#C0C8C3` with Basalt `#181918` text: the
  logo (`brand/logo-basalt.svg`), "Listicles workspace", nav groups Review
  (Ads, Library, Upload an ad -- Library since cycle 86), Queue (Jobs with a
  running-jobs badge, Audit log) and Library (Runs, Images); the active row is Stone `#EFE3D2`; the reviewer at
  the bottom;
- a top bar with a search pill (ads by name; keeps the home filter) and the
  reviewer's name and email;
- Stone `#EFE3D2` ground, cards `#F7F1E8`, Cedar-tinted hairlines, 8px radius,
  flat surfaces, Basalt primary buttons, Red `#702B33` only for live and
  attention chips;
- Acid Grotesk (the webfont the generator loads from the theme CDN) with a
  Helvetica Neue fallback, Geist Mono (Google Fonts) for numbers and labels.

On a phone (900px and less) the sidebar becomes a top row with the logo and a
sideways-scrolling row of nav pills; the generation cards scroll sideways in
each ad, the dialog is a bottom sheet, tables become stacked rows. From 1100px
the home page puts two ad cards side by side. The older reviewer pages
(`/runs`, `/run/<id>`, `/images`) use the same shell. 404 and 500 errors and a
wrong form token get a styled page. The site's own icon (Stone wave mark on
Fossil Dust, `harness/static/site/`) is served at `/favicon.ico`,
`/site-static/<name>` and `/site.webmanifest`; generated pages never link it.

### Ads and their names

An ad is a group of these, by name (lower case, and every run of characters
that are not letters or digits counts as one space, so "Hidden Costs V2" and
"hidden-costs-v2" are the same ad):

- A/B/C tests (`tenants/<t>/abtests/*.json`, the test name);
- inbox items (`tenants/<t>/meta_inbox/*/ad.json`, Meta pulls and uploads,
  the `ad_name`);
- older runs (`tenants/<t>/out/<run>/`, the ad brief's `source_file` without
  its extensions). A run that is a test variant shows only under its test.
  Runs that the test suite made (no model call in the run log) do not show.

### Feedback and regenerate

- Feedback is free text, 1 to 2,000 characters. The logged-in email is the
  author. A line that starts with `cut:` removes that exact sentence (no
  model call). The other lines go to the writer as notes.
- The site records the feedback with `runstate.request_changes` (as the
  reviewer app does) and queues a `regenerate` job. The job runs
  `revise.revise_page` (the code of `harness revise`). The old page is kept
  as `page.vN.json` / `index.vN.html` / `<page>-review.vN.html`; the new page
  is version N + 1.
- While a regenerate job for a page is queued or running, the site refuses
  more feedback for that page (`harness revise` reads only the last
  feedback).
- A regenerate job never publishes. The Shopify page does not change.

### Publish new version (a page that is not in a test)

The button shows only when the page has a Shopify page from an earlier
`harness publish` and was regenerated after that publish. Cycle 80: for a live
page that no test or redirect points at, the site shows **Update live page**
instead (see below), because the publish lock refuses this job for a live page. A confirm page
comes first. The `publish_page` job then does: approve (as the logged-in
reviewer), packet stamp `ship`, and `harness publish --update`. The page
stays live, or stays a draft, as it was (read from the run history; if the
history does not say, the site refuses and you publish from the command
line).

### Post live (unlinked) -- cycle 80

Owner, 2026-10-06: "have a button on each ad to do what we just did, post to a
live link, but not link it to any ad yet". **Post live** on a generation card
or on the generation page opens a dialog with the proposed address
`<site_host>/pages/lp-<topic>-<model>` (from the display line or headline and
the product; `-2`, `-3` when taken). You can edit the slug. It must start with
`lp-`, use only lower-case letters, digits and single hyphens (80 characters at
most), and not use a word of the tenant's `emf_terms`; the site also refuses a
handle that another generation or a queued post already uses.

The `post_live` job (harness/postlive.py, also `harness post-live <run> --page
<page> --handle lp-x --by <email>`) does what was done by hand for the five
runs of 2026-10-06:

1. checks that the reviewer is in `tenant.yaml` `reviewers`, and that the page
   is not an A/B/C variant and has no redirect;
2. `approve` as that reviewer and packet stamp `ship`;
3. asks Shopify whether a page (live or hidden) already has the handle
   (`GET pages.json?handle=`) -- Shopify would otherwise create `<handle>-1`;
4. publishes LIVE at that handle, with no redirect, no SEO-hidden metafield,
   no beacon and no split page;
5. records `published_pages[page]` with `unlinked: true`, `live: true`, `by`,
   `at`, `handle`, `url`, `page_id`.

A page that is a hidden draft on Shopify already (for example after
**Unpublish**) is switched live in place at its own handle.

**Update live page** (`update_live` job): approve + stamp + `harness publish
--update --live` of the same page id; the record keeps the first poster and
adds `updated_by` / `updated_at`. **Unpublish** (`unpublish_live` job): a PUT
of only `published: false` to the page id (the body stays); the record gets
`live: false`, `unpublished_by` / `unpublished_at`, and the history a
`live=False unpublished` line. Both have a confirm step and show only for a
live page that no test or redirect points at -- that includes the five pages
published by hand on 2026-10-06.

**The lock stays on.** `publish_locked: true` still makes every Shopify path
use `LockedShopifyPublisher`: A/B/C publish and split pages, auto publish, the
**Publish new version** job of a live page, redirects and a plain `harness
publish --live` are all refused. The one exception is
`LockedShopifyPublisher(unlinked_live=True)`, which only `cmd_publish` builds,
and only when harness/postlive.py calls it (`harness publish` has no flag for
it). With that flag a live create needs an explicit handle, a live update needs
a page id the run owns, and `create_redirect` is still refused.

### Linked-ad icon -- cycle 83

"Live" means only that the Shopify page is published. A page that is the
current destination of a Meta ad also shows a link icon next to its status
pill, on the generation cards and on the generation page. The icon is red when
at least one ACTIVE ad points at the page and grey when only paused (or other
non-ACTIVE) ads do; its tooltip names each ad and its status ("Destination of
Meta ad <name> (<status>)", one line per ad). The Details card lists "Ads
pointing here" with each ad's name, status and the time it was last read from
Meta, or "none" for a live page no ad points at. An ad points at a page when
its `destination_url` in the inbox's ad.json equals the page's live URL, or the
test's split link for an A/B/C variant, after normalising both (http and https
alike, no `www.`, no query string or fragment, no trailing slash, path
percent-decoded; `postlive.linked_ads`). The data is only as fresh as the last
`harness meta refresh-links` (in the meta-pull cron, docs/META-INGEST.md); an ad
never refreshed shows its pull time. The site and the harness only read from
Meta; nothing here attaches a page to an ad.

### Replace live variant (a page that is a variant of a live A/B/C test)

The button shows only when the page was regenerated after the variant was
published. A confirm page comes first and says what happens to the numbers.
The `replace_variant` job then does: approve, stamp `ship`,
`harness publish --update` of the variant's Shopify page (same handle, SEO
hidden, beacon), and then resets the variant's statistics.

How the statistics reset works (`abtest.reset_variant_stats`,
`abevents.archive_variant`): the variant's events in `events.sqlite` move to
the key `<key>@<n>` (for example `A@1`). The test's results count only the
current key, so the variant starts again at 0 views and 0 CTA clicks. The
other variants keep their numbers. The old events are not deleted: the test
record lists them under the variant's `replacements` (with time and
reviewer), and the build library's pooled statistics still count them for
that build. A returning visitor counts again on the new page (the old rows
no longer block the unique key). The beacon receiver never accepts an
archived key.

Why this and not a new variant key: the keys A/B/C are in the split script's
cookie, the beacon script, and order attribution. A new key would change all
of them. Moving the old rows changes nothing a visitor sees.

### Upload an ad

- Fields: ad name (required, up to 200 characters), one media file, and the
  optional primary text, headline, description, and call to action.
- Media: mp4 or mov video, or jpg, png, or webp image, up to 500 MB. The
  site reads the type from the first bytes of the file. The extension must
  also be one of these and agree with the bytes. The file streams to disk.
- The file name that the browser sends is never a path: the site saves the
  file as `meta_inbox/up-<8 characters>/upload-up-<8 characters>.<type>`.
- The site writes an inbox item in the same format as a Meta pull
  (`source: "upload"`, `uploaded_by`), sets it to `queued`, and queues a
  `create_test` job. The job page shows the progress.
- The `create_test` job runs `harness abtest create` on the media with the
  ad name (`harness/abtest_inbox.py`). The ad copy goes to the ad brief
  (cycle 68 sidecar rule). When `abtest.auto_publish` is true (PEAK: true),
  the job then runs `harness abtest publish` and the job page and the Ads
  page show the split link. Paste it into the ad in Ads Manager.
- If the daily budget cap stops a build, the test stays `queued` and the
  inbox item stays `building`. The next `harness abtest from-inbox` run
  resumes it.

### Library -- cycle 86

The Library page (`/library`, nav: Review > Library) shows every version of
page the harness can generate, rendered once as a reference, so the team can
see the options and pick one to generate from. A note at the top says:
"Reference renders use a canned demo page and the tenant's brand; copy is not
real."

**What a version is.** A cartridge x one of its looks (`harness/looks.py`
`CARTRIDGE_LOOKS`: landing `open|proof`, longform `open|classic`, product-page
`pdp|classic`, listicle `open`) and, for the listicle, x its first screen
(`face|story|display`, harness/first_screen.py) x its copy style
(`reasons|mistakes|questions|myths|tested`). A cartridge with no looks
(article, comparison, quiz) is one version. That is 24 versions today (15 of
them listicles). A tenant-level imported look, a folder
`tenants/<t>/looks/<cartridge>/<name>/` with a `template.html` and a
`library.json` `{name, title, summary, source, added_at}`, is listed
automatically as one more version of that cartridge (a name the harness
already uses for its own look is ignored). It is rendered when the harness can
resolve that look name; until then its card shows the library.json title and
summary and says it cannot be rendered yet.

**The build.** `harness library build --tenant <t> [--only landing,...]
[--force]` (harness/library.py) renders each version with NO model call: the
cartridge's canned page from `evals/fake_run.py` goes through the real
pipeline with the tenant's brand (brand/base.css, logo, photo library when
present) and the facts pack the fake run builds, from the demo text ad
`fixtures/founder-warranty-demo.txt`. The runs happen in a scratch folder (out/,
runs/ and evals/ point there; nothing reaches the spend ledger or the Ads
page), with reviewer notifications off, one listicle draft (no Jev call), and
every network connect in the process blocked. Each version writes
`tenants/<t>/library/<cartridge>/<look>[/<first screen>/<style>]/`
(`default` as the look of a cartridge with no looks): `index.html` + `assets/`,
a self-contained `review.html` (the review builder `harness review` uses),
`meta.json` (cartridge, look, first_screen, style, title, summary from
cartridge.md's Purpose plus the look's notes, audience_temperature,
word_range, built_at, sha, and `arm`, the A/B/C build string) and, when a
`chromium`, `google-chrome` or `chromium-browser` binary is on PATH, a 390x844
`thumb.png` at scale 1. Without a browser there is no thumbnail and the card is
a text card; the build never fails for that. A version whose sha (its cartridge
template, look template, cartridge.md and schema.json) has not changed is
skipped unless `--force`. Exit 1 when a version failed to render.

Not renderable offline: the listicle's `face` first screen needs a still from
the ad, and the demo ad is text, so its five versions show a text card with
that reason (the "Generate with this" button still works: a real ad with a
usable still renders it).

**Cards.** Grouped by cartridge (a row of jump links at the top). Each card:
the thumbnail (or a text card), the look / first screen / style chips, the
title, the summary, the audience temperature and word range, **Open reference**
(the version's `review.html`, in a new tab, from the frameable route) and
**Generate with this**: it opens `/upload` with the cartridge, look, hero style
and style pre-selected (shown in a "Page version" box, sent as hidden fields).
The upload checks the combination (`abtest.parse_arm`) and keeps it on the inbox
item as `pinned_arm`; the `create_test` job and `harness abtest from-inbox`
build **variant A** as that version, and B and C are the usual ranked picks from
the test library (`abtest.create_test(pinned_arm=...)`). An item whose pinned
version the harness no longer has fails with a reason.

**Rebuild.** **Rebuild library** (login + form token, like every POST) queues
one `library_build` job with `--force` (a second press while it waits opens the
same job); the worker runs it. `harness serve` queues a non-forced build at
startup when `tenants/<t>/library/` does not exist; startup does not wait for
it.

Run it on a server: `cd ~/advertorial && .venv/bin/harness library build
--tenant peak-saunas` (about 15 seconds without thumbnails; add a few seconds
for each thumbnail when Chromium is installed).

### Security

- Every route except `POST /e` needs the login (HTTP basic auth against
  `tenant.yaml` `reviewers` and `REVIEW_PASSWORD`, or Cloudflare Access
  headers when `REVIEW_TRUST_CF_ACCESS` is on). A person who is not in
  `reviewers` gets 403.
- Every POST of the site carries a form token: an HMAC of the reviewer's
  email with a key made from `REVIEW_PASSWORD`. A POST without the correct
  token gets 403. This is in addition to the Origin/Referer check that
  `serve.py` does on every POST. (Basic auth has no session, so the token
  comes from the login. A new `REVIEW_PASSWORD` makes old tokens fail: reload
  the page.)
- Feedback: 10 posts a minute for each reviewer. Uploads: 6 in 10 minutes
  for each reviewer. Over the limit: 429.
- All text from people or from disk is HTML-escaped. Responses have
  `X-Content-Type-Options: nosniff`; an uploaded image is served only as its
  detected image type.
- The audit log (`jobs.sqlite`, table `audit`, page `/audit`) records the
  email and time of each feedback, upload, publish request, replace request,
  and each finished publish and replace.

## 2. Operator steps (not done in cycle 69)

Do these in this order. None of them is done yet.

### 2.1 Deploy the code

Merge `cycle69/listicle-site` into master in `~/advertorial` when no
production job is running, then restart the review app so that it loads the
new routes:

```bash
systemctl --user restart harness-review@peak-saunas.service
```

### 2.2 Install and start the worker

`crons/install.sh` renders `crons/worker.service` as
`~/.config/systemd/user/harness-worker@<tenant>.service` (it does not start
it). Then:

```bash
cd ~/advertorial
crons/install.sh peak-saunas            # renders every unit; starts none
systemctl --user daemon-reload
systemctl --user enable --now harness-worker@peak-saunas.service
systemctl --user status harness-worker@peak-saunas.service
journalctl --user -u harness-worker@peak-saunas.service -n 50
```

Without the worker, jobs stay `queued`. For a one-time run of the queue:
`.venv/bin/harness worker --tenant peak-saunas --once`.

The worker stops cleanly on SIGTERM: it finishes the job in hand and then
exits (`TimeoutStopSec=900`). If systemd kills it, the next start marks that
job `failed` with a reason, and you submit it again.

### 2.3 DNS

In the Cloudflare zone `peaksaunasteam.com`:

```
A   listicle.peaksaunasteam.com   167.233.29.4   DNS only (grey cloud)
```

DNS only, like the other names in this zone: Caddy gets the certificate, and
the app sees the real visitor IP for the `/e` rate limit.

### 2.4 Caddy

The block to add is `deploy/caddy-listicle-vhost.txt` (checked with
`caddy adapt`; not applied).

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-listicle-$(date +%Y%m%d-%H%M%S)
sudo sh -c 'cat ~deploy/advertorial/deploy/caddy-listicle-vhost.txt >> /etc/caddy/Caddyfile'
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
curl -sI https://listicle.peaksaunasteam.com/ | head -5      # expect 401 and X-Robots-Tag
```

If `caddy validate` fails, put the backup back
(`sudo cp /etc/caddy/Caddyfile.bak-listicle-<stamp> /etc/caddy/Caddyfile`)
and do not reload.

What the block does: `X-Robots-Tag: noindex, nofollow`; request body up to
520 MB; proxy to `127.0.0.1:4870`; removes a visitor's own
`CF-Connecting-IP` (there is no Cloudflare proxy in front, and the app trusts
that header from loopback); no proxy read or write timeout, so a long upload
does not stop.

### 2.5 Check

1. `https://listicle.peaksaunasteam.com/` asks for a login. A reviewer
   email and `REVIEW_PASSWORD` open the Ads page.
2. `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'User-Agent: Mozilla/5.0' --data '{}' https://listicle.peaksaunasteam.com/e`
   prints 400 (the beacon route is public and refuses a bad event).
3. Upload a small image. The job page goes queued, running, done. With
   `auto_publish` on, it shows the split link.
4. After the first live test, do the three checks in docs/ABTEST.md
   section 2.

### 2.6 Who can log in

Only the people in `tenants/peak-saunas/tenant.yaml` `reviewers` (now
Michael and Caleb). Add each team member there (name, email, role) before
you give them the link. They all use the same `REVIEW_PASSWORD`.

## 3. Meta ads to tests: `harness abtest from-inbox`

```bash
.venv/bin/harness abtest from-inbox --tenant peak-saunas [--limit N] [--by <reviewer email>]
```

- It works on inbox items in this order: tests the daily cap stopped in the
  middle of a build (resumed), items the cap left `queued`, then `new` items
  (oldest first).
- Before each item it checks the daily cap (today's spend plus reservations
  plus one more run's reservation). When there is no room, it stops and sets
  the rest to `queued` with the reason `budget cap`. The next run (the next
  day) builds them.
- A finished item is `tested` (with the test id and, with `auto_publish`,
  the split link in its reason) or `failed` (with a reason).
- `--by` is the reviewer that auto publishes are recorded under. The default
  is the first `reviewers` entry with role `primary`.
- It holds the tenant's build lock, so it never builds at the same time as a
  worker job. If a job is running, it skips this run (exit 0).
- `crons/meta-pull.sh` runs it after `harness meta pull`, also when the pull
  failed (the Meta token is not installed yet).

## 4. Data

| Path | What |
|---|---|
| `tenants/<t>/jobs/jobs.sqlite` | The job queue (`jobs`) and the audit log (`audit`). WAL mode. Gitignored. |
| `tenants/<t>/jobs/worker.lock`, `build.lock` | One worker for each tenant; one build at a time. |
| `tenants/<t>/meta_inbox/up-*/` | Uploaded ads (`ad.json` + media). |
| `tenants/<t>/runs/thumb-cache/` | Page thumbnails for the Ads page. |
| `tenants/<t>/library/` | Cycle 86: the Library's reference renders (`harness library build`). Gitignored. |
| `tenants/<t>/looks/<cartridge>/<name>/` | Cycle 86: tenant-level imported looks the Library lists (template.html + library.json). |

Job states: `queued`, `running`, `done`, `failed` (always with a reason).
Job types: `regenerate`, `create_test`, `publish_page`, `replace_variant`,
`post_live`, `update_live`, `unpublish_live`, `library_build` (cycle 86).
