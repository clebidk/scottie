# Kimi long-run baseline (2026-09-11)

The phase-1 deliverable of `docs/KIMI-LONG-RUN.md`: the system as it actually
stands at branch `kimi/long-run` (base: `main` @ cycle 26, 734 tests, ruff
clean), the review findings this run takes, the landscape borrowings it
applies, and the pre-registered done criteria every phase is judged against.
Written after reading `docs/ARCHITECTURE.md`, `docs/HARNESS-MAP.md`,
`docs/REVIEW-2026-09-11.md`, `docs/RESEARCH-HARNESS-LANDSCAPE.md`,
`tenants/peak-saunas/docs/design-notes-batch50.md`, `docs/FIXLOG.md` cycles
12-26, `tenants/peak-saunas/vocab.yaml`, and `tenants/peak-saunas/guardrails.md`.

## 1. System map, in my words

The harness is a V1-static pipeline (`docs/SPEC.md`'s ladder: no grader, no
cartridge-smith, no self-improvement until a scored corpus exists). One ad
(video / still / text) goes in; up to three gated, rendered landing pages come
out, one per cartridge. The engine (`harness/`) is company-agnostic; every
company value resolves at runtime through `harness/tenant.py` from
`tenants/<name>/`. Cartridges (`cartridges/<name>/`: `cartridge.md` +
`schema.json` + `template.html` + `rubric.md`) are tenant-neutral page types
with `{{ tenant.* }}` placeholders substituted at load.

Pipeline (`harness/pipeline.py`, `DEFAULT_STAGES`; `harness run` and
`harness workflow run ad-to-pages` share `execute`, so they cannot drift):

1. `prepare_run` — collision-safe run id (cycle 23: seconds + 4-char base32
   suffix, `os.makedirs(exist_ok=False)`), run dir, log, seed/RNG, cartridge
   selection, `state.json` (`generated`) + `packet.json` (`BOT DRAFT · NOT
   SENT`) written immediately.
2. `refresh_prices` — live Shopify prices merged into the run's claim
   universe; PDP claims seeded from product `body_html`; both cached under
   `tenants/<t>/runs/`; `claims/verified.json` is never written by a run.
3. `ingest` — `ad_brief.json`; EMF-mentioning ad claims are dropped here,
   before any gate sees them.
4. `ground` — product pick (explicit `--product`, else price-based inference,
   else tenant default) *before* the ad-claims gate, so price claims can
   match; `facts_pack.json` (product, specs, warranty/shipping/returns,
   verified-claim subset, assets, live reviews claim).
5. `gate_ad_claims` — one semantic-matcher model call (falls back to `{}` on
   any failure), then the deterministic `gate_ad_brief_claims`: alternative-
   subject claims never match or stop; locked topics (warranty / reviews /
   financing / price) go to their own evaluators against single sources of
   truth; everything else is alias match, then semantic mapping (numeric
   guard re-checked in code), then word overlap. Policy `warn` (the committed
   tenant default since cycle 19) drops failures into `not_repeated`; `stop`
   raises. STOP = exit 2, `unmatched_claims.json`, no page written.
6. `write_pages` — one writer call per cartridge (system prompt = forbidden
   words + `cartridge.md` + `schema.json` + global voice block, cache-
   breakpointed; user = ad_brief + facts_pack + ≤2 trimmed exemplars), then
   the gate-repair loop in `harness/cli.py`: `check_page_gates` =
   `claims.gate_page_json` (claim-id references, trigger-word/number
   sourcing, banned terms, lender names, first-person attribution, benefit-
   claim minimums, proof-stats, second-CTA, warranty/financing fixed
   sentences) + word range + CTA allowlist. A deterministic pre-repair pass
   (hype synonyms, leaked ids, incidental numerals, warranty/financing fixed
   sentences) runs before any model repair; at most `MAX_REPAIR_ATTEMPTS = 2`
   model repairs, then STOP.
