# Harness map (2026-09-10)

The system of record is `prod:/home/deploy/advertorial`, branch `master` -- not the Mac clone used to
write this research, and not the Mac-only static artifact described below. This maps what exists,
what's stubbed, and the smallest changes to add a listicle type and a Shopify publish path.

## Entrypoints

- `.venv/bin/adv run <input>` -- the full pipeline, `adv/cli.py`'s `cmd_run`. `--cartridges`,
  `--seed`, `--product` flags select which cartridges run and pin the RNG/product for a reproducible
  page set.
- `.venv/bin/adv review out/<run-id>` -- `cmd_review`, writes a self-contained `<cartridge>-review.html`
  per cartridge with images inlined as data URIs (`inline_assets_as_data_uris`).
- `.venv/bin/adv claims add/list` -- `cmd_claims_add`/`cmd_claims_list`, the only write path into
  `claims/verified.json`.
- `.venv/bin/adv score <run-id> --angle N --brand N --claims N --publish N` -- `cmd_score`, appends
  one JSON line to `evals/scores.jsonl`. No read/aggregation command exists yet.
- `make review RUN=<run-id>` -- wraps `adv review` over `ssh $(PROD)` and rsyncs the generated
  `*-review.html` files back to a local path (`Makefile`'s `review` target).
- `make deploy` / `make install` / `make test` / `make run` / `make pull-out` -- rsync the source tree
  to prod, `pip install -e .` there, run the local pytest suite, run a job remotely, and pull `out/`
  back, respectively.

## Pipeline stages and the file that owns each

| Stage | Owning file | Notes |
|---|---|---|
| ingest (ad -> `ad_brief.json`) | `adv/ingest.py` | Drive-link/local-path resolution, whisper transcript or Claude-vision still-image read, `drop_emf_claims` runs here before the ad-claims gate ever sees an EMF mention. |
| grounder (-> `facts_pack.json`) | `adv/ground.py` | `LocalFactsSource` is the real, working implementation (products.json, specs, assets, Drive image selection). `GBrainSource` (line 372) is a stub that raises `NotImplementedError("GBrainSource is a stub; wire it in after docs/knowledge-map.md lands ...")` -- grounding today never reads g Brain live. |
| live price/claim refresh | `adv/prices.py`, `adv/pdp_claims.py` | Pulls `peaksaunas.com/products.json` and derives claims from Shopify `body_html` at run time; never hardcodes a price. |
| claims gate | `adv/claims.py` | `gate_ad_brief_claims`, locked-topic evaluators, `classify_ad_claim_about`, warranty-violation and word-overlap/numeric-guard matching. Deterministic, no model call except the semantic-match pre-pass. |
| semantic claim matching | `adv/semantic_match.py` | One real Claude call per run proposing meaning-based claim mappings; falls back to `{}` (pure word-overlap) on any failure. |
| writer (-> `page.json` x N) | `adv/write.py` | `write_page`/`write_and_gate_page`, cartridge-agnostic: loads `cartridge.md`, `schema.json`, up to 2 trimmed exemplars, drives the repair loop against `check_page_gates`. |
| deterministic pre-repair | `adv/cli.py` | `apply_deterministic_fixes` -- hype-word substitution, incidental-numeral conversion, warranty-sentence fix -- resolves specific gate failures without spending a model call. |
| renderer (-> `index.html`) | `adv/render.py` | Injects byline/dates/disclosure/ad-label and JSON-LD; the model never writes these fields. Falls back to `adv/fallback.css`/a built-in byline if `brand/base.css`/`brand/byline.html` are missing. |
| budget/log | `adv/budget.py`, `adv/log.py` | Wall-clock/token/call caps (currently 300s / 220,000 tokens / 14 calls); per-run structured log with an estimated-cost line. |
| CLI orchestration | `adv/cli.py` | `cmd_run`, `write_review_md`, `build_parser` -- ties every stage together; owns `REVIEW.md` generation. |

## Done vs. stubbed

**Done and load-bearing** (per `docs/FIXLOG.md` cycles 1-13 and `docs/SWEEP-2026-09-10b.md`, all
seven fixtures currently PASS): ingest (video + still), claims gate with locked-topic evaluators and
semantic matching, deterministic pre-repair for hype words/numerals/warranty wording, live price and
Shopify-body-derived claims, budgeted repair loop, renderer with byline/disclosure/JSON-LD injection,
`adv review`/`adv score` recording.

**Stubbed or absent**:
- `GBrainSource` (`adv/ground.py:372`) -- grounding runs entirely on `LocalFactsSource` today; g Brain
  retrieval described in `docs/SPEC.md` section 3 ("Long-term: g Brain ... read through a retrieval
  allowlist") is not wired into a live run.
- **Shopify publish** -- no Admin API call exists anywhere in `adv/*.py` (confirmed by grep); `adv
  publish` is listed as "week 2" in `docs/SPEC.md` section 8 but has no `cmd_publish` in `cli.py`
  today.
- **Quiz and comparison cartridges** -- `cartridges/` holds only `article`, `longform`, `product-page`;
  styles 4 and 5 from `brand/NOTES.md` are `[NEEDS INPUT]`/unbuilt, matching `docs/SPEC.md`'s "Friday
  set: 1, 2, 3."
- **Self-grading loop** -- `docs/SPEC.md`'s V1.5 (`writer -> grader -> writer`, max 3 rounds) has no
  code: no `grader` module, and no reference to "grader" or "rubric" anywhere in `adv/*.py`.
  `rubric.md` exists per cartridge but is documentation only, per `README.md`'s own note ("not executed
  in V1").
- **Scoring is minimal** -- `cmd_score` appends one line to `evals/scores.jsonl`; there is no
  aggregation, no report command, and nothing reads the file back into the harness.

## The legacy Mac artifact

`/Users/calebniednagel/Claude/peak-listicle-lp` is a one-page static replica, not a generator:
`index.html` (standalone, own header/footer/logo), `shopify-body.html` (the live `body_html`, no
header/footer -- the Aurora theme supplies those), 7 image assets, and a `README.md` documenting three
Shopify traps -- (1) the rich-text editor strips the `<style>` block on save, so edits must go through
the Admin API only; (2) Aurora's `.container--small` wrapper re-narrows the page unless the top three
`:has()` full-bleed rules stay intact; (3) the storefront serves several cache epochs, so a change
needs verification with 8+ pulls, not one. It has no CLI, no templating, no schema -- every value is
hand-typed HTML, matching one specific ad angle ("5 Reasons") for one specific product (the Mini). It
is the design source for the new `listicle` cartridge's markup/CSS, not code to extend in place.

`docs/existing-page-generator.md` documents a second, unrelated tool: a root-owned Python CLI
(`create_page.py`/`bulk_create.py`, outside this repo) that already publishes generic Shopify `Page`
resources via the Admin API from CLI flags or a CSV, with a fixed 5-section template
(hero/trust-badges/benefits-grid/testimonial/final-CTA). Its publish plumbing (`SHOPIFY_STORE`/
`SHOPIFY_TOKEN` env vars, Page-resource creation) is reusable as-is; its template is not -- it has no
concept of a cartridge and cannot render this harness's `page.json` shape.

## Smallest bone fixes

**Listicle type, no engine rewrite**: add `cartridges/listicle/{cartridge.md, schema.json,
template.html, rubric.md}` following the exact shape of the three existing cartridges --
`discover_cartridges()`, `write_page`, and `render_page` already handle any conforming directory
generically. `template.html` reuses `reference/peak-listicle-lp/shopify-body.html`'s markup/CSS
structure and its three `:has()` rules, re-themed to `brand/tokens.json`'s color tokens (see
`docs/RESEARCH-LISTICLE.md` 0A for the token conflict this resolves).

**Shopify body builder, no rewrite**: add one new function to `adv/render.py` (or a small new
`adv/publish.py`) that wraps an already-rendered cartridge's HTML body in the same `:has()`
full-bleed header block, reusing `docs/existing-page-generator.md`'s Admin-API publish plumbing
(store/token env vars, Page-resource create) rather than its template -- the harness already produces
a Jinja-rendered `<body>`; publishing needs a thin adapter, not a new renderer.

## File-touch list, Phase 2/3

- `cartridges/listicle/*` (new) -- schema, cartridge rules, template, rubric.
- `adv/write.py` -- none, if the listicle schema stays within the existing generic writer path;
  revisit only if a listicle-specific repair rule (e.g. per-item claim_id minimum) needs new logic
  beyond what `check_page_gates` already does generically.
- `adv/ground.py` -- implement `GBrainSource` in place of the stub, once g Brain's retrieval allowlist
  is confirmed live (blocks nothing about the listicle type itself).
- `adv/render.py` or new `adv/publish.py` -- Shopify body-builder/publish adapter.
- `adv/cli.py` -- new `cmd_publish` wired into `build_parser`, once the publish adapter exists.
- `claims/verified.json` -- needs per-item proof claims once real listicle ad angles are ingested;
  none of this is a listicle-specific schema change, just ordinary claim additions via `adv claims add`.

## Do not rewrite

- **`adv/claims.py`'s gate logic** -- extensively iterated across 13 fix cycles (locked-topic
  evaluators, semantic matcher, alternative-claim classifier, warranty-wording pre-repair); the
  failure symptom that would justify touching it is a new, reproducible false-MATCH or false-STOP on
  a real fixture, not a desire to simplify it -- see `docs/FIXLOG.md`'s own repeated caution against
  patching the noun-list classifier ad hoc.
- **`adv/write.py`'s generic cartridge-loading path** -- already cartridge-agnostic; a rewrite here
  would be justified only if a new type genuinely cannot express its rules through
  `cartridge.md`/`schema.json`, which a listicle (same page.json-shaped structure as the other three)
  does not require.
- **The legacy Mac artifact and `docs/existing-page-generator.md`'s CLI** -- both are reference
  material, not live systems to extend; rewriting either in place would fork effort away from the one
  system of record on prod. The failure symptom that would justify reviving either is a Shopify publish
  need arriving before this harness's own publish adapter exists -- otherwise, treat them as read-only
  sources for the new bones above.
