---
name: harness-long-run-kimi
overview: "A long-running Kimi K3 engagement against the advertorial harness (github.com/clebidk/scottie): build the block registry, add deterministic checks and an eval export, apply the open findings from the structural review, and prepare the comparison cartridge — on a branch, with evidence gates, never touching prod or master."
todos:
  - id: bootstrap
    content: Clone clebidk/scottie on branch kimi/long-run; install; 702+ tests green; ruff clean; `harness run` on the text fixture with the fake client reproduces three pages
    status: pending
  - id: read-first
    content: Read docs/ARCHITECTURE.md, HARNESS-MAP.md, REVIEW-2026-09-11.md, RESEARCH-HARNESS-LANDSCAPE.md, design-notes-batch50.md, FIXLOG.md cycles 12-26, tenants/peak-saunas/vocab.yaml and guardrails.md; write docs/KIMI-BASELINE.md (what exists, what is stubbed, the unapplied review findings you will take)
    status: pending
  - id: checks
    content: Add deterministic checks to the page gate: HTML validity (unclosed tags), internal-link presence, image allowlist, JSON-LD validity; discrimination smoke tests (known-good page passes, bait page fails)
    status: pending
  - id: blocks
    content: Build harness/blocks registry (registry.json in shadcn registry-item shape) with 20-30 tenant-neutral static blocks; cartridge templates compose blocks; writer selects block variants by id; every block passes the block gate
    status: pending
  - id: review-findings
    content: Apply the unapplied REVIEW-2026-09-11 findings that are behavior-preserving (cli.py split, page-walker dedupe, config precedence) each as its own commit with tests
    status: pending
  - id: eval-export
    content: `harness dataset export` -> JSONL of (ad_brief, facts_pack, cartridge versions, block ids, page.json, gate history, human scores); `harness eval report` aggregating evals/scores.jsonl per cartridge, block, and angle
    status: pending
  - id: comparison-cartridge
    content: Draft cartridge 5 (comparison) from the wireframe in design-notes-batch50.md with a sourced competitor spec table schema; no live runs against named competitors until Caleb approves the competitor claims
    status: pending
  - id: runbook
    content: docs/PROMPTING.md - master prompt, per-phase loop prompts, checkpoint format, stop conditions, budget guards
    status: pending
isProject: false
---

# Harness long run for Kimi K3

## What is true about the system today (read before planning)

- The generator is the **advertorial harness**, a Python CLI at github.com/clebidk/scottie (mirror of the prod server repo). Engine in `harness/`, tenant-neutral type definitions in `cartridges/`, all Peak Saunas data in `tenants/peak-saunas/`. 702 tests, ruff clean, `harness doctor` checks a tenant.
- **Four cartridges exist**: article, product-page, longform, listicle. Quiz and comparison exist only as wireframes in `docs/design-notes-batch50.md`.
- There are **no copy banks**. Copy comes from `tenants/<t>/claims/verified.json` (every fact carries a source), `vocab.yaml` (forbidden terms, synonyms, fixed sentences), `authors.yaml`, and per-cartridge exemplars. The writer may only cite verified claim ids; a deterministic gate plus a bounded model repair loop enforces this.
- **Locked topics** never come from ad text or model output: warranty (fixed sentence), financing (fixed sentence "Financing is available through {lender} at checkout."; monthly figures are forbidden), review statistics (live Judge.me only), price (live Shopify only). Absolute rule for Peak: the term EMF never appears anywhere.
- Runs carry `state.json` (generated, needs_review, approved, published, rejected), a packet stamp, and a reviewer-restricted approve step. Publishing is draft-first through a Shopify adapter and never runs without approval plus a `ship` stamp. Notifications post to Slack when enabled.
- Evidence already exists: `docs/REVIEW-2026-09-11.md` (45 findings, 12 applied, 33 open with reasons), `docs/RESEARCH-HARNESS-LANDSCAPE.md` (ranked borrowings), `docs/design-notes-batch50.md` (39 screenshot teardown, proposed changes), `docs/FIXLOG.md` (26 cycles, each with failure symptom and fix). Start from these; do not re-derive them.

## Corrections to the original plan

1. **Phase 1 ARCHITECTURE.md already exists.** Write `docs/KIMI-BASELINE.md` instead: what you found, what you will take from the open findings, and your pre-registered done criteria per phase.
2. **Financing widgets with $/mo framing, "30-day guarantee", and "white-glove delivery" are prohibited outputs for Peak.** Returns carry a 25% restocking fee, delivery is curbside freight, and monthly figures have no quote basis. A block may provide the layout for a trust strip or a financing line; the copy inside it always comes from verified claims through the existing gate. Blocks are layout, never copy.
3. **Competitor harvesting is fine for structure; competitor names are not.** Sunlighten may never appear in Peak creative; "Sun Home" reads "Sun". Harvest section anatomy and rebuild blocks by hand. Never copy code or copy from a competitor page.
4. **"UI style cartridge as judge" does not exist.** Build the block gate as deterministic checks first (tenant-neutral words, CSS variables only, no fixed widths over 390 px, motion via CSS and the existing scroll observer, every image sized, license field present, screenshot present). A model judge is allowed only after Caleb's human scores exist to calibrate it against.
5. **Model tiering is configured per tenant** (`models:` in tenant.yaml). Do not hard-code a model anywhere. If Kimi K3 is to write pages, add a provider adapter behind the existing client interface with the same usage and cost accounting, behind a config key, default off. Gates are model-agnostic and stay so.
6. **Past bugs listed (markdown list loss, missing internal links, hallucinated model names, unclosed divs) came from the old root-owned generator, not this harness.** Model names and specs are already gated by claim ids. Add the HTML validity and internal-link checks as deterministic gates; they are cheap and missing.
7. **Fine-tuning is out of scope.** Build the dataset export so the record exists. A fine-tune is not worth running before roughly two hundred human-scored pages.

