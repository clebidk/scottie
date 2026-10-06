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
| `/upload` | The upload form. |
| `/jobs`, `/job/<id>` | The job queue and one job (state, reason, result, split link). |
| `/audit` | Who did what, and when. |
| `/runs` | The old run list (it was at `/` before cycle 69). |
| `/e` | The A/B/C beacon receiver (cycle 67). The only route without a login. |

### Look (cycle 80)

`harness/site_ui.py` holds the shell (top bar with the tenant logo from
`brand/logo-basalt.svg`, navigation, a running-jobs badge), the stylesheet and
the small script (copy, phone / desktop preview, the post-live dialog, the
slug check). White ground, Basalt `#161817` ink, text `#1A1A1A`, muted
`#6B6B6B`, hairlines `rgba(22,24,23,.12)`, red `#702B34` only for live and
attention states; DM Sans for the UI and Poppins for headings (Google Fonts);
4px radius on cards and inputs, pill buttons. No build step. On a phone the
navigation scrolls sideways, the generation cards scroll sideways in each ad,
the dialog is a bottom sheet, and tables become stacked rows. The older
reviewer pages (`/runs`, `/run/<id>`, `/images`) use the same shell. 404 and
500 errors and a wrong form token get a styled page.

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

Job states: `queued`, `running`, `done`, `failed` (always with a reason).
Job types: `regenerate`, `create_test`, `publish_page`, `replace_variant`.
