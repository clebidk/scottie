# harness

Turns one ad (video, still, or text) into publishable landing pages, each in a
different style, matched to the ad's angle, with every claim traceable to a
verified source.

The engine is company-agnostic. Everything about a company -- its claims, its
brand, its authors, its forbidden words, its theme -- lives in
`tenants/<name>/`. `harness/` never reads a company value from code.

## Five-minute start

```
python3 -m venv .venv && .venv/bin/pip install -e .

# Which company a command is for: --tenant > HARNESS_TENANT > tenants/default.txt
.venv/bin/harness tenant list
.venv/bin/harness run tenants/peak-saunas/fixtures/hidden-costs-v2.mov --tenant peak-saunas
```

That writes one folder per run under `tenants/<tenant>/out/<run-id>/`:
`ad_brief.json`, `facts_pack.json`, `REVIEW.md`, and `<cartridge>/index.html` +
`page.json` per cartridge. Read `REVIEW.md` first -- it says which ad claims
matched, which were dropped, every source used, and what the run cost.

Exit codes: `0` done, `1` bad usage or a refused operation, `2` a claims-gate
STOP (see `unmatched_claims.json`), `3` a budget cap, `4` the tenant is not
configured yet. A STOP or a budget cap never leaves a partial page. They are
defined once in `harness/exits.py` and printed by `harness --help`; an argparse
usage error is `1` too, so `2` always means a gate stopped the run.

## Commands

```
harness run <input> [--cartridges a,b] [--seed N] [--product <slug>] [--batch] --tenant <t>
harness ingest <input> --tenant <t>          # ad_brief.json only, for debugging
harness review <run-dir> --tenant <t>        # self-contained review.html, images inlined
harness shopify-body <run-dir>/<cartridge>   # page body for a storefront paste
harness score <run-dir> --angle N --brand N --claims N --publish N --tenant <t>
harness claims add "..." --category spec --source https://... --tenant <t>
harness claims list --tenant <t>
harness tenant init <slug> | harness tenant list
harness workflow run ad-to-pages --input <input> --tenant <t>
harness workflow list
harness approve <run-dir> --by <email> [--pages a,b] [--note ...]   # cycle 20
harness reject <run-dir> --by <email> --note ...                    # cycle 20
harness packet <run-dir> --stamp ship|redo|kill --by <email>        # cycle 20
harness publish <run-dir> --page <cartridge> [--live] [--dry-run]   # cycle 20
harness digest needs-review --tenant <t> [--days 3]                 # cycle 20
harness doctor --tenant <t> [--offline]                             # cycle 22
```

`harness doctor` answers "can this tenant run?" in one table: files, a validated
`tenant.yaml`, which credentials are set (by NAME -- never a value), whether the
configured models are reachable, whether whisper/ffmpeg are installed, and whether
a run's directories are writable. Exit 1 if anything that blocks a run failed.

Every run now ends in `needs_review`, not just `REVIEW.md` -- see
`docs/PUBLISHING.md` for the full state machine, approval, packet-stamp gate,
and publish flow.

`harness workflow run ad-to-pages` produces exactly what `harness run` produces:
both call the same stage functions, and the YAML only owns the order.

`adv` still works as an alias for one more release; it prints a deprecation line
and runs the same CLI.

## Layout

```
harness/        the engine: ingest, ground, claims gate, writer, renderer,
                budget, log, CLI, doctor. Tenant-neutral. sources/ holds the
                input adapters (Drive, product feed, reviews, knowledge base);
                publishers/ holds the output adapters (Shopify, export).
                exits.py and errors.py own the exit codes and the one
                user-facing error path; textutil.py holds the helpers more
                than one module needs.
cartridges/     page types: article, product-page, longform, listicle. Each is
                cartridge.md (voice/structure), schema.json (page.json shape),
                template.html (Jinja), rubric.md. No company's words -- a
                cartridge says {{ tenant.name }} where a company belongs.
agents/         role briefs, one per pipeline job, as markdown with front-matter.
workflows/      named pipelines as YAML. `stage:` steps are executed by the
                runner; `action:` steps are specification only.
crons/          systemd USER unit templates + install.sh. Nothing is enabled.
evals/          the generic 4-axis score sheet. Scores land per tenant.
tenants/        one directory per company (see tenants/_template/README.md).
docs/           harness-level docs. Tenant-specific notes live with the tenant.
tests/          pytest. No network calls anywhere; every model client is a fake.
```

## Adding a company

```
harness tenant init acme-co
```

Then work through `tenants/acme-co/README.md`. `docs/TENANT-ONBOARDING.md` walks
the same list with the reasons behind each step. Until the claims store is
filled in, a run for that tenant exits 4 with `tenant not configured: missing
...` rather than producing anything.

## Where a value lives

| To change | Edit |
|---|---|
| A forbidden word, or a fixed warranty/financing sentence | `tenants/<t>/vocab.yaml` |
| The company name, site, theme, review source, CTA default | `tenants/<t>/tenant.yaml` |
| Who signs and who reviews a page | `tenants/<t>/authors.yaml` |
| What a page may claim | `tenants/<t>/claims/verified.json` |
| Financing lender, review source, overclaim policy | `tenants/<t>/claims/config.json` |
| A page type's structure or voice | `cartridges/<name>/cartridge.md`, `schema.json` |
| One company's twist on a page type | `tenants/<t>/cartridge-overrides/<name>/cartridge.md` |

## Tests

```
make test
```

Every test injects a fake model client (`tests/conftest.py`) -- no network, no
whisper, no ffmpeg. Since Cycle 22 that is enforced rather than assumed: an
autouse fixture blocks socket connections, so a test that forgets to inject a
fake fails instead of quietly reaching a real storefront. The suite also
enforces that `harness/` and `cartridges/` contain no company's words, that the
tenant skeleton refuses to run until it is filled in, and that the workflow
runner reproduces `harness run`.

`pytest -m "not slow"` skips the two image-re-encoding tests. `ruff check` is
clean; its configuration and the reason for each excluded rule are in
`pyproject.toml`.

## Documents

- `docs/ARCHITECTURE.md` -- the ladder, the stages, the gates, the budgets.
- `docs/PUBLISHING.md` -- run states, approval, the packet stamp, publishing,
  and reviewer notifications (cycle 20).
- `docs/TENANT-ONBOARDING.md` -- standing up a new company.
- `docs/SPEC.md` -- the contract this harness is built against.
- `docs/HARNESS-MAP.md` -- what exists, what is stubbed, what not to rewrite.
- `docs/GENERATOR.md` -- how a page type is built end to end.
- `docs/IMAGE-MAP.md`, `docs/REFINE-NOTES.md`, `docs/FIXLOG.md` -- assets,
  refinement notes, and the per-cycle fix record.
- `docs/RESTRUCTURE-2026-09-10.md` -- what this restructure moved, and why.
