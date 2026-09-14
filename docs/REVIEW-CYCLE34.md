# Cycle 34 swarm review

Reviewer pass over the six `cycle34/*` package branches from `docs/SWARM-2026-09-14-cycle34.md`.
Base: `master` @ `3ece609`. Each package was reviewed in its own worktree
(`git worktree add ../advertorial-c34-<pkg> github/cycle34/<pkg>`, branch
`test-<pkg>` merged onto master, own venv, symlinked `models`/`vendor`,
copied `.env` and media fixtures). Every worktree and throwaway branch was
removed after its review; `git worktree list` on `~/advertorial` shows only
the primary checkout, and `git branch` shows no leftover `test-<pkg>`
branches. Nothing was merged into master, nothing pushed to `github` -- all
commands ran over `ssh prod`. Packages were reviewed one at a time, never
concurrently, since prod is a shared box.

Acceptance bar: `docs/CYCLE-33-SWARM-PLAN.md` section 9. Rules: `docs/KIMI-LONG-RUN.md`.
Baseline suite size on master: **1020 passed**, `ruff check .` clean.

## Results table

| Package | Tests | Ruff | Fake-run baseline | Tenant words | Secrets | New network+model calls | Diff | Verdict |
|---|---|---|---|---|---|---|---|---|
| `notify-webhook-https` | 1021 passed | clean | byte-identical (both fixtures) | pass | none | none new | +12/-0 harness, +12/-0 tests, 2 files | **FIX** |
| `publisher-store-guard` | 1031 passed | clean | byte-identical | pass | none | none new (existing Shopify Admin call path extended) | +164/-10, 2 files | **MERGE** |
| `repair-warranty-scope` | 1021 passed | clean | byte-identical | pass | none | none | +48/-3, 2 files | **MERGE** |
| `revise-spend-ledger` | 1023 passed | **1 error** (`F841` unused var, `tests/test_revise.py:279`) | byte-identical | pass | none | none | +211/-100, 2 files | **FIX** |
| `runlog-close-protocol` | 1022 passed | clean | byte-identical | pass | none | none | +228/0 (net; see diff stat below), 3 files | **MERGE** |
| `serve-auth-hardening` | 1025 passed | clean | byte-identical | pass | none | **none** (no JWKS fetch, no network added) | +129/-3, 2 files | **FIX** |

All six packages merge cleanly onto `master` @ `3ece609` with no conflicts (confirmed file-exclusive
per the synthesis doc). "Byte-identical" = `diff -rq` between a fresh `evals.fake_run --baseline-dir`
capture and `evals/baseline/{hidden-costs-v2-transcript,founder-warranty-demo}/*` (manifest.json,
whose run id/date are expected to differ, excluded) produced no output on both fixtures for every
package.

## Findings, by package

### notify-webhook-https -- FIX
- `harness/notify.py`: the https-only check runs before any send is attempted -- `scheme =
  urllib.parse.urlparse(webhook_url).scheme.lower()` and an early `return False` when
  `scheme != "https"` (`harness/notify.py:37-44`), ahead of the request body build (line 45) and
  the `urlopen` call (lines 53-58). The rejection log line names the reason and echoes the bad
  scheme (`harness/notify.py:40-43`).
- Tenant off switch (`tenants/peak-saunas/tenant.yaml:184-186`, `notifications.slack: false`)
  short-circuits at `harness/notify.py:115-117` before `send_slack` is ever called -- disables
  notifications regardless of URL scheme, independent of this branch's change.
- Test coverage: `http://`/`file://` rejection is covered
  (`tests/test_notify.py:159-169`, `test_send_slack_skips_non_https_webhook`). **No test exists
  for a non-Slack https host.**
- **Blocking finding:** the check is scheme-only. `harness/notify.py:37-38` never compares
  `urlparse(webhook_url).hostname` against `hooks.slack.com` or any allowlist. Any `https://` URL
  is accepted -- a misconfigured or hostile `SLACK_WEBHOOK_URL` would still get Peak's review
  text POSTed to it. Needs a hostname pin (e.g. `hostname == "hooks.slack.com"`) plus a test for
  it before this is a clean MERGE.

### publisher-store-guard -- MERGE
- Asset-manifest contract (`docs/IMAGES.md:136-143`, built by `harness/page_body.py`, untouched
  by this diff): every fallback `src` and every `srcset` width variant (JPEG + WebP) a page
  references.