7. `render_pages` — Jinja render (autoescape on; `byline_html` is the one
   `| safe` injection and is itself autoescaped since R37), structure.css
   always first, tenant `base.css` as override (cycle 23), assets downloaded
   + downscaled + dimension-stamped, sources list and JSON-LD injected by the
   renderer, post-render backstops (forbidden visible text, leaked claim ids,
   missing attribution) raise `ClaimsGateFailure` → exit 2.
8. `review_notify` — `state.json` → `needs_review`; Slack/email notify, both
   optional, both fail closed.
9. `write_review` — `REVIEW.md`.

Around the pipeline: per-run budget (300 s / 220k tokens / 14 calls,
`harness/budget.py`, exit 3); model tiering per tenant (`models:` in
`tenant.yaml`, `Tenant.model_for`, `config.DEFAULT_MODELS` fallback);
`--batch` initial writes at 50% off; run states + reviewer-restricted
`approve`/`reject`/`packet` (`harness/runstate.py`); draft-first publish via
`harness/publishers/` (Shopify Admin REST or export folder), refused without
`approved` + `ship` stamp, never run live yet; `harness serve` reviewer web
app and `harness revise` feedback-driven revision (cycle 26); `harness score`
appending human 4-axis scores to `tenants/<t>/evals/scores.jsonl` (write-only
today — nothing reads it back).

## 2. What exists vs. what is stubbed (verified against the tree)

**Exists and load-bearing:** the four cartridges (`article`, `product-page`,
`longform`, `listicle` — listicle opt-in, not in the default pool); the full
gate stack above; live price/PDP-claim refresh; budgeted repair loop with
deterministic pre-repair; review/approve/packet/publish plumbing; reviewer
web app; revise; 734-test offline suite with a socket guard; ruff clean at
`E4/E7/E9/F/B`.

