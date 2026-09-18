# Publishing (cycle 20)

A run no longer ends at `REVIEW.md`. Every run now carries a `state.json`
(run states, approval) and a `packet.json` (the ship-stamp gate), and
`harness publish` can hand a cartridge's page to a real storefront through a
publisher adapter -- or, with no credentials at all, export it as a
self-contained folder for manual upload.

## States

`state.json`, one per run directory, next to `REVIEW.md`:

```json
{
  "run_id": "20260910-1200-hidden-costs-v2",
  "state": "needs_review",
  "pages": {"article": "needs_review", "product-page": "approved"},
  "history": [
    {"state": "generated", "by": "system", "at": "...", "note": "run started"},
    {"state": "needs_review", "by": "system", "at": "...", "note": "run passed the claims gate"},
    {"state": "approved", "by": "michael@peaksaunas.com", "at": "...", "note": "pages=product-page"}
  ]
}
```

States: `generated` -> `needs_review` -> `approved` -> `published`, with
`rejected` reachable from `needs_review`. Every page in the run has its own
state; the run-level `state` is `approved` only once every page is (a
partial approval leaves the run `needs_review`). A run that STOPped at the
claims gate or a budget cap never leaves `generated` -- there is no page to
review.

`harness run` (and `harness workflow run ad-to-pages`) writes `state.json`
at the start of the run (harness/pipeline.py's `prepare_run`, state
`generated`) and advances it to `needs_review` right after `render_pages`
(the new `review_notify` stage) once the run has passed every gate --
`harness run` always ends in `needs_review` when it PASSes, under either
`ad_overclaim_policy`.

## Approval

```
harness approve <run-dir> --by <email> [--pages article,longform] [--note "..."]
harness reject <run-dir> --by <email> --note "..."
```

`--by` must match an entry in the tenant's `tenant.yaml` `reviewers` list
(`role: primary | backup`, informational only). An email not on that list is
refused with a one-line message and exit 1 -- no traceback. `approve` with
no `--pages` approves every page in the run; `--pages` approves only the
named ones, so a run with a mixed verdict (one page ready, one not) can move
forward without waiting on the other. Every approval also appends one line
to `tenants/<tenant>/evals/approvals.jsonl` (same append-only pattern as
`evals/scores.jsonl`). `reject` always rejects the whole run.

## The packet stamp

```
harness packet <run-dir> --stamp ship|redo|kill --by <email> [--note "..."]
```

Writes `packet.json`, the second gate `harness publish` checks. Every new
run starts at `"BOT DRAFT · NOT SENT"` -- the same draft-object convention
`tenants/peak-saunas/docs/PACKET-DRAFT.md` has always used. `harness
publish` refuses unless the stamp is exactly `"ship"`.

## Publishing

```
harness publish <run-dir> --page <cartridge> [--live]
harness publish <run-dir> --page <cartridge> --dry-run
```

`harness publish` refuses unless **both**:

1. `state.json`'s `pages.<cartridge>` is `"approved"`
2. `packet.json`'s `stamp` is `"ship"`

Each refusal prints one clear line (the exact command to run next) and exits
1 -- never a traceback. Default publish is **unpublished** (a draft page);
only `--live` creates/updates a live page. `--dry-run` skips both gates
entirely -- it only validates credentials and the page body (for the
Shopify adapter, a `GET` on the shop endpoint) and makes no other call; it
never creates anything, so it can run before a run is even approved.

Which adapter runs is `tenant.yaml`'s `publisher: shopify|export` key
(`harness/publishers/`):

- **`export`** (the default for every new tenant): writes a self-contained
  folder -- `index.html`, `shopify-body.html`, an `assets/` folder, and a
  `README.md` with manual-upload steps -- under `<run-dir>/<cartridge>/export/`.
  No credentials needed; this is also what `harness publish` falls back to
  behaving like the moment a tenant hasn't set `publisher: shopify` yet.