- `rewrite_asset_srcs` coverage (`harness/publishers/shopify.py:117-135`, via
  `_SRC_ASSET_RE`/`_SRCSET_ATTR_RE` at lines 95-96 and `_rewrite_srcset_value` at 99-114):
  plain `src=` rewritten outright; `srcset=` rewritten per-entry preserving width descriptors.
  `<picture><source srcset=...>` uses the same `srcset` attribute, so it's caught by the same
  regex applied over the whole `body_html` string -- no gap. Covered by
  `tests/test_publishers.py:154-172` (`<picture><source>` + `<img srcset>` combo).
- Store guard (`normalize_shopify_store`, `harness/publishers/shopify.py:46-75`, applied at
  `:149-158`): **pattern-only** -- `^[a-z0-9][a-z0-9-]*\.myshopify\.com$` over https, rejecting
  bad scheme, userinfo, ports, non-myshopify hosts (including an SSRF/metadata-IP guard). It does
  **not** cross-check against `tenants/peak-saunas/tenant.yaml`'s `shopify_publish.store_admin_domain`
  (currently `null`, unconfirmed) -- any valid `*.myshopify.com` store passes, not specifically
  Peak's own. Not a regression (no such check existed before) and the tenant field isn't populated
  yet to check against, so flagged as a follow-up, not a blocker.
- Default state stays unpublished: `publish(self, page, *, unpublished=True)` (line 238,
  untouched by this diff) is unaffected.
- No live Shopify in tests: `tests/test_publishers.py` uses `FakeTransport` throughout; the
  credentials-missing tests fail closed before any network call.
- Cycle-31 manifest contract unchanged -- diff touches only `harness/publishers/shopify.py` and
  its tests; `harness/page_body.py` and `docs/IMAGES.md` untouched.

### repair-warranty-scope -- MERGE (locked-topic path, verified live -- see dedicated section below)
- Cycle-13 coverage (`tests/test_repair_loop.py:401-509`): both prose "proof point"/"paragraph"
  mentions and a genuine spec-table row are exercised and still pass on this branch.