**Stubbed or absent (confirmed):** `GBrainSource` raises
`NotImplementedError` (grounding is `LocalFactsSource` only); Shopify publish
has never run against a live storefront (no credentials, fails closed);
`sweep`/`score`/`packet`/`weekly-digest` workflows are action-only specs the
runner refuses by design; cron units are templates, nothing enabled; quiz and
comparison cartridges are wireframes in `design-notes-batch50.md` only; no
copy banks anywhere — copy comes from `claims/verified.json`, `vocab.yaml`,
`authors.yaml`, exemplars; no block registry; no eval aggregation (`scores
.jsonl` is append-only, never read); no HTML-validity / internal-link /
image-allowlist / JSON-LD checks anywhere in the gate (phase 2's gap);
`harness/blocks/` does not exist.

**Verified during bootstrap:** `evals/scores.jsonl` and `approvals.jsonl`
present in a fresh clone are *test artifacts* (the suite writes into the live
tenant tree — finding R30 in action), not human scores; deleted. No real
human scores exist in the repo today, which correction 4's "model judge only
after Caleb's scores exist" hinges on.

## 3. Review findings this run takes (by id)

Phase 4 applies these, one commit each, finding id in the message, full suite
green, and page.json parity against `evals/baseline/` via `evals/fake_run.py`.

| id | sev | what | why it is safe to take |
|---|---|---|---|
| R1 | High | `pipeline.py`'s four function-local `cli.py` imports (import cycle) | dies with the R2 split |
| R2 | High | `cli.py` (1,646 lines) owns the page gates, deterministic pre-repair, repair loop, soft checks, REVIEW.md renderer | move domain logic to `harness/repair.py` / `harness/review_md.py`; `cli.py` keeps argparse + `cmd_*` thin wrappers. Pure code motion; parity proven by byte-compare |
| R11 | Med | the recursive page.json walker is hand-written 10 times (9 in `claims.py`, 1 in `render.py`) | one `walk_page()` in `harness/textutil.py`; the review notes three copies have different skip rules — those keep their skip rules as parameters, behavior identical, pinned by the existing gate tests plus parity |
| R23 | Med | `tenant.yaml` vs `claims/config.json` overlap on 4 keys, implicit JSON-wins, no warning | **not** the review's merge (a precedence change is a behavior change): keep precedence exactly, add a logged warning when the two files set a key differently, plus tests pinning the order |
| R5 | Low | `review.py` carries the only pure-transform `cmd_*` | folds into the R2 split (command wrappers live with the CLI) |
| R9 | Low | SPEC.md says "page type", code says "cartridge" | one glossary line in `docs/SPEC.md` |
| R20 | Low | `cmd_ingest` re-implements `execute`'s budget/log/close block | share the block; cycle 23 already took the `make_run_dir` half |
| R24 | Low | `config.py`'s `REPO_DIR` defaults to `~/advertorial` and is read at import time | resolve lazily / inject; constants stay the defaults |

**Deliberately not taken, with reasons:**

- **R3** (claims.py package split) — real, but `claims.py` is the
  "do not rewrite" asset; a split is churn on it without a failure symptom.
  Follow-up after this run.
- **R6** (`adv` alias) — review itself defers it to the release commit.
- **R8** (`_dropped_emf_claims` rename) — changes model input (writer sees
  the key); not behavior-preserving.
- **R15** (cartridge placeholder dedupe) — cartridge text is prompt wording;
  changes model input.
- **R25** (`load_env(override=False)`) — changing which credential wins is a
  behavior change on prod; needs Caleb's call, not this run.
- **R26** (machine-readable run manifest) — a new artifact is a behavior
  change per the review; phase 5's dataset export covers the same need from
  the existing artifacts without changing a run.
- **R27/R28** (log handle lifecycle, JSONL logs) — fine as-is for this run;
  log shape is an artifact contract other tooling scrapes.
- **R30** (suite writes into the live tenant tree) — real (observed during
  bootstrap), but it re-homes the integration tests' outputs; larger than a
  "Low item closed cleanly". Flagged for a later cycle.
- **R33/R41** (fixture tenant; split `tenants/peak-saunas/` out) — the
  release blocker; multi-day, and it moves tenant data this run is forbidden
  to touch. Out of scope.
- **R38** (delimit untrusted ad text in prompts) — changes prompt bytes
  (model input) and the review itself marks it "a product decision".
  Recommended to Caleb separately.
- **R39** (fetch scheme allowlist/size cap) — a size cap can break a
  legitimate large asset; needs a real limit decision. Not behavior-
  preserving.
- **R40** (product-category words in engine prompts/regexes) — "every one is
  prompt text or changes facts_pack.json"; the largest remaining pre-release
  job, not a behavior-preserving fix.
- **R42/R44/R45** (LICENSE/CONTRIBUTING/CI, command grouping, `--version`) —
  release engineering; LICENSE already exists. Out of this run's scope.
- Section 13's `listicle/cartridge.md` `adv run` line — prompt text, and the
  alias still works.

## 4. Landscape borrowings this run applies

From `docs/RESEARCH-HARNESS-LANDSCAPE.md`'s ranked list:

- **#4 (promptfoo/Inspect: declarative eval cases + a report command)** —
  applied as phase 5, adapted: `harness dataset export` writes the JSONL
  record (ad_brief, facts_pack summary, cartridge/block versions, page.json,
  gate history, deterministic check results, human scores) and
  `harness eval report` aggregates `scores.jsonl` by cartridge, block, angle,
  reviewer. Borrow the file shapes and the dataset/solver/scorer split, not
  the libraries — the claims gate already outperforms generic assertion
  matching here.
- **#9 (Prefect: budget caps as named, tenant-overridable config)** — applied
  as phase 3's daily spend cap: a `budget:` block in `tenant.yaml` /
  `claims/config.json` (same precedence as every other overlapping key),
  engine defaults unchanged when unset. The daily cap is new: a per-tenant
  per-day spend ceiling checked before a run starts, recorded in the run log.
- **#11 (guardrails-ai `OnFailAction` vocabulary)** — docs only, phase 7:
  name the existing repair loop's actions (`fix` = deterministic pre-repair,
  `reask` = model repair, `exception` = `ClaimsGateFailure`) in
  `docs/ARCHITECTURE.md`/`docs/PROMPTING.md` so the design is legible.

