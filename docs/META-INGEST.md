# Meta ad ingest (cycle 68)

`harness meta pull` copies every new ad in the tenant's Meta ad account into
`tenants/<tenant>/meta_inbox/`, so the next stage can build an A/B/C
landing-page test from it. "New" means created on or after
`meta.ingest_since` in `tenant.yaml` (PEAK: 2026-09-23, the day the owner
switched it on).

The harness only READS from Meta. The token needs `ads_read` and nothing
else. The harness never creates, edits, pauses, or deletes an ad.

## 1. Make a system-user token (once)

Use a **system-user** token, not your own user token:

- A user token lasts 60 days at most (long-lived), then the pull stops until
  someone makes a new one. A system-user token can be set to never expire.
- A system user belongs to the business, not to a person, so the token does
  not stop working when someone leaves or changes their password.

Steps, in Meta Business Settings (business.facebook.com/settings) for the
business that owns ad account `act_963439094733982`:

1. **Accounts > Apps**: you need an app of type Business that has the
   Marketing API product added. Use an existing one or make a new one in
   developers.facebook.com. The app does not need App Review for `ads_read`
   on the business's own ad account.
2. **Users > System users > Add**: name it e.g. `harness-ingest`, role
   *Employee* (least access).
3. On that system user, **Assign assets**:
   - **Ad accounts**: `act_963439094733982`, access *View performance*
     (read only).
   - **Pages**: the Facebook Page(s) the ads run from, read access. Without
     this, Meta may leave out the `source` of a video that the Page owns and
     the pull marks that ad `failed` with a reason that says so.
   - **Apps**: the app from step 1.
4. **Generate new token**: choose the app, token expiration **Never**, and
   tick **ads_read** only. Copy the token. Meta shows it only once.

## 2. Put the token in the tenant's .env (hidden prompt)

The token goes in `tenants/peak-saunas/.env` as `META_ACCESS_TOKEN`. Never
paste it into a command line (shell history) or into `tenant.yaml` (git).
On the server:

```bash
cd ~/advertorial/tenants/peak-saunas
grep -c '^META_ACCESS_TOKEN=' .env || true      # must print 0; if 1, edit that line instead
read -rs -p "META_ACCESS_TOKEN: " T; echo
printf 'META_ACCESS_TOKEN=%s\n' "$T" >> .env; unset T
chmod 600 .env
```

`read -s` does not echo the token, and `printf` is a shell builtin, so the
token is not in the process list either. The variable name comes from
`meta.token_env` in `tenant.yaml`.

## 3. Check, then pull

```bash
cd ~/advertorial
.venv/bin/harness meta check --tenant peak-saunas
```

`check` makes the only two calls that do not pull anything: `GET /me` and
`GET /act_963439094733982?fields=name,account_status`. It prints who the
token acts as and the account's name and status, never the token. With no
token it says so and makes no call. With an expired or wrong token it says
"invalid or expired". Exit code 1 on any failure.

```bash
.venv/bin/harness meta pull --tenant peak-saunas --dry-run   # list only, writes nothing
.venv/bin/harness meta pull --tenant peak-saunas             # download
.venv/bin/harness meta inbox --tenant peak-saunas            # table of items and states
```

`pull` options:

| option | what it does |
|---|---|
| `--since 2026-09-23` | use this cutoff instead of `meta.ingest_since` (00:00 UTC on that date) |
| `--limit N` | ingest at most N ads this run |
| `--refresh` | pull ads already in the inbox again (metadata and media) |
| `--dry-run` | list what would be ingested; no download, no write |

An ad already in the inbox is skipped, except one whose last pull failed
(for example, a CDN error). That one is tried again on the next pull, so a
cron run heals itself. Exit code 0 when the pull ran, even when an item
failed; each failure is on its item with a reason. Exit code 1 when the
config, the token, or the ad listing fails.

## 4. What lands in the inbox

```
tenants/peak-saunas/meta_inbox/<ad_id>/
  ad.json
  meta-<ad_id>.mp4     # or .mov / .jpg / .png / .webp
```

