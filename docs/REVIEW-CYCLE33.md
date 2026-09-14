# Cycle 33 swarm review

Reviewer pass over the six `cycle33/*` package branches from `docs/SWARM-2026-09-14.md`.
Base: `master` @ `715e590`. Each package was reviewed in its own worktree
(`git worktree add ../advertorial-c33-<pkg> github/cycle33/<pkg>`, branch
`test-<pkg>` merged onto master, own venv, symlinked `models`/`vendor`,
copied `.env` and media fixtures). Every worktree and throwaway branch was
removed after its review; `git worktree list` on `~/advertorial` shows only
the primary checkout. Nothing was merged, pushed, or left on the operator's
Mac -- all commands ran over `ssh prod`.

Acceptance bar: `docs/CYCLE-33-SWARM-PLAN.md` section 9. Rules: `docs/KIMI-LONG-RUN.md`.
Baseline suite size on master: **1008 passed**, `ruff check .` clean.

## Results table

| Package | Tests | Ruff | Fake-run baseline | Tenant words | Secrets | New network/model calls | Diff | Verdict |
|---|---|---|---|---|---|---|---|---|
| `warmup-window` | **1 failed, 1013 passed** | clean | byte-identical (both fixtures) | **FAIL** -- `harness/write.py:442` | none | none new (pre-existing `model="claude-sonnet-5"` test kwarg, used 14x already on master) | +186/-11, 4 files | **FIX** |
| `fetch-url-safety` | 1011 passed | clean | byte-identical | pass | none | none (diff hits are `monkeypatch.setattr` test mocks only) | +111/-1, 2 files | **MERGE** |
| `run-log-context-manager` | 1011 passed | clean | byte-identical | pass | none | none | +40/-1, 2 files | **MERGE** |
| `shopify-body-rename` | 1008 passed | clean | byte-identical | pass | none | none | +5/-5, 3 files | **FIX** (doc-accuracy only) |
| `ci-pipeline` | 1008 passed | clean | byte-identical | pass | none (no secrets used in workflow) | n/a (workflow file, no test-suite network) | +23/-0, 1 file | **FIX** |
| `packaging-metadata` | 1008 passed | clean | byte-identical | pass | none | none | +5/-0, 1 file | **FIX** |

"Byte-identical" = `diff -rq` between a fresh `evals.fake_run --baseline-dir` capture and
`evals/baseline/{hidden-costs-v2-transcript,founder-warranty-demo}/*.page.json` (manifest.json,
whose run id/date are expected to differ, excluded) produced no output on both fixtures for
every package.

## Findings, by package

### warmup-window -- FIX (trivial, one-line)
- **Blocking:** `tests/test_tenant.py::test_no_tenant_specific_words_in_the_engine_or_the_cartridges[harness]`
  fails. Cause: `harness/write.py:442`, the docstring of `filter_exemplars_for_warmup`, reads
  *"Cycle 33 critic: **Peak's** live .md exemplars name the brand in the headline/body..."* --
  a tenant word landed in engine source. Everything else in this package (the hard-constraint
  builder, `_warmup_window_words`, `_warmup_brand_terms`) is correctly tenant-neutral, reading
  `tenant.display_name` / `tenant.get("cartridges.article.warmup_window_words")`, never a literal
  company name. Fix is a one-line docstring edit (drop "Peak's", say "Live .md exemplars...").
  This is the only reason the package isn't a clean MERGE.
- `harness/claims.py` (the gate, `find_warmup_violations`) and `warmup_mode` are untouched by
  this diff -- gate strength and semantics are unchanged, confirmed by `git diff --stat` showing
  only `cartridges/article/cartridge.md`, `schema.json`, `harness/write.py`, `tests/test_write.py`.
- Real-run verification (see below): first brand mention lands at word **1267** against the
  600-word gate, more than double the margin, and the fake-baseline stayed byte-identical (the
  fix is prompt-only; `FakeClient` never reads prompts) -- matches the package's own claim.

### fetch-url-safety -- MERGE
- `harness/render.py`: `http_fetch_bytes` now rejects non-http(s) schemes (`ValueError`) and caps
  the response at `HTTP_FETCH_MAX_BYTES = 25 * 1024 * 1024` via chunked reads (raises `ValueError`
  on overflow, response discarded, not buffered past the cap). Tests cover all three paths
  (`tests/test_render.py`, three new tests).
