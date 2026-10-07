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

  **Re-publishing the same page (`--update`, Cycle 40):** by default,
  re-running `harness publish` for a page it already published creates a
  *second* Shopify page (e.g. `listicle-test-1-1`) instead of touching the
  first. `harness publish <run-dir> --page <cartridge> --update` updates the
  page this run already published in place instead -- `PUT
  /admin/api/2024-10/pages/<id>.json` -- using the page id `harness publish`
  recorded in `state.json` the first time it published this page. It
  refuses with a clear message if this run has no stored
  page id for `--page` yet (nothing was ever published, or it went through
  the `export` adapter, which has no page id). Assets are still re-uploaded
  and the body still rewritten, same as any other publish. `--handle`
  together with `--update` is ignored, with a warning -- an update targets
  the page's already-live handle; changing it is out of scope.
  `--redirect-from` is unaffected and still works with `--update`.

### The storefront cache trap

`tenants/peak-saunas/reference/peak-listicle-lp/README.md`: "The storefront
serves page updates from several cache epochs. Verify with >= 8 pulls, not
one." `--live` publish confirms it: after a live publish, `harness publish`
fetches the new page's storefront URL 8 times, 2 seconds apart, and reports
how many pulls returned the new body. `--live` is the only path that ever
does this -- a draft publish has nothing live to verify yet.

## Storefront template (cycle 84)

The export (`harness/page_body.py`, `harness/css_scope.py`) carries all of its
CSS in one body `<style>` block. The store runs an A/B test between two
themes. One theme's default page template prints the body raw; the other
routes it through a section whose fallback strips every `<style>` block, so a
harness page shows its CSS as text to every visitor in that bucket. The fix
is one page template, the same file in both themes:

- **The template**: `deploy/shopify/templates/page.pk-harness.liquid` (theme
  key `templates/page.pk-harness.liquid`). It uses the theme layout (head,
  fonts, analytics and app scripts still load), prints `{{ page.content }}`
  raw (no section, no `.rte` wrapper, no container, no theme page title) and
  hides the theme header, announcement bar and footer with the same
  selectors as `templates/page.pk-regret-lp.liquid`. It refers to no section
  or snippet, so it renders the same in every theme. The body's own
  storefront-fit CSS (`css_scope.STOREFRONT_FIT_CSS`) is page-scoped with
  `:has(.adv-wrap)`; with no container around the page it only resets the
  layout's own wrappers, which it already did in the theme that rendered the
  pages correctly, so nothing in the export changed.
- **The tenant key**: `tenant.yaml` `shopify_publish.template_suffix`
  (`peak-saunas`: `pk-harness`; `_template`: `null`). `harness/cli.py`
  `_make_publisher` passes it to the publisher, so every publish path gets it:
  `harness publish` (create and `--update`), `harness post-live` and the
  listicle site's "Post live" button (post and update), `harness abtest
  publish` (the three variants and the split page). Shopify gets
  `template_suffix` on every page create and on every update that carries a
  title or body. An unpublish (`published: false` only) and a tenant with no
  suffix send exactly the payloads they sent before. `harness publish
  --dry-run` prints `template_suffix` in its report; the export adapter's
  `README.md` tells the operator which theme template to pick.
- **Pages published before the key**: `harness pages set-template --tenant
  <t> [--dry-run]` walks every run's `state.json` `published_pages[*]` that
  has a `page_id`, reads each page from Shopify and PUTs only
  `template_suffix` (never the body, never `published`) where it differs. One
  line per page (`handle: old -> new`, `unchanged`, or `not found`) and a
  summary. `--dry-run` reads and writes nothing. It refuses when the tenant
  has no suffix.
- **Pushing the template**: `deploy/shopify/push_template.py --tenant <t>
  --theme-id <id> [--theme-id <id> ...] [--dry-run | --apply | --rollback]`.
  Default `--dry-run`: GET the asset from each theme, print `absent`,
  `identical` or `different`. `--apply`: PUT the repo file where it is absent
  or different and print the asset's `updated_at`. `--rollback`: DELETE the
  asset only where it equals the repo file (exit 1 when a theme copy differs
  and was left alone). It touches no other asset. Credentials come from the
  tenant `.env` (`SHOPIFY_STORE`, `SHOPIFY_TOKEN`); the token also needs the
  **`read_themes`, `write_themes`** scopes. The theme ids are operator input
  (Online Store -> Themes -> "..." -> Edit code: the id is in the URL); they
  are not in the repo.

Order (the template must exist in both themes before any page names it):

```
.venv/bin/python deploy/shopify/push_template.py --tenant peak-saunas --theme-id <A> --theme-id <B>          # dry run
.venv/bin/python deploy/shopify/push_template.py --tenant peak-saunas --theme-id <A> --theme-id <B> --apply
# tenant.yaml: shopify_publish.template_suffix: pk-harness   (already set for peak-saunas)
harness pages set-template --tenant peak-saunas --dry-run
harness pages set-template --tenant peak-saunas
# check one page in each theme: ?preview_theme_id=<A> and ?preview_theme_id=<B>
```

Rollback: set `shopify_publish.template_suffix: null` (new pages stop naming
the template), then `push_template.py ... --rollback`. A page whose
`template_suffix` names a template the theme does not have falls back to the
theme's default page template (the expected Shopify behaviour; not verified
on this store), which is the state before cycle 84.

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