## Access, branch, and safety rules

- **No access to the prod server.** All work happens in your VM against the GitHub clone. Media fixtures are not in git; use `tenants/peak-saunas/fixtures/*.transcript.txt` and `founder-warranty-demo.txt`, and the fake client used by the test suite, for all generation unless a key is provided.
- **Branch `kimi/long-run` only.** Never push to `master` or `main`. Caleb reviews on GitHub; merges happen on the prod server, which then mirrors back. If branch protection on master and main is not yet enabled, ask Caleb to enable it before your first push.
- **Keys.** Never ask for Peak's production key. If real generations are wanted, Caleb issues a separate key with a monthly cap for this run. Every real run must use the harness budget (tokens, calls, wall clock) and the daily cap you add in phase 3. Never call Shopify or Slack; both fail closed without credentials, keep them absent.
- **Never modify** `tenants/peak-saunas/claims/verified.json`, `vocab.yaml`, or `authors.yaml`. Propose additions in `claims/pending.json` with a source; Caleb approves.
- **One commit per loop iteration.** Each commit names the finding id or phase, includes tests, and leaves the full suite green and ruff clean. A phase is done only when its pre-registered criteria pass.

## Phase plan

**Phase 0, bootstrap.** Clone, install, `pytest -q` green, `ruff check` clean, `harness run --tenant peak-saunas tenants/peak-saunas/fixtures/founder-warranty-demo.txt` with the fake client produces three pages. Report the counts.

**Phase 1, baseline.** Read the six documents above. Write `docs/KIMI-BASELINE.md`: system map in your words, the open review findings you will take (by id), the borrowings from the landscape report you will apply, and done criteria for every phase. Run the test suite's dry-run fixtures and store the resulting page.json files under `evals/baseline/` as the regression reference.

**Phase 2, deterministic checks.** HTML validity via an HTML5 parser (unclosed tags, nesting), internal-link count and target validity, image allowlist compliance (only assets from the run's manifest), JSON-LD parse and type check per cartridge, plus a discrimination smoke test per gate: a known-good page passes, a planted bad page fails. Wire into the existing page gate so the repair loop handles failures. Tests.

**Phase 3, block registry.** `harness/blocks/` with `registry.json` in the shadcn registry-item shape (name, type, category, files, dependencies, meta: license, tenant_neutral, mobile_rules, motion, screenshot). Twenty to thirty static blocks: hero variants, three-stat proof row, feature grid, numbered reason, testimonial (renders only verified quotes), FAQ, comparison table (schema only, data from claims), sticky CTA, closing offer, financing line, trust strip. Cartridge templates compose blocks; the writer chooses a block variant per section by id, recorded in page.json so scores attach to blocks. Block gate as in correction 4. Seed patterns from the 39 screenshots in `docs/design-notes-batch50.md`. Add the daily spend cap here.

**Phase 4, structural findings.** Apply the open behavior-preserving findings from the review: the cli.py split into command modules, the single page walker, config precedence with a test, and any Low items you can close cleanly. One commit each, finding id in the message, tests, real-run parity proven with the fake client by byte-comparing page.json against `evals/baseline/`.

**Phase 5, eval export and report.** `harness dataset export --tenant <t>` writes JSONL: ad_brief, facts_pack summary, cartridge and block versions, page.json, gate history, deterministic check results, human scores from `evals/scores.jsonl`. `harness eval report` aggregates scores by cartridge, block, angle, and reviewer. No fine-tune.

**Phase 6, comparison cartridge draft.** From the wireframe: verdict box, sourced comparison table, deep dives on wins, honest competitor strengths, FAQ, one CTA. Schema requires a claim id on every table cell; competitor rows come from `claims/competitors/*.json` (new, pending Caleb's sources). Ship the cartridge with the fake client passing; no real run against a named competitor until Caleb approves the competitor claims.

**Phase 7, runbook.** `docs/PROMPTING.md`: master prompt (objective, repo map, artifact discipline, evidence gates, stop conditions, budget guards), per-phase loop prompts, the checkpoint report format, and the human-review triggers (any change to a gate, a cartridge rule, a tenant file, or spend).

## Checkpoint report format (every phase end)

Commit hashes; test count before and after; ruff status; findings applied by id; what you could not do and why; spend if any real runs; the next phase's done criteria restated.