- Confirmed **not** a risk to Drive, Shopify `products.json`, Judge.me, or the review inliner:
  `git grep http_fetch_bytes` on the branch shows only two production call sites, both inside
  `harness/render.py`'s asset-download path (`download_asset`'s default `fetch_url` arg) plus the
  `fetch_url=` kwarg threaded through `harness/pipeline.py`. Drive downloads go through
  `harness/sources/drive.py`'s own `urllib.request.urlopen` calls (hardcoded `https://drive.google.com/...`
  and `https://drive.usercontent.google.com/download`), entirely separate code. Shopify
  `products.json` (`harness/prices.py`) and Judge.me (`harness/sources/judgeme.py`) each have
  their own fetch paths too -- none of the three routes this change could plausibly break are
  actually touched by it.

### run-log-context-manager -- MERGE
- `harness/log.py`: `RunLog.close()` is now idempotent (`if self._fh is not None and not
  self._fh.closed`), and `__enter__`/`__exit__` added (`__exit__` calls `close()`, returns
  `False`). Diff is exactly this plus `tests/test_log.py`.
- Every caller (`pipeline.py`, `revise`, `serve.py`, `workflows.py`) is untouched by the diff --
  zero risk of format drift in the final `run_result` / cost log lines the review site and
  `REVIEW.md` parse, because nothing at those call sites changed. Converting call sites to `with`
  remains backlog (synthesis doc item 11), correctly not attempted here.

### shopify-body-rename -- FIX (doc-accuracy only, not functional)
- `harness/shopify.py` -> `harness/page_body.py` (pure rename, `git diff` shows `0` changed lines
  for the move). `harness/cli.py` updated its two imports accordingly.
- **CLI alias intact**: `harness/cli.py:713` still registers `sub.add_parser("shopify-body", ...)`
  -- the subcommand name is a literal string, independent of the module name, so `harness
  shopify-body` keeps working with no shim needed.
- **Manifest contract intact**: `harness/page_body.py:239` still writes `shopify-body.assets.json`
  verbatim -- the filename contract `docs/IMAGES.md`, `docs/PUBLISHING.md`, and
  `tenants/peak-saunas/docs/PACKET-DRAFT.md` all reference is unchanged.
- `Makefile` and `docs/REVIEW-SITE.md` have zero references to the old module -- clean.
- **Stale references left behind** (comments/prose only, nothing that imports the dead path, so
  nothing breaks at runtime, but they now describe a file that no longer exists):
  - `docs/GENERATOR.md:77` and `:224` -- "`harness/shopify.py`'s `full_bleed_css()`..."
  - `docs/IMAGES.md:136` -- "(`harness/shopify.py`'s `build_asset_manifest`)"
  - `docs/REFINE-NOTES.md:64` -- "`harness/shopify.py` that turns any cartridge's rendered HTML..."
  - `harness/publishers/base.py:4` and `:22` -- comments citing `harness/shopify.py`
  - `harness/publishers/shopify.py:42` -- comment citing `harness/shopify.py`
  - (`docs/FIXLOG.md` and `docs/RESTRUCTURE-2026-09-10.md` also still say `harness/shopify.py`,
    but those are dated historical log entries, not living docs -- left alone deliberately.)

### ci-pipeline -- FIX
- `.github/workflows/ci.yml:3-5`:
  ```yaml
  on:
    push:
    pull_request:
  ```
  Neither trigger has a branch filter, so the workflow runs on push to **every** branch, not
  just `master`/`main` as the review point requires. Needs:
  ```yaml
  on:
    push:
      branches: [master, main]
    pull_request:
  ```
- Everything else is clean: installs only `-e .` plus `pytest`/`ruff` (no extra deps), no
  `secrets:` usage anywhere, `actions/checkout@v4` and `actions/setup-python@v5` are both pinned
  by major version, no network call to Anthropic or Shopify (job just runs `pytest -q`), and the
  suite's own `-m 'not live'` default (`pyproject.toml`'s `[tool.pytest.ini_options]`) already
  keeps the run offline -- the workflow does not need its own network guard.

### packaging-metadata -- FIX
- `pyproject.toml:6-10` adds `license = { file = "LICENSE" }` and two classifiers
  (`Programming Language :: Python :: 3.13`, `Private :: Do Not Upload`) but never states
  **Apache-2.0** anywhere -- no SPDX `license = "Apache-2.0"` string and no
  `"License :: OSI Approved :: Apache Software License"` trove classifier. The repo's own
  `LICENSE` file is Apache 2.0 (confirmed: first three lines are the Apache License header), so
  this is a one-line gap: swap to `license = "Apache-2.0"` (SPDX form) or add the OSI classifier
  alongside the file reference.
- `version` unchanged (`0.2.0`) -- fine, no bump claimed or needed.
- `[tool.setuptools.package-data]` still lists `design_skills/*` and `blocks/*` files untouched.
- Zero `[project.dependencies]` changes.