`ad.json` (normalized fields first, Meta's raw response under `meta`):

| field | from |
|---|---|
| `ad_id`, `ad_name`, `created_time`, `effective_status` | the ad |
| `adset`, `campaign` | `{id, name}` |
| `creative_id`, `creative_name`, `object_type` | the creative |
| `primary_text` | `link_data.message`, `video_data.message`, `body`, or the first `asset_feed_spec.bodies` |
| `headline` | `link_data.name`, `video_data.title`, `title`, or the first `asset_feed_spec.titles` |
| `description` | `link_data.description`, `video_data.link_description`, or the first `asset_feed_spec.descriptions` |
| `cta` | the call-to-action type, e.g. `SHOP_NOW` |
| `destination_url` | `link_data.link`, `call_to_action.value.link`, `link_url`, or the first `asset_feed_spec.link_urls` |
| `url_tags`, `thumbnail_url` | the creative |
| `media_type`, `media_file`, `media_source` | `video` / `image`, the file name, and which part of the creative it came from |
| `media_candidates` | every video and image in the creative (all carousel cards, all asset-feed assets) |
| `video` / `image` | length, title, picture / hash, width, height |
| `state`, `reason`, `history`, `pulled_at`, `updated_at` | inbox lifecycle |

Media choice: the first video anywhere in the creative (its own video, the
story spec, a carousel card, or `asset_feed_spec.videos`); if there is no
video, the first image. A creative with neither (for example a catalog ad
built from a product feed) is `skipped` with a reason.

Downloads stream to disk with a 500 MB cap and a content-type check (only
the video and image types `harness run` reads). A refused download leaves
no file behind. Downloads go to Meta's CDN with no token.

### States

```
new -> queued -> building -> tested
  \        \          \
   -> skipped  -> failed   -> failed
```

- `new`: pulled; media on disk.
- `queued`, `building`, `tested`: owned by the next stage (the A/B/C test
  build). A `--refresh` keeps these states and only adds a history line.
- `failed`: with a reason. A pull failure is tried again on the next pull.
- `skipped`: with a reason (no media).

Code that moves an item calls `harness.meta_ingest.Inbox.set_state(ad_id,
state, reason)`, which refuses a move the lifecycle does not allow.

### Build pages from an item

```bash
.venv/bin/harness run tenants/peak-saunas/meta_inbox/<ad_id>/meta-<ad_id>.mp4
```

`harness run` (and `harness ingest`) reads the `ad.json` next to the media
file when its `media_file` names that file, and sends `primary_text`,
`headline`, `description`, and `cta` to the ad-brief step as `ad_copy`.
That matters most for a still ad, where the offer is often only in the copy.
The ad copy is brand copy: it never goes into `transcript_or_text` or
`speaker_experience`, so the quote-fidelity gate still checks speaker lines
against the transcript only.

## 5. Cron

`crons/meta-pull.sh [tenant]` runs `harness meta pull`, appends to
`tenants/<tenant>/runs/meta-pull.log`, and skips a run if the last one is
still going (flock). It is not installed. When the token is in place and
`harness meta check` passes, add this line with `crontab -e` as the deploy
user (every hour at :15):

```
15 * * * * /home/deploy/advertorial/crons/meta-pull.sh peak-saunas
```

## Graph API details

- Version: `v24.0` (`meta.graph_api_version`). It was the newest version
  this code was written against (released October 2025). Meta supports a
  version for about two years, so v21.0 (October 2024) is close to its end.
  When Meta retires a version, the calls fail with a "deprecated version"
  error: set a newer version in `tenant.yaml` and run `harness meta check`.
- Host: `https://graph.facebook.com/<version>/`. Token in the
  `Authorization: Bearer` header only. If Meta echoes an `access_token`
  into a `paging.next` URL, it is removed before the call; a `paging.next`
  on any other host is refused.
- Calls (all GET):
  - `me?fields=id,name` and `act_<id>?fields=name,account_status` (check)
  - `act_<id>/ads?fields=<below>&effective_status=[...]&updated_since=<unix>&limit=100`, then `paging.next`
  - `<video_id>?fields=source,length,title,picture`
  - `act_<id>/adimages?hashes=["<hash>"]&fields=hash,url,width,height,name`
- Ad fields: `id,name,created_time,effective_status,adset{id,name},campaign{id,name},creative{id,name,title,body,object_type,video_id,image_url,image_hash,thumbnail_url,object_story_spec,asset_feed_spec,url_tags,call_to_action_type,link_url}`
- Statuses asked for: ACTIVE, PAUSED, IN_PROCESS, PENDING_REVIEW,
  PREAPPROVED, CAMPAIGN_PAUSED, ADSET_PAUSED, WITH_ISSUES. A brand-new ad
  is often PENDING_REVIEW or IN_PROCESS first. DELETED, ARCHIVED, and
  DISAPPROVED are never ingested.
- `updated_since` is a server-side superset (an ad created after the cutoff
  was also updated after it); the `created_time` cutoff is applied here.
- Retries: HTTP 429 and 5xx, network errors, `is_transient`, and the rate
  limit codes 4, 17, 32, 613, and 80000-80014 are retried up to 4 times with
  backoff (2, 4, 8, 16 s). Error 190/102 (token) and 10/200-299 (permission)
  stop at once with a message that names this file.

## Not verified without a live token

The tests use hand-written responses in Meta's documented shapes. These
points can only be confirmed with the real token (run `check`, then
`pull --dry-run`, then one `pull --limit 1`):

- that every field in the ad field list is accepted on v24.0 (Meta rejects
  the whole call if one field name is wrong; `link_url` is the least certain);
- that the `effective_status` and `updated_since` parameters filter as
  documented;
- that the system user can read the video `source` of Page-owned videos
  (needs the Page asset, step 1.3);
- that `Authorization: Bearer` is accepted for this token type (it is for
  Graph API tokens in general);
- the real content types Meta's CDN sends for video and image downloads.
