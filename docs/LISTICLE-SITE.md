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
| `/` | Every ad, newest first, 20 for each page, with a search by ad name. For each ad: status, created date, the 3 variants (thumbnail, build name, views, CTA clicks, CTR, P(best) when the test is live or finished), the split link with a Copy button, and today's model spend against the daily cap. |
| `/gen/<run>/<page>` | One generation: the review render (desktop or mobile width), the versions, the feedback form, the status of the regenerate job, and the old version next to the new one. Also the **Publish new version** or **Replace live variant** button. |
| `/upload` | The upload form. |
| `/jobs`, `/job/<id>` | The job queue and one job (state, reason, result, split link). |
| `/audit` | Who did what, and when. |
| `/runs` | The old run list (it was at `/` before cycle 69). |
| `/e` | The A/B/C beacon receiver (cycle 67). The only route without a login. |

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
`harness publish` and was regenerated after that publish. A confirm page
comes first. The `publish_page` job then does: approve (as the logged-in
reviewer), packet stamp `ship`, and `harness publish --update`. The page
stays live, or stays a draft, as it was (read from the run history; if the
history does not say, the site refuses and you publish from the command
line).

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
