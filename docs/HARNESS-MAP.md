# Harness map (2026-09-10, restructured)

The system of record is `prod:/home/deploy/advertorial`, branch `master`. This maps what exists,
what's stubbed, and what not to rewrite.

Since the 2026-09-10 restructure the tree has two halves. `harness/` is the engine and is
company-agnostic; `tenants/<name>/` holds one company's claims, brand, authors, vocabulary,
fixtures, exemplars, and docs. `cartridges/` sits with the engine and carries no company's words --
a cartridge says `{{ tenant.name }}` where a company belongs, substituted at load time. Every
command takes `--tenant`, defaulting through `HARNESS_TENANT` to `tenants/default.txt`
(`peak-saunas`). See `docs/RESTRUCTURE-2026-09-10.md` for the move list and
`docs/ARCHITECTURE.md` for the shape.

## Entrypoints

- `.venv/bin/harness run <input>` -- the full pipeline, `harness/cli.py`'s `cmd_run`. `--cartridges`,
  `--seed`, `--product` flags select which cartridges run and pin the RNG/product for a reproducible
  page set.
- `.venv/bin/harness review tenants/peak-saunas/out/<run-id>` -- `cmd_review`, writes a self-contained `<cartridge>-review.html`
  per cartridge with images inlined as data URIs (`inline_assets_as_data_uris`).
- `.venv/bin/harness claims add/list` -- `cmd_claims_add`/`cmd_claims_list`, the only write path into
  `tenants/peak-saunas/claims/verified.json`.
- `.venv/bin/harness score <run-id> --angle N --brand N --claims N --publish N` -- `cmd_score`, appends
  one JSON line to `tenants/peak-saunas/evals/scores.jsonl`. No read/aggregation command exists yet.
- `harness tenant init <slug>` / `harness tenant list` -- copy `tenants/_template` to a new tenant,
  and show every tenant with whether it is configured yet.
- `harness workflow run ad-to-pages --input <input> --tenant <t>` -- the same stage functions
  `harness run` calls, in the order `workflows/ad-to-pages.yaml` gives. `harness workflow list`
  shows what exists. `sweep`/`score`/`packet` are specifications: their steps are keyed `action:`
  and the runner does not execute them.