## Recommended merge order (differs from the synthesis doc)

Synthesis order was 1 `warmup-window`, 2 `fetch-url-safety`, 3 `run-log-context-manager`,
4 `shopify-body-rename`, 5 `ci-pipeline`, 6 `packaging-metadata`, on the premise that warm-up
goes first and needs one real generation right after.

None of the six branches touch overlapping files (confirmed via each `git diff --stat
master...github/cycle33/<pkg>`), so there is no merge-conflict reason to resequence. The reason
to resequence is that **4 of 6 packages are not clean MERGEs today** -- only
`fetch-url-safety` and `run-log-context-manager` pass every check with no changes needed. Since
section 9's acceptance bar requires suite-green before merge, `warmup-window` cannot go first as
written: it currently fails `test_no_tenant_specific_words_in_the_engine_or_the_cartridges`.

Recommended order:
1. **`fetch-url-safety`** -- clean, no dependency on anything else.
2. **`run-log-context-manager`** -- clean, no dependency on anything else.
3. **`warmup-window`** -- after the one-line `write.py:442` docstring fix restores suite-green;
   still merge before the doc/CI packages since it's the operator's actual priority target and
   the real generation below already validates the fix works.
4. **`shopify-body-rename`** -- after sweeping the 5 stale doc/comment references (or accept as
   a fast-follow commit if the operator wants to ship the rename now; nothing breaks either way).
5. **`ci-pipeline`** -- after adding the `branches: [master, main]` filter.
6. **`packaging-metadata`** -- after adding the explicit `Apache-2.0` SPDX string or classifier.

## Warm-up window: the one real run

Worktree: `advertorial-c33-warmup-window` (`test-warmup-window` @ `c48c6fa`, merged onto
`master` @ `715e590`, no conflicts). Command: `harness run
tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas` (foreground, real
Anthropic key from `tenants/peak-saunas/.env`, `timeout 600`). Never called Shopify or Slack
(neither credential was present; `products.json`/reviews came from the tenant's cached claims,
not a live fetch, per `REVIEW.md`'s `reviews-live`/`price-fuji` claim ids sourced from the
existing claims files).

From `.../out/20260914-173053-hidden-costs-v2-j7f5/REVIEW.md`:
- **First brand mention: word 1267** (product/company name), vs the 600-word gate -- clears it
  by more than 2x. First price: none in-window. First CTA: none in-window.
- **Attempts/repairs**: `product-page` and `longform` passed on attempt 1 (0 failures).
  `article` took **3 attempts** (2 repairs): attempt 1 had 1 failure, attempt 2 had 2 failures,
  attempt 3 had 0 -- final result PASS. Zero deterministic (no-model-call) fixes applied at any
  attempt, so all 3 attempts were real model calls.
- **Cost**: 9 of 14 allowed calls used, 41,997 of 220,000 allowed tokens, elapsed 258.5s of a
  300s wall limit. **Estimated cost: $0.3993** for all three cartridges (product-page + longform
  + article).
- Two other REVIEW.md items, unrelated to this package and pre-existing on master: `product-page`'s
  headline is 13 words against its own 4-10 word band (WARN, soft gate, `simplicity_mode: warn`),
  and the product's own Shopify URL handle contains "near-zero-emf" (flagged by the harness's
  existing banned-term URL check as informational -- it's the live storefront's own product slug,
  not text this package or any of the six generates).

## Exact merge commands (not run)

```bash
ssh prod
cd /home/deploy/advertorial
git checkout master
git pull --ff-only          # only if master could have moved; it was 715e590 throughout this review

# 1 + 2: clean, no fix needed
git merge --no-ff github/cycle33/fetch-url-safety
git merge --no-ff github/cycle33/run-log-context-manager

# 3: after the one-line docstring fix lands on cycle33/warmup-window and re-passes
#    `pytest -q` clean (1014 passed, 0 failed)
git merge --no-ff github/cycle33/warmup-window

# 4: after the doc sweep (or as-is, accepting the stale-doc debt)
git merge --no-ff github/cycle33/shopify-body-rename

# 5: after adding `branches: [master, main]` to both triggers
git merge --no-ff github/cycle33/ci-pipeline

# 6: after adding the explicit Apache-2.0 SPDX string/classifier
git merge --no-ff github/cycle33/packaging-metadata

# then, per docs/CYCLE-33-SWARM-PLAN.md section 9/10:
#   - one real generation per merge group, confirm PASS + financing sentence + byline + zero
#     banned terms + cost logged
#   - append "## Cycle 33" to docs/FIXLOG.md
#   - push the mirror
```