Deferred, with reasons: #1/#5/#6 (Meta/Shopify/Drive connector work — not
this run), #2/#7/#10 (release engineering — Caleb's release cycle, not this
branch), #3 (CHANGELOG — start at first public tag), #8 (secrets discipline —
already true here; restated in the phase-7 runbook), #12 (checkpoint/resume —
a RunState persistence change; the cost-saver doesn't justify the artifact
change this run).

## 5. Pre-registered done criteria

A phase is done only when every criterion below passes. Checkpoint report per
phase end: commit hashes; test count before/after; ruff status; findings
applied by id; what could not be done and why; spend (must be $0 — no real
model keys exist on this branch); the next phase's criteria restated.

**Phase 0 — bootstrap (DONE).** Clone; Python 3.13 venv (repo requires
≥3.13; system 3.12); 734 tests green; ruff clean;
`harness run .../founder-warranty-demo.txt` with the fake client produces
three pages (article, product-page, longform), warranty overclaim dropped
under `warn`, state `needs_review`. Evidence: `evals/fake_run.py` (committed,
with tests) reproduces it on demand.

**Phase 1 — baseline (this document).** Done when: this file exists with the
map, findings, borrowings, and criteria; `evals/baseline/<fixture>/` holds
the fake-client `page.json` files for the suite's dry-run fixtures
(`founder-warranty-demo`, `hidden-costs-v2-transcript`) plus a manifest;
suite green; ruff clean.

**Phase 2 — deterministic checks.** New tenant-neutral module
(`harness/pagechecks.py`), all four wired so failures stop a bad page before
publish, with discrimination smoke tests (known-good passes, planted bad
fails) per check:

1. *Image allowlist* — every `asset_id` referenced in page.json exists in the
   run's `facts_pack.assets` (today an unknown id is silently dropped at
   render). Page-gate level: the repair loop can fix it.
2. *Internal links* — every URL field in page.json is internal (tenant
   `site_host`, or relative); at least one internal link present. Page-gate
   level. Post-render backstop counts rendered `<a href>` internal links.
3. *JSON-LD* — `build_json_ld`'s output for the page is JSON-serializable,
   carries `@context`/`@type`, and the type matches the cartridge's
   (article→Article, product-page→Product, listicle→ItemList,
   longform→FAQPage). Page-gate level (the builder is a pure function of
   page.json + facts_pack — no render needed).
4. *HTML validity* — rendered `index.html` parses with zero errors under an
   HTML5 parser (html5lib, `strict=True`: unclosed tags, bad nesting).
   Post-render backstop in `render_page`, same pattern as the existing
   `html_visible_text` backstop. **Registered deviation from the plan's
   "repair loop handles failures":** writer prose is autoescaped (R37) and
   JSON-LD goes through `| tojson`, so page.json content cannot produce
   invalid HTML — a validity failure is a template/renderer/tenant-file bug
   no writer repair can fix; burning two repair calls before the same STOP
   would only spend budget. The three page.json-level checks above *are* in
   the repair loop, which is where repairable failures live.

Done when: all four checks fail a planted-bad page and pass the known-good
canned pages; the image/link/JSON-LD failures are resolved by the repair loop
with a fake client; the HTML check STOPs a run whose rendered HTML is broken;
suite green; ruff clean.

