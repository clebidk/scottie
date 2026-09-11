# Architecture

## What it is

This repo turns one ad (video, still image, or text) into publishable landing
pages, each in a different style ("cartridge"), matched to the ad's angle,
with every claim traceable to a verified source. It is split into a
company-agnostic engine (`harness/`) and per-company data (`tenants/<name>/`).
The engine never reads a company-specific value from code -- every string that
names the company, an author, a URL, a lender, a review source, a theme rule,
or a forbidden word is resolved at runtime through `harness/tenant.py` from
files under `tenants/<name>/`. `tenants/peak-saunas/` is the first tenant, and
the only one filled in today; `tenants/_template/` is the skeleton a new
tenant is copied from.

## The ladder

`docs/SPEC.md` section 2 lays out four rungs:

| Level | What it is | Status |
|---|---|---|
| V0 | System prompt only (a Claude Project) | Rejected -- not enough control. |
| V1 static | Fixed orchestration: workers, tools, gates, renderer | **This is what exists.** |
| V1.5 loop | Generate -> grade -> revise inside a budget | Not built. |
| V2 self-improving | Cartridges versioned and scored; a cartridge-smith proposes edits from review data | Not built. |

Everything in this repo today is V1 static: one writer call per cartridge,
then a deterministic gate-repair loop (see Gates below) -- not a
generate/grade/revise loop, and nothing scores or rewrites a cartridge itself.
`docs/SPEC.md` names a `grader` and a `cartridge_smith` worker; neither exists
in `agents/` or in code yet -- they are V1.5/V2 specifications. `agents/` today
covers the stages that do run, plus three support roles (research,
design-audit, drive-index) that a person or a subagent performs by hand.
Rule, stated plainly in `docs/SPEC.md`: no self-improvement layer turns on
before a scored corpus exists. `harness score` (see `evals/rubric.md`) is how
that corpus gets built, one human-scored run at a time, before V2 is allowed
to touch a cartridge on its own.

## Pipeline

```
ad (video / still / text)
        |
        v
   [ingest] ---------------------------> ad_brief.json
        |
        v
   [ground] ---------------------------> facts_pack.json
        |
        v
 [claims gate: gate_ad_claims] --STOP--> unmatched_claims.json  (exit 2)
        |  PASS                          no page written
        v
 [writer, per cartridge] -------------> page.json
        |
        v
 [gate-repair loop: deterministic pass, then model repair, up to 2 retries]
        |  PASS                    FAIL after retries --STOP--> (exit 2)
        v
   [render] ---------------------------> index.html
        |
        v
   [REVIEW.md]
```

## Stages

Stage order and behavior live in `harness/pipeline.py` (`STAGES`,
`DEFAULT_STAGES`). `harness run` and `harness workflow run ad-to-pages` both
call `pipeline.execute` over this same registry, so the two commands cannot
drift apart.

| Stage | Owning module | Produces |
|---|---|---|
| `prepare_run` | `harness/pipeline.py`, `harness/runstate.py` | `run_id`, `run_dir`, the run log, the seed/rng, the selected cartridges, `claims_config`, the facts source, plus `state.json` and `packet.json` |
| `refresh_prices` | `harness/prices.py`, `harness/pdp_claims.py` | live product prices merged into `state.merged_products`, page-derived PDP claims, both cached under `tenants/<t>/runs/` |
| `ingest` | `harness/ingest.py` | `ad_brief.json` |
| `ground` | `harness/ground.py`, `harness/sources/judgeme.py` | `facts_pack.json` (the chosen product, plus its claim universe) |
| `gate_ad_claims` | `harness/claims.py`, `harness/semantic_match.py` | matched/unmatched ad claims, or a STOP |
| `write_pages` | `harness/write.py`, `harness/repair.py` (`write_and_gate_page`) | one `page.json` per selected cartridge |
| `render_pages` | `harness/render.py` | one `index.html` per cartridge (byline, disclosure, sources, JSON-LD injected by the renderer, never by the model) |
| `review_notify` | `harness/runstate.py`, `harness/notify.py` | `state.json` advanced to `needs_review`; the tenant's reviewers notified (both channels optional, both fail closed) |
| `write_review` | `harness/review_md.py` (`write_review_md`) | `REVIEW.md` |

## Gates

Four things end a run early, each with its own exit code (`harness/cli.py`
`main`, `harness/pipeline.py` `execute`):

- **Exit 1** -- bad usage, or a refused operation: an argparse usage error, a
  `--cartridges` naming a cartridge that does not exist (`UnknownCartridge`), an
  unknown workflow or `--product`, or a publish refused by the approval gate.
  The codes themselves live in `harness/exits.py` and are printed by
  `harness --help`; `main` turns anything from `harness/errors.py`'s hierarchy
  into one line and the matching code, never a traceback.
- **Exit 2** -- claims gate STOP (`ClaimsGateFailure`). This fires in two
  places: `gate_ad_claims`, when an ad claim cannot be matched (or is an
  overclaim on a locked topic) against `claims/verified.json`'s universe; and
  inside `write_and_gate_page`'s per-page gate-repair loop, when a cartridge's
  `page.json` still fails a gate check after every repair attempt. Either way,
  `unmatched_claims.json` is written under the run directory and no partial
  page is ever written -- `harness/claims.py`'s module docstring spells out
  exactly what the deterministic gate checks: ad-claim overlap and locked-topic
  overclaims (a), claim_id references and trigger-word/number sourcing (b),
  absolute banned terms and unapproved lender names (c), first-person
  attribution (d), rendered visible-text bans (e), a per-cartridge minimum of
  product-benefit claim_ids (f), longform's proof_stats (g), and the one-CTA
  rule (h).