- **`shopify`**: calls the Shopify Admin API (REST for pages/redirects,
  GraphQL for file uploads -- `2024-10`). Needs `SHOPIFY_STORE` (the
  storefront's `*.myshopify.com` admin domain) and `SHOPIFY_TOKEN` (an
  Admin API access token) in the tenant's `.env`. Required scopes:
  **`write_content`, `write_files`, `read_content`**. Neither variable is
  set for `peak-saunas` yet -- every `publish` call fails closed with a
  one-line "no SHOPIFY_STORE / SHOPIFY_TOKEN configured" message and makes
  no network call at all.

  Images: Shopify has no REST endpoint for a file's bytes, so each asset in
  `shopify-body.assets.json`'s manifest is uploaded through the GraphQL
  staged-upload flow -- `stagedUploadsCreate` (a one-time signed upload
  URL), a `multipart/form-data` POST of the raw bytes straight to that URL
  (no Admin API host, no token), `fileCreate` (adopts the upload as a real
  Shopify file), then polling `node(id: ...)` until Shopify finishes
  processing it (`fileStatus: READY` with a non-empty `image.url`, up to 30
  seconds). Every `src="assets/..."` in the body is then rewritten to the
  uploaded CDN URL -- Shopify's own returned URL, never one this adapter
  constructs -- before the page is created.

  Page create: `POST /admin/api/2024-10/pages.json` with `body_html`;
  `published: false` unless `--live`.

### The storefront cache trap

`tenants/peak-saunas/reference/peak-listicle-lp/README.md`: "The storefront
serves page updates from several cache epochs. Verify with >= 8 pulls, not
one." `--live` publish confirms it: after a live publish, `harness publish`
fetches the new page's storefront URL 8 times, 2 seconds apart, and reports
how many pulls returned the new body. `--live` is the only path that ever
does this -- a draft publish has nothing live to verify yet.

## Notifications

`harness/notify.py` sends on: a run reaching `needs_review` (tenant, run id,
input name, pages, gate-history summary, the "AD CLAIMS NOT REPEATED" count,
every review file path, and the exact `harness approve` command to run), on
`approve`, and on `publish` (with the URL). Two channels, both optional and
both fail closed -- a channel that's off, or a missing credential, just logs
`"notification skipped: no channel configured"` and never blocks a run or a
command:

- **Slack** (`tenant.yaml` `notifications.slack: true`): an incoming webhook,
  `SLACK_WEBHOOK_URL` from the tenant's `.env`.
- **Email** (`tenant.yaml` `notifications.email: [addr, ...]`): plain-text
  SMTP, `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`/`NOTIFY_FROM` from
  the tenant's `.env`.

No secret is ever put into a message body or printed. An ad claim flagged as
an overclaim is truncated to 120 characters before it can appear in any
message -- a notification can never repeat a full flagged claim verbatim.

`peak-saunas` has `notifications.slack: false` and `notifications.email: []`,
and no `SLACK_WEBHOOK_URL` or SMTP credentials are set -- every notification
logs a skip; nothing has been posted to a real Slack channel.

## Weekly backlog digest

```
harness digest needs-review --tenant <t> [--days 3]
```

Lists every run still `needs_review` whose `state.json` history shows it
entered that state more than `--days` days ago -- so a run nobody reviewed
doesn't just silently age out. `crons/needs-review-digest.service`/`.timer`
runs this weekly (Monday 08:15); `workflows/weekly-digest.yaml` documents
the same step as a specification (it has no `stage:` step, so `harness
workflow run weekly-digest` exits 1 with "runs no pipeline stages" -- run
the `harness digest needs-review` command directly, same as `sweep`/`score`).

## Getting from a run to a live page

```
harness run <input> --tenant <t>                                    # -> needs_review
harness approve tenants/<t>/out/<run-id> --by <email> --pages article
harness packet tenants/<t>/out/<run-id> --stamp ship --by <email>
harness publish tenants/<t>/out/<run-id> --page article --dry-run    # optional: check credentials first
harness publish tenants/<t>/out/<run-id> --page article              # draft page
harness publish tenants/<t>/out/<run-id> --page article --live       # live, + 8-pull cache check
```