- Cycle-16 narrowing (`tests/test_claims.py:1505-1544`, gate-side via
  `_WARRANTY_COVERAGE_ASSERTION_RE`, `harness/claims.py:1168-1170`): separate mechanism from this
  package (gate decides what's flagged; this package decides how a flagged item is repaired),
  untouched, still passing.
- Scoping logic: `_is_warranty_spec_label` (`harness/repair.py:425-437`, called at line 463) --
  true when the sibling `"label"` string equals `vocab.ALLOWED_WARRANTY_SPEC_LABEL` ("Warranty")
  case-insensitively or contains "warrant" case-insensitively. Label-text match only, no
  column-header/table-position check -- a hypothetical "No warranty" label would also match; not
  exercised by any test, minor edge case, not a blocker.
- Genuine spec row rewrite: `harness/repair.py:463-468` -- label+value pair set to the canonical
  values when the label matches. Verified live (see below).
- Prose-only mention no longer rewritten to the pair: `harness/repair.py:470-473` -- non-matching
  labels get only the `value` field rewritten to the flat allowed sentence, preserving the
  original label. New test: `tests/test_repair_loop.py:459`.

### revise-spend-ledger -- FIX
- `run`'s spend contract: `reserve_spend`/`record_spend` (`harness/budget.py:268`, `:297`),
  called from `harness/pipeline.py:190` (reserve, before ingest/write) and three exit points in
  `execute()` (`:439`, `:547`, `:509`/`:560` via `abort_budget_run`).
- `revise.py` wiring: `reserve_spend` at `harness/revise.py:278`, `record_spend` at `:323` inside
  a `finally` (`:279-326`) -- same imported functions, not a parallel reimplementation
  (`harness/revise.py:30`).
- Cuts-only stays free: the `elif applied_cuts:` branch (`harness/revise.py:327-343`) never calls
  either spend function. Covered by `tests/test_revise.py:267-291`.
- Cap refusal is clean: `reserve_spend` sits outside the inner `try:` (`harness/revise.py:278-279`),
  raises `BudgetExceeded` before any ledger write or model-client construction
  (`harness/budget.py:288` vs. the ledger write at `:290-293`). Covered by
  `tests/test_revise.py:236-263`.
- **Blocking finding -- ruff:** `tests/test_revise.py:279`, `F841` unused local variable `page`
  in the new cuts-only spend test. Trivial, one-line fix.
- **Blocking finding -- CLI crash on cap refusal:** `harness/cli.py:287-291` (`cmd_revise`)
  catches only `revise_mod.ReviseError`, not `BudgetExceeded`. Since the cap-refusal path raises
  a raw `BudgetExceeded`, a capped-out tenant's `harness revise` call (CLI or
  `serve.py:553-556`'s background launch) gets an unhandled traceback instead of the clean
  one-line message every other `cmd_revise` error path gets (compare `cli.py:109`/`:182` for
  `cmd_run`/`cmd_ingest`, which do catch `BudgetExceeded`).
- **Blocking finding -- orphaned reservations can't reconcile:** `reconcile_stale_reservations`
  (`harness/budget.py:331-359`) matches on `_run_has_final_state` (`:319-329`), which looks for
  `tenant.runs_dir / f"{run_id}.log"`. Revise's actual RunLog filename is
  `f"{run_id}-revise-{page_name}-v{version}.log"` (`harness/revise.py:258`), while the spend
  ledger's `run_id` is `f"{run_id}__revise__{page_name}__v{version}"` (`:276`) -- different
  strings (dash/`-v` vs. double-underscore/`v`). `_run_has_final_state` can never match a revise
  reservation, so a crash between `reserve_spend` and `record_spend` (skipping the `finally`)
  leaves a reservation that `harness spend reconcile` can never sweep -- it eats into the
  tenant's real daily cap headroom permanently until hand-edited.
- **Gap -- no test for the stated purpose:** the commit's own claim ("closes revise RunLog on
  every unwind path") has no test that injects an exception mid-revise and asserts the RunLog
  file was closed; existing tests only exercise `ReviseError`/`BudgetExceeded`/success. The
  `finally: log.close()` (`harness/revise.py:378`) is correct by inspection but untested for the
  unnamed-exception case.

### runlog-close-protocol -- MERGE
- `execute()` (`harness/pipeline.py`): outer `try/finally`, `finally: if state.log:
  state.log.close()` at `:583-585`, wrapping the PASS path (`:567-582`), `UnknownCartridge`
  (`:541`), `ClaimsGateFailure` (`:544`), and `BudgetExceeded` (`:561`, via `abort_budget_run`).
- `cmd_ingest`/`cmd_brand_import` (`harness/cli.py`): same outer `try/finally` shape --
  `cmd_ingest` closes at `:128` (covers `BudgetExceeded` at `:112-116` and success at `:126`);
  `cmd_brand_import` closes at `:272` (covers `BudgetExceeded` at `:258-262`).
- `abort_budget_run` (`harness/pipeline.py:501-513`): writes the exceeded-budget event, records
  spend if a tenant was given, writes the budget summary, writes `run_result: STOP` via
  `review_md.log_run_result`, then closes the log -- shared by all three call sites.
- **Overlap check with `revise-spend-ledger` -- no risk.** Neither `execute()` nor
  `cmd_ingest`/`cmd_brand_import` call into `harness/revise.py`; `cmd_revise` is its own
  entrypoint with its own `RunLog` instance. `harness/log.py` is untouched by this branch.
  `RunLog.close()`'s idempotency guard (`if self._fh is not None and not self._fh.closed`,
  `harness/log.py:102-106`) is unchanged and was confirmed live via a manual `execute()` repro
  that hit the `BudgetExceeded` path: log file contained the expected lines exactly once despite
  `close()` being called twice (inner `abort_budget_run` + outer `finally`). No double-close, no
  duplicate log lines, no shared file handle between the two packages.
- `run_result`/cost lines confirmed present on the STOP path by the same manual repro (no
  existing test drives `execute()`'s `BudgetExceeded` branch end-to-end -- `test_budget.py` only
  unit-tests `Budget` itself). This branch's own `tests/test_runlog_close.py` covers the pass path
  and an unnamed-exception unwind, but not the `BudgetExceeded` branch specifically -- a gap, not
  a blocker, since the manual repro confirms correct behavior.

### serve-auth-hardening -- FIX (regression found; auth logic itself is sound)
- Trust flag: `REVIEW_TRUST_CF_ACCESS` (`harness/serve.py:417`). When true, the
  `Cf-Access-Authenticated-User-Email` header is **no longer sufficient alone** -- a second
  header, `Cf-Access-Jwt-Assertion`, must also be present and non-empty or the request is
  rejected 401 (`:419-425`).