- **Exit 3** -- budget cap exceeded (`BudgetExceeded`, see Budgets below).
- **Exit 4** -- tenant not configured (`TenantNotConfigured`), raised before
  any stage runs.

A gate STOP never writes a partial page: `write_and_gate_page` only returns a
page once it has fully passed the gate, and `render_pages`/`write_review`
never run on a cartridge that raised.

Inside the per-page repair loop (`harness/cli.py`
`write_and_gate_page`/`apply_deterministic_fixes`), a **deterministic
pre-repair pass** runs before any model call: it fixes a forbidden hype word,
a leaked claim id, a safe trigger-word synonym, an incidental numeral, or a
warranty-wording mismatch with plain text substitution, then re-runs the gate.
Only failures that survive that pass ever reach a real repair call. The
repair loop is capped at `MAX_REPAIR_ATTEMPTS = 2` real model calls beyond the
initial write (3 attempts total per cartridge); exhausting the cap raises
`ClaimsGateFailure`.

## Budgets

Per run, from `harness/budget.py`'s `Budget`:

- wall clock: **300 s**
- tokens: **220,000**
- Claude calls: **14**

`Budget.check()` is called before each stage and after every model call, and
`write_and_gate_page` also skips a repair attempt early (logging why) once the
remaining budget can no longer afford the average cost of a call on that
cartridge so far. Hitting any cap raises `BudgetExceeded`, which propagates
out of `pipeline.execute` as exit 3 -- the run never returns a partial page as
done; the budget summary and `REVIEW.md` still get written so an operator can
see what was spent.

## Tenant resolution

`harness/tenant.py` `resolve_tenant_name`, in order:

1. the `--tenant` CLI flag
2. the `HARNESS_TENANT` environment variable
3. `tenants/default.txt` (currently `peak-saunas`)

A `Tenant` reads `tenant.yaml`, `authors.yaml`, and `vocab.yaml` at
construction, plus `claims/config.json` for `claims_config` (which always wins
over `tenant.yaml`'s overlapping keys). The engine reaches tenant data only
through `Tenant` properties: `claims_dir`, `brand_dir`, `fixtures_dir`,
`env_path`, `out_dir`, `runs_dir`, `inbox_dir`, `evals_path`, `docs_dir`,
`exemplars_dir(cartridge)`, `cartridge_overrides(cartridge)`. A tenant
directory that exists but is missing `tenant.yaml`, `claims/verified.json` (no
approved claims), or `claims/products.json` (no products) raises
`TenantNotConfigured` (exit 4) before any stage runs.

Cartridges are tenant-neutral files under `cartridges/`, written with
`{{ tenant.x }}` / `{{ authors.x.y }}` / `{{ vocab.x }}` placeholders.
`Tenant.render()` substitutes them at load time -- `write_and_gate_page` calls
it on `cartridge.md` and `schema.json` before either is used, so cartridge
files never hardcode a company name.

## Layout map

```
harness/       the engine: pipeline, tenant resolution, ingest, grounding,
               claims gate, writer, renderer, budget, vocab
cartridges/    tenant-neutral page types (article, listicle, longform,
               product-page): cartridge.md, rubric.md, schema.json, template.html
agents/        Markdown specs for each worker (ingest, grounder, writer,
               gate-repair, reviewer, design-audit, drive-index, research)
workflows/     named pipelines as YAML; only steps keyed `stage:` execute
crons/         systemd --user unit templates + install.sh, no publishing
evals/         evals/rubric.md -- the four-axis scoring rubric for `harness score`
tenants/       one directory per company; `_template/` is the skeleton,
               `peak-saunas/` is the filled-in first tenant
docs/          specs and engineering notes (this file, SPEC.md, onboarding)
tests/         pytest suite covering claims, render, write, budget, tenant, ...
```

## Where to change what

| I want to... | Edit |
|---|---|
| Ban or allow a word, phrase, or lender name | the tenant's `vocab.yaml` |
| Change a page's structure or required fields | `cartridges/<name>/cartridge.md` + `schema.json` |
| Change a page's HTML rendering | `cartridges/<name>/template.html`, `harness/render.py` |
| Add a company | `harness tenant init <slug>`, then `tenants/<slug>/` (see `docs/TENANT-ONBOARDING.md`) |
| Change which claims a page may cite | the tenant's `claims/verified.json`, `claims/products.json` |
| Change a run's budget caps | `harness/budget.py` |
| Change stage order or add a stage | `harness/pipeline.py` (`STAGES`, `DEFAULT_STAGES`) and, if it should run under `harness workflow run`, the matching `workflows/*.yaml` |
| Change which tenant runs by default | `tenants/default.txt` |
| Change the repair loop's retry count or fixes | `harness/repair.py` (`MAX_REPAIR_ATTEMPTS`, `apply_deterministic_fixes`) |