- `make review RUN=<run-id> TENANT=<t>` -- wraps `harness review` over `ssh $(PROD)` and rsyncs the generated
  `*-review.html` files back to a local path (`Makefile`'s `review` target).
- `make deploy` / `make install` / `make test` / `make run` / `make pull-out` -- rsync the source tree
  to prod, `pip install -e .` there, run the local pytest suite, run a job remotely, and pull `out/`
  back, respectively.

## Pipeline stages and the file that owns each

| Stage | Owning file | Notes |
|---|---|---|
| ingest (ad -> `ad_brief.json`) | `harness/ingest.py` | Drive-link/local-path resolution, whisper transcript or Claude-vision still-image read, `drop_emf_claims` runs here before the ad-claims gate ever sees an EMF mention. |
| grounder (-> `facts_pack.json`) | `harness/ground.py` | `LocalFactsSource` is the real, working implementation (products.json, specs, assets, Drive image selection). `GBrainSource` (line 372) is a stub that raises `NotImplementedError("GBrainSource is a stub; wire it in after tenants/peak-saunas/docs/knowledge-map.md lands ...")` -- grounding today never reads g Brain live. |
| live price/claim refresh | `harness/prices.py`, `harness/pdp_claims.py` | Pulls `peaksaunas.com/products.json` and derives claims from Shopify `body_html` at run time; never hardcodes a price. |
| claims gate | `harness/claims.py` | `gate_ad_brief_claims`, locked-topic evaluators, `classify_ad_claim_about`, warranty-violation and word-overlap/numeric-guard matching. Deterministic, no model call except the semantic-match pre-pass. |
| semantic claim matching | `harness/semantic_match.py` | One real Claude call per run proposing meaning-based claim mappings; falls back to `{}` (pure word-overlap) on any failure. |
| writer (-> `page.json` x N) | `harness/write.py` | `write_page`/`write_and_gate_page`, cartridge-agnostic: loads `cartridge.md`, `schema.json`, up to 2 trimmed exemplars, drives the repair loop against `check_page_gates`. |
| deterministic pre-repair | `harness/cli.py` | `apply_deterministic_fixes` -- hype-word substitution, incidental-numeral conversion, warranty-sentence fix -- resolves specific gate failures without spending a model call. |
| renderer (-> `index.html`) | `harness/render.py` | Injects byline/dates/disclosure/ad-label and JSON-LD; the model never writes these fields. Falls back to `harness/fallback.css`/a built-in byline if `tenants/peak-saunas/brand/base.css`/`tenants/peak-saunas/brand/byline.html` are missing. |
| budget/log | `harness/budget.py`, `harness/log.py` | Wall-clock/token/call caps (currently 300s / 220,000 tokens / 14 calls); per-run structured log with an estimated-cost line. |
| CLI orchestration | `harness/cli.py` | `build_parser`, `write_review_md`, the repair loop, and the deterministic pre-repair pass. |
| stage registry | `harness/pipeline.py` | `RunState` plus the eight named stages and `execute`; `harness run` and `harness workflow run` both go through it, so they cannot drift apart. |
| tenant resolution | `harness/tenant.py` | Which company a command is for, every per-tenant path, config layering (defaults < `tenant.yaml` < `claims/config.json`), placeholder rendering, and the readiness check that exits 4 instead of crashing. |
| vocabulary | `harness/vocab.py` | A loader over the tenant's `vocab.yaml`. The absolute bans, the fixed warranty/financing sentences, and the trigger words are data, not code. |
| review html | `harness/review.py` | Split out of `cli.py`; self-contained `<cartridge>-review.html` with images inlined. |

## Done vs. stubbed

**Done and load-bearing** (per `docs/FIXLOG.md` cycles 1-13 and `tenants/peak-saunas/docs/SWEEP-2026-09-10b.md`, all
seven fixtures currently PASS): ingest (video + still), claims gate with locked-topic evaluators and
semantic matching, deterministic pre-repair for hype words/numerals/warranty wording, live price and
Shopify-body-derived claims, budgeted repair loop, renderer with byline/disclosure/JSON-LD injection,
`harness review`/`harness score` recording.

**Stubbed or absent**:
- `GBrainSource` (`harness/ground.py:372`) -- grounding runs entirely on `LocalFactsSource` today; g Brain
  retrieval described in `docs/SPEC.md` section 3 ("Long-term: g Brain ... read through a retrieval
  allowlist") is not wired into a live run.
- **Shopify publish** -- no Admin API call exists anywhere in `harness/*.py` (confirmed by grep); `adv
  publish` is listed as "week 2" in `docs/SPEC.md` section 8 but has no `cmd_publish` in `cli.py`
  today.
- **Quiz and comparison cartridges** -- `cartridges/` holds `article`, `longform`, `product-page`,
  and `listicle` (opt-in); styles 4 and 5 from `tenants/peak-saunas/brand/NOTES.md` are
  `[NEEDS INPUT]`/unbuilt, matching `docs/SPEC.md`'s "Friday set: 1, 2, 3."
- **The non-pipeline workflows** -- `workflows/sweep.yaml`, `score.yaml`, and `packet.yaml` describe
  real work but their steps are keyed `action:`; `harness/workflows.py` executes only `stage:` steps,
  so running one of those exits 1 with "runs no pipeline stages" rather than pretending.
- **Cron timers** -- `crons/` holds systemd USER unit templates and an `install.sh` that renders them
  into `~/.config/systemd/user/` and then PRINTS the enable commands. Nothing is enabled.
- **Self-grading loop** -- `docs/SPEC.md`'s V1.5 (`writer -> grader -> writer`, max 3 rounds) has no
  code: no `grader` module, and no reference to "grader" or "rubric" anywhere in `harness/*.py`.
  `rubric.md` exists per cartridge but is documentation only, per `README.md`'s own note ("not executed
  in V1").
- **Scoring is minimal** -- `cmd_score` appends one line to `tenants/peak-saunas/evals/scores.jsonl`; there is no
  aggregation, no report command, and nothing reads the file back into the harness.

## The legacy reference artifact

`tenants/peak-saunas/reference/peak-listicle-lp` is a one-page static replica, not a generator:
`index.html` (standalone, own header/footer/logo), `shopify-body.html` (the live `body_html`, no
header/footer -- the Aurora theme supplies those), 7 image assets, and a `README.md` documenting three
Shopify traps -- (1) the rich-text editor strips the `<style>` block on save, so edits must go through
the Admin API only; (2) Aurora's `.container--small` wrapper re-narrows the page unless the top three
`:has()` full-bleed rules stay intact; (3) the storefront serves several cache epochs, so a change
needs verification with 8+ pulls, not one. It has no CLI, no templating, no schema -- every value is
hand-typed HTML, matching one specific ad angle ("5 Reasons") for one specific product (the Mini). It
is the design source for the new `listicle` cartridge's markup/CSS, not code to extend in place.

`tenants/peak-saunas/docs/existing-page-generator.md` documents a second, unrelated tool: a root-owned Python CLI
(`create_page.py`/`bulk_create.py`, outside this repo) that already publishes generic Shopify `Page`
resources via the Admin API from CLI flags or a CSV, with a fixed 5-section template
(hero/trust-badges/benefits-grid/testimonial/final-CTA). Its publish plumbing (`SHOPIFY_STORE`/
`SHOPIFY_TOKEN` env vars, Page-resource creation) is reusable as-is; its template is not -- it has no
concept of a cartridge and cannot render this harness's `page.json` shape.

## Smallest bone fixes (as of the restructure)

**Listicle type, no engine rewrite** (done, Cycle 14): `cartridges/listicle/{cartridge.md, schema.json,
template.html, rubric.md}` follows the exact shape of the other cartridges --
`discover_cartridges()`, `write_page`, and `render_page` already handle any conforming directory
generically. `template.html` reuses `tenants/peak-saunas/reference/peak-listicle-lp/shopify-body.html`'s markup/CSS
structure and its three `:has()` rules, re-themed to `tenants/peak-saunas/brand/tokens.json`'s color tokens (see
`tenants/peak-saunas/docs/RESEARCH-LISTICLE.md` 0A for the token conflict this resolves).

**Shopify body builder, no rewrite**: add one new function to `harness/render.py` (or a small new
`harness/publish.py`) that wraps an already-rendered cartridge's HTML body in the same `:has()`
full-bleed header block, reusing `tenants/peak-saunas/docs/existing-page-generator.md`'s Admin-API publish plumbing
(store/token env vars, Page-resource create) rather than its template -- the harness already produces
a Jinja-rendered `<body>`; publishing needs a thin adapter, not a new renderer.

## File-touch list, Phase 2/3

- `cartridges/listicle/*` (new) -- schema, cartridge rules, template, rubric.
- `harness/write.py` -- none, if the listicle schema stays within the existing generic writer path;
  revisit only if a listicle-specific repair rule (e.g. per-item claim_id minimum) needs new logic
  beyond what `check_page_gates` already does generically.
- `harness/ground.py` -- implement `GBrainSource` in place of the stub, once g Brain's retrieval allowlist
  is confirmed live (blocks nothing about the listicle type itself).
- `harness/render.py` or new `harness/publish.py` -- Shopify body-builder/publish adapter.
- `harness/cli.py` -- new `cmd_publish` wired into `build_parser`, once the publish adapter exists.
- `tenants/peak-saunas/claims/verified.json` -- needs per-item proof claims once real listicle ad angles are ingested;
  none of this is a listicle-specific schema change, just ordinary claim additions via `harness claims add`.

## Do not rewrite

- **`harness/claims.py`'s gate logic** -- extensively iterated across 13 fix cycles (locked-topic
  evaluators, semantic matcher, alternative-claim classifier, warranty-wording pre-repair); the
  failure symptom that would justify touching it is a new, reproducible false-MATCH or false-STOP on
  a real fixture, not a desire to simplify it -- see `docs/FIXLOG.md`'s own repeated caution against
  patching the noun-list classifier ad hoc.
- **`harness/tenant.py`'s resolution order and readiness check** -- the whole point of the
  restructure is that one place decides which company a run is for and refuses to start when that
  company is not set up. Adding a second way to reach a tenant value re-scatters what was just
  gathered. The failure symptom that would justify touching it is a real tenant whose data genuinely
  cannot be expressed in `tenant.yaml`/`authors.yaml`/`vocab.yaml`, not convenience.
- **`harness/write.py`'s generic cartridge-loading path** -- already cartridge-agnostic; a rewrite here
  would be justified only if a new type genuinely cannot express its rules through
  `cartridge.md`/`schema.json`, which a listicle (same page.json-shaped structure as the other three)
  does not require.
- **The legacy Mac artifact and `tenants/peak-saunas/docs/existing-page-generator.md`'s CLI** -- both are reference
  material, not live systems to extend; rewriting either in place would fork effort away from the one
  system of record on prod. The failure symptom that would justify reviving either is a Shopify publish
  need arriving before this harness's own publish adapter exists -- otherwise, treat them as read-only
  sources for the new bones above.