- **JWT check precision, confirmed exactly as the synthesis doc flagged, and weaker than "decodes
  claims":** the check is presence-only on the raw header string -- no base64 decode, no
  issuer/audience/expiry check, no signature verification, **no JWKS fetch, no network call of
  any kind** (confirmed by both the diff's network-call grep, zero hits in `serve.py`, and the
  code's own docstring at `harness/serve.py:412-415`: "not a full JWT signature check -- no JWKS
  fetch, the harness stays offline-friendly"). A guessed or replayed non-empty string in that
  header satisfies the gate. This is the single most important fact in this package's review:
  **it is not cryptographic verification in any sense**, only "a second header must also be
  present." Full JWKS verification remains backlog item #1, exactly as the synthesis doc states.
- Basic-auth fallback unchanged: `harness/serve.py:429-440` has zero changed lines in the diff.
- CSRF Origin/Referer check is POST-only: `_check_post_origin()` (`harness/serve.py:456`) returns
  early for any non-POST method; a manual GET to a review route confirmed no CSRF interference.
- **Blocking finding -- iframe preview is broken, confirmed live, not inferred.** The
  `app.after_request` hook `_security_headers` (`harness/serve.py:503-509`) sets
  `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` on **every**
  response via `setdefault`, with no carve-out anywhere in the diff or the surrounding file for
  the `page_review` route (`:543`) that `run_detail`'s `<iframe src="{review_url}">`
  (`:349`) embeds. A direct Flask-test-client hit against `/run/<id>/review/article` returned
  `X-Frame-Options: DENY` and the CSP `frame-ancestors 'none'` header on the *review* route itself
  -- `DENY` (unlike `SAMEORIGIN`) blocks framing even same-origin, so a real browser will refuse
  to render the app's own preview iframe. The route's HTML content is otherwise correct (200,
  valid body) -- the break is purely at the browser's frame-rendering layer.
- Test coverage: JWT presence (`tests/test_serve.py::test_cf_access_email_alone_without_jwt_is_401`,
  `test_cf_access_header_trusted_when_enabled`), CSRF POST-only (`test_cross_origin_post_is_refused`,
  `test_same_origin_post_is_allowed`, `test_cross_site_referer_post_is_refused`), framing on
  normal pages (`test_responses_deny_framing`, but it only hits `/`). **No test exists for the
  iframe route's headers**, so the existing `page_review`-hitting tests
  (`test_page_review_serves_the_review_html` and four others) check status/content only and pass
  right through the regression above.
- No secret logging: `harness/serve.py` has no `log.`/`logger.`/`print(` calls at all -- nothing
  newly logs headers, tokens, or emails.

## The one real run: repair-warranty-scope

Worktree: `advertorial-c34-repair-warranty-scope` (`test-repair-warranty-scope`, merged onto
`master` @ `3ece609`, no conflicts). Command: `timeout 600 .venv/bin/harness run
tenants/peak-saunas/fixtures/still-levelup-4x5.png --tenant peak-saunas` (foreground, real
Anthropic key from the tenant's `.env`). Neither Shopify nor Slack was called -- the run log shows
notify skipped ("slack disabled for tenant"), no publish step ran (state stopped at
`needs_review`), and the copied `.env` in this worktree carries only `ANTHROPIC_API_KEY`,
`REVIEW_PASSWORD`, `SLACK_WEBHOOK_URL` (names checked only, no values) -- no Shopify credential
exists in the worktree at all.

Output dir: `tenants/peak-saunas/out/20260914-200511-still-levelup-4x5-dmgh` (inside the worktree).

- **Gate result:** PASS on all three cartridges (product-page, longform, article); run state
  landed at `needs_review`, the normal non-publishing end state.
- **Warranty deterministic fix fired:** yes -- once on `longform` (attempt 1, logged
  `write.longform: deterministic fix applied: warranty sentence`, alongside two unrelated
  financing-sentence fixes) and once on `article` (attempt 1). `product-page` needed none -- its
  spec row was already the exact allowed pair as generated.
- **Exact warranty wording, every page it appears on:**
  - product-page, spec-table row: `Warranty` / *"Limited lifetime warranty (terms by component)."*
  - longform, how-it-works prose: *"Limited lifetime warranty; full terms by component are
    published on the warranty page."*
  - longform, spec-table row: `Warranty` / *"Limited lifetime warranty (terms by component)."*
  - longform, final-CTA line: *"Limited lifetime warranty; full terms by component are published
    on the warranty page."*
  - longform, FAQ answer (not gate-flagged -- no "lifetime" bigram trigger, correctly untouched
    per the cycle-16 gate): *"Limited lifetime warranty; full terms by component are published on
    the warranty page. Coverage varies by part, so it's worth reviewing the specific terms for
    your components before you buy."*
  - article, descriptive prose (correctly left untouched -- no coverage-assertion bigram, cycle-16
    gate never flags it): *"A warranty that backs up the maker's confidence in their build
    quality, covering key components for years to come."*
- **Attempts/repairs:** product-page 1 attempt / 0 repairs. longform 1 attempt / 0 repairs (3
  deterministic fixes applied, then PASS). article 2 attempts / 1 real repair call (attempt 1
  failed on an unrelated claim-id gap about "clinical"-sounding lighting language, not warranty).
- **Cost:** 27,351 of 220,000 token budget; 7 of 14 allowed calls; 147.34s of a 300s wall-clock
  budget; estimated **$0.2830**.

This confirms the package's core claim directly: a genuine spec-table warranty row is rewritten
to the canonical pair, and a value-only warranty mention under a different label (e.g.
"Coverage") is left alone rather than silently duplicated into a second Warranty row.

## Recommended merge order

The synthesis doc says "any order except file ownership" (true -- confirmed no two packages
touch the same file) and "prefer #11 [serve-auth-hardening] before exposing serve on a shared
host." I deviate from a same-order-as-listed merge because **three of six packages are not clean
MERGEs today** -- only `publisher-store-guard`, `repair-warranty-scope`, and
`runlog-close-protocol` pass every check with nothing to change first. Per the acceptance bar
(`docs/CYCLE-33-SWARM-PLAN.md` section 9: "every merged package: suite green, ruff clean..."),
`revise-spend-ledger` cannot merge as-is (ruff is not clean), and `notify-webhook-https` /
`serve-auth-hardening` carry security-relevant gaps that should close before merge, not after.

Recommended order:

1. **`repair-warranty-scope`** -- clean MERGE, locked-topic path already verified against a real
   generation above; ship it first since it's independently verified end-to-end.
2. **`runlog-close-protocol`** -- clean MERGE, no dependency on anything else, confirmed no
   behavioral overlap with `revise-spend-ledger`.
3. **`publisher-store-guard`** -- clean MERGE (the tenant-store-domain gap is a follow-up, not a
   blocker; no existing check regresses).
4. **`revise-spend-ledger`** -- after fixing the `ruff` `F841` (one line), adding a
   `BudgetExceeded` catch to `cmd_revise` in `cli.py` to match `cmd_run`/`cmd_ingest`, and fixing
   the RunLog-filename mismatch that blocks reservation reconciliation (or documenting it as an
   accepted known gap if a quick fix isn't available -- but it should not ship silently).
5. **`notify-webhook-https`** -- after adding a `hooks.slack.com` hostname pin (or an explicit
   Slack-webhook-host allowlist) alongside the existing scheme check, plus a test for a non-Slack
   https host.
6. **`serve-auth-hardening`** -- last, and only after adding a carve-out for the `page_review`
   iframe route (skip the framing headers there, or relax to `SAMEORIGIN`/a `frame-ancestors`
   value that allows the app's own origin) plus a regression test for that route's headers in
   both directions. This one should go last of the six specifically *because* the synthesis doc's
   own instinct to ship it early ("prefer #11 before exposing serve on a shared host") is right
   for the auth logic but wrong for timing here: merging it as-is would break the tool's own
   preview feature the moment `serve` is exposed, which is a worse outcome than a few more days on
   the current CF/basic-auth posture without the extra CSRF/JWT-presence gate.

## Exact merge commands (not run)

```bash
ssh prod
cd /home/deploy/advertorial
git checkout master
git pull --ff-only          # only if master could have moved; it was 3ece609 throughout this review

# 1-3: clean, no fix needed
git merge --no-ff github/cycle34/repair-warranty-scope
git merge --no-ff github/cycle34/runlog-close-protocol
git merge --no-ff github/cycle34/publisher-store-guard

# 4: after the ruff fix + cmd_revise BudgetExceeded catch + reconciliation-filename fix
git merge --no-ff github/cycle34/revise-spend-ledger

# 5: after adding the hooks.slack.com hostname pin + test
git merge --no-ff github/cycle34/notify-webhook-https

# 6: after the page_review iframe header carve-out + regression test
git merge --no-ff github/cycle34/serve-auth-hardening

# then, per docs/CYCLE-33-SWARM-PLAN.md section 9/10:
#   - one real generation per merge group, confirm PASS + financing sentence + byline + zero
#     banned terms + cost logged
#   - append "## Cycle 34" to docs/FIXLOG.md
#   - push the mirror
```