**Phase 3 — block registry.** Done when: `harness/blocks/` exists with
`registry.json` in the shadcn registry-item shape (name, type, category,
files, dependencies, meta: license, tenant_neutral, mobile_rules, motion,
screenshot); 20-30 tenant-neutral static blocks (hero variants, three-stat
proof row, feature grid, numbered reason, testimonial — verified quotes only,
FAQ, comparison table schema-only, sticky CTA, closing offer, financing line,
trust strip); the deterministic block gate (tenant-neutral words, CSS
variables only, no fixed widths over 390 px, motion via CSS + the existing
scroll observer, every image sized, license field, screenshot present) passes
every block and fails planted violations; blocks are layout only — copy comes
from verified claims through the existing gate (no $/mo widget, no
"30-day guarantee", no "white-glove delivery", no EMF term, ever); cartridge
templates can compose blocks and the writer's variant choice is recorded in
page.json; the daily spend cap (borrowing #9) is enforced and tested; suite
green; ruff clean. No model judge — correction 4.

**Phase 4 — structural findings.** Done when: R1/R2, R11, R23, and the Low
items in section 3 land as one commit each with tests; the full suite and
ruff stay green at every commit; and `evals/fake_run.py` re-run against
`evals/baseline/` byte-compares equal for every fixture/cartridge after each
commit (behavior-preserving proven, not assumed).

**Phase 5 — eval export and report.** Done when:
`harness dataset export --tenant <t>` writes JSONL with ad_brief, facts_pack
summary, cartridge + block versions, page.json, gate history, deterministic
check results, and human scores joined from `evals/scores.jsonl`;
`harness eval report` aggregates scores by cartridge, block, angle, and
reviewer and prints the rubric's publish bar (mean "would publish" ≥ 4); both
handle an empty/missing scores file cleanly (none exist in the repo today);
tests; suite green; ruff clean. No fine-tune — correction 7.

**Phase 6 — comparison cartridge draft.** Done when:
`cartridges/comparison/{cartridge.md,schema.json,template.html,rubric.md}`
exists from the type-05 wireframe (verdict box, sourced comparison table,
deep dives on wins, honest competitor strengths, FAQ, one CTA);
generic-category framing only — no named competitor, no EMF section, per the
wireframe's own Peak rule; the schema requires a claim id on every table
cell; competitor rows come from `claims/competitors/*.json` (new directory,
seeded empty/`pending` — Caleb's sources required before any real run); the
fake-client suite passes a comparison page end to end; suite green; ruff
clean. No live run against a named competitor — correction 3.

**Phase 7 — runbook.** Done when: `docs/PROMPTING.md` exists with the master
prompt (objective, repo map, artifact discipline, evidence gates, stop
conditions, budget guards), per-phase loop prompts, the checkpoint format,
and the human-review triggers (any change to a gate, a cartridge rule, a
tenant file, or spend); the OnFailAction vocabulary (borrowing #11) is
documented where the repair loop is described.

## 6. Standing constraints (the plan's corrections, restated for this branch)

1. Blocks are layout, never copy; copy always comes from verified claims
   through the existing gate. Prohibited outputs for Peak: $/mo financing
   widgets, "30-day guarantee", "white-glove delivery" (returns carry a 25%
   restocking fee; delivery is curbside freight).
2. Competitor harvesting is for structure only; competitor names never appear
   ("Sunlighten" banned absolutely; "Sun Home" reads "Sun" per vocab.yaml).
   Never copy code or copy from a competitor page.
3. No model judge until Caleb's human scores exist to calibrate against;
   the block gate is deterministic checks first.
4. Model tiering stays per-tenant config; no hard-coded model anywhere; a new
   provider would be an adapter behind the existing client interface, behind
   a config key, default off. Gates stay model-agnostic.
5. Never modify `claims/verified.json`, `vocab.yaml`, `authors.yaml`;
   additions go to `claims/pending.json` with a source.
6. Branch `kimi/long-run` only; never push to `master`/`main`; no prod
   access; no real keys; Shopify/Slack credentials stay absent (both fail
   closed). Branch protection on `master`/`main` could not be verified from
   this token (403 on the API) — flagged to Caleb per the plan.
7. One commit per loop iteration; finding id or phase in the message; full
   suite green and ruff clean at every commit.
